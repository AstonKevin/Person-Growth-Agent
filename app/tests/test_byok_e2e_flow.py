# -*- coding: utf-8 -*-
"""BYOK 与管理后台全链路端到端闭环测试。

链路覆盖：
1. 普通用户注册与登录
2. 配置自带 LLM Key（PUT /providers/me）并测试连通性（POST /providers/me/test）
3. 执行一次带有用量记录的 LLM 调用（tracked_chat）
4. 验证 usage_log 审计日志自动落库
5. 管理员登录并查阅 /admin/stats，确认统计指标与用量自增
6. 管理员通过 /admin/users 看到该用户且 has_provider=True
7. 管理员停用该用户，验证该用户即刻被系统拦截 (401)
"""
import time
import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage

from app.main import app
from app.database import SessionLocal
from app.models.user import User
from app.models.provider import UserProvider
from app.models.usage import UsageLog
from app.agents.common import tracked_chat
from app.routers import providers as providers_router

pytestmark = pytest.mark.integration
client = TestClient(app)


class _MockOpenAIChat:
    """模拟 OpenAI client.chat.completions.create"""
    def __init__(self, **kwargs):
        class _Comp:
            def create(self, **kwargs):
                return type("Resp", (), {"id": "mock_ok"})()
        self.chat = type("Chat", (), {"completions": _Comp()})()


class _FakeChatLLM:
    """模拟 LangChain ChatModel"""
    def invoke(self, messages):
        return AIMessage(
            content="这是一次测试拆解输出",
            usage_metadata={"input_tokens": 120, "output_tokens": 80, "total_tokens": 200},
        )


def test_full_byok_and_admin_lifecycle(monkeypatch):
    ts = int(time.time() * 1000)
    uname = f"e2e_user_{ts}"
    admin_name = f"e2e_admin_{ts}"

    # 1. 普通用户注册与登录
    r_reg = client.post("/auth/register", json={"name": uname, "password": "password123"})
    assert r_reg.status_code == 201, r_reg.text
    uid = r_reg.json()["id"]

    r_login = client.post("/auth/login", data={"username": uname, "password": "password123"})
    assert r_login.status_code == 200
    token = r_login.json()["access_token"]
    u_auth = {"Authorization": f"Bearer {token}"}

    # 2. 配置自带 Key 与连通测试
    monkeypatch.setattr(providers_router.openai, "OpenAI", _MockOpenAIChat)
    r_test = client.post(
        "/providers/me/test",
        headers=u_auth,
        json={
            "provider": "openai",
            "base_url": "https://api.test-llm.com",
            "api_key": "sk-test-secret-key",
            "model": "deepseek-chat",
        },
    )
    assert r_test.status_code == 200 and r_test.json()["ok"] is True

    r_put = client.put(
        "/providers/me",
        headers=u_auth,
        json={
            "provider": "openai",
            "base_url": "https://api.test-llm.com",
            "api_key": "sk-test-secret-key",
            "model": "deepseek-chat",
        },
    )
    assert r_put.status_code == 200
    assert "api_key" not in r_put.json()

    # 3. 模拟一次 LLM 调用并生成用量记录
    resp = tracked_chat(
        _FakeChatLLM(),
        [AIMessage(content="拆解目标")],
        user_id=uid,
        model="deepseek-chat",
        provider="openai",
    )
    assert resp.content == "这是一次测试拆解输出"

    # 4. 创建管理员用户并登录
    r_admin_reg = client.post("/auth/register", json={"name": admin_name, "password": "password123"})
    admin_id = r_admin_reg.json()["id"]

    db = SessionLocal()
    try:
        a_user = db.query(User).filter(User.id == admin_id).first()
        a_user.role = "admin"
        db.commit()
    finally:
        db.close()

    r_admin_login = client.post("/auth/login", data={"username": admin_name, "password": "password123"})
    admin_token = r_admin_login.json()["access_token"]
    admin_auth = {"Authorization": f"Bearer {admin_token}"}

    try:
        # 5. 管理员查看 /admin/stats，确认统计到该用户的调用和 token
        r_stats = client.get("/admin/stats", headers=admin_auth)
        assert r_stats.status_code == 200
        stats = r_stats.json()
        assert stats["total_requests"] >= 1
        assert stats["prompt_tokens"] >= 120
        assert stats["completion_tokens"] >= 80

        model_stat = next((m for m in stats["by_model"] if m["model"] == "deepseek-chat"), None)
        assert model_stat is not None
        assert model_stat["count"] >= 1

        # 6. 管理员在用户列表中能看到该用户的 has_provider=True
        r_users = client.get("/admin/users", headers=admin_auth)
        assert r_users.status_code == 200
        user_record = next((u for u in r_users.json() if u["id"] == uid), None)
        assert user_record is not None
        assert user_record["has_provider"] is True

        # 7. 管理员停用该用户，普通用户随后的所有认证请求立即被 401 拦截
        r_patch = client.patch(f"/admin/users/{uid}", headers=admin_auth, json={"is_active": False})
        assert r_patch.status_code == 200 and r_patch.json()["is_active"] is False

        r_blocked = client.get("/users/me", headers=u_auth)
        assert r_blocked.status_code == 401
        assert "停用" in r_blocked.json()["message"]

    finally:
        # 清理测试数据
        clean_db = SessionLocal()
        try:
            clean_db.query(UsageLog).filter(UsageLog.user_id.in_([uid, admin_id])).delete(
                synchronize_session=False
            )
            clean_db.query(UserProvider).filter(
                UserProvider.user_id.in_([uid, admin_id])
            ).delete(synchronize_session=False)
            clean_db.query(User).filter(User.id.in_([uid, admin_id])).delete(
                synchronize_session=False
            )
            clean_db.commit()
        finally:
            clean_db.close()
