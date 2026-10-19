# -*- coding: utf-8 -*-
"""用量记录测试：需要 MySQL（集成），LLM 用假对象，不真实调网络。

跑：python -m pytest app/tests/test_usage.py -v -m integration
验证：成功/失败各产生一条；token/延迟有值；记录失败不影响主流程；聚合正确。
"""
import time

import pytest
from langchain_core.messages import AIMessage

from app.database import SessionLocal
from app.core.security import hash_password
from app.models.user import User
from app.models.usage import UsageLog
from app.agents.common import tracked_chat
from app.crud import usage as usage_crud

pytestmark = pytest.mark.integration


@pytest.fixture
def temp_user():
    """临时用户，跑完清理其用量记录和用户。"""
    uname = f"usage_t_{int(time.time())}"
    db = SessionLocal()
    try:
        u = User(name=uname, password_hash=hash_password("123456"))
        db.add(u)
        db.commit()
        db.refresh(u)
        uid = u.id
    finally:
        db.close()
    try:
        yield uid
    finally:
        db = SessionLocal()
        try:
            db.query(UsageLog).filter(UsageLog.user_id == uid).delete()
            db.query(User).filter(User.id == uid).delete()
            db.commit()
        finally:
            db.close()


class _OK:
    def invoke(self, msgs):
        return AIMessage(
            content="ok",
            usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
        )


class _Bad:
    def invoke(self, msgs):
        raise RuntimeError("boom")


def test_success_recorded(temp_user):
    tracked_chat(_OK(), [AIMessage(content="x")], user_id=temp_user, model="fake-m", provider="openai")
    db = SessionLocal()
    try:
        rows = db.query(UsageLog).filter(UsageLog.user_id == temp_user).all()
        assert len(rows) == 1
        r = rows[0]
        assert r.status == "success"
        assert r.prompt_tokens == 10 and r.completion_tokens == 5
        assert r.latency_ms is not None and r.latency_ms >= 0
    finally:
        db.close()


def test_error_recorded_and_reraised(temp_user):
    with pytest.raises(RuntimeError):
        tracked_chat(_Bad(), [AIMessage(content="x")], user_id=temp_user, model="fake-m")
    db = SessionLocal()
    try:
        r = db.query(UsageLog).filter(UsageLog.user_id == temp_user).first()
        assert r.status == "error" and r.error_code == "RuntimeError"
    finally:
        db.close()


def test_logging_failure_does_not_break(temp_user, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("db down")
    monkeypatch.setattr(usage_crud, "add_usage", boom)
    out = tracked_chat(_OK(), [AIMessage(content="x")], user_id=temp_user, model="fake-m")
    assert out.content == "ok"


def test_aggregation(temp_user):
    tracked_chat(_OK(), [AIMessage(content="x")], user_id=temp_user, model="fake-m")
    with pytest.raises(RuntimeError):
        tracked_chat(_Bad(), [AIMessage(content="x")], user_id=temp_user, model="fake-m")

    db = SessionLocal()
    try:
        totals = usage_crud.total_counts(db)
        assert totals["total"] >= 2
        assert totals["success"] >= 1 and totals["error"] >= 1
        assert totals["prompt_tokens"] >= 10
        models = usage_crud.counts_by_model(db)
        assert any(m["model"] == "fake-m" for m in models)
        days = usage_crud.counts_by_day(db, days=7)
        assert days and all(d["total"] >= 1 for d in days)
    finally:
        db.close()
