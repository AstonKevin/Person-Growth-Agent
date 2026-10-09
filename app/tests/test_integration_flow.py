# -*- coding: utf-8 -*-
"""跨两个 Agent 的整合测试：目标拆解 → 执行跟踪 → （落后时）动态调整。
真实调 Ollama + MySQL，需二者都启动。跑完自动清理。
跑：python -m pytest app/tests/test_integration_flow.py -v -m integration
"""
import json
import uuid
from datetime import date
import pytest
from langgraph.types import Command
from app.agents.goal_breakdown import graph as goal_graph
from app.agents.tracking_agent import graph as tracking_graph
from app.database import SessionLocal
from app.models.plan import Plan
from app.models.checkin import CheckIn

pytestmark = pytest.mark.integration

GOAL_INPUT = {
    "goal": "", "messages": [], "weekly_plans": [], "daily_plans": [],
    "is_valid": False, "validation_feedback": "",
    "retry_count": 0, "final_plan": {}, "struct_goal": None,
    "saved_plan_id": None, "save_confirmed": False, "save_error": "",
}


def _make_plan(goal_text):
    """跑目标拆解，返回新 plan id"""
    inp = dict(GOAL_INPUT)
    inp["goal"] = goal_text
    r = goal_graph.invoke(inp)
    assert r["save_confirmed"], "测试前置：目标拆解应成功"
    return r["saved_plan_id"]


def _day1_tasks(plan_id):
    """读新计划第一天的任务名"""
    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == plan_id).first()
        daily = json.loads(p.daily_plan)
        return [d["task"] for d in daily if d.get("day") == 1]
    finally:
        db.close()


def _cleanup(plan_id):
    """删除该测试计划的打卡和计划本身"""
    db = SessionLocal()
    try:
        db.query(CheckIn).filter(CheckIn.plan_id == plan_id).delete(synchronize_session=False)
        db.query(Plan).filter(Plan.id == plan_id).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()


def test_plan_then_checkin_ok():
    """拆解 → 第一天全部完成 → 一次跑完、打卡落库"""
    pid = _make_plan("2个月背完3000个英语单词")
    today = date.today().isoformat()       # 新计划今天创建，第一天
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    try:
        tasks = _day1_tasks(pid)
        items = [{"task": t, "completed": True, "note": "测试完成"} for t in tasks]
        r = tracking_graph.invoke(
            {"plan_id": pid, "today": today, "raw_checkin": "", "checkin_items": items},
            config,
        )
        assert r["saved"] is True
        assert r["completion_rate"] == 1.0

        # checkin 真的写了一条
        db = SessionLocal()
        try:
            assert db.query(CheckIn).filter(CheckIn.plan_id == pid).count() == 1
        finally:
            db.close()
    finally:
        _cleanup(pid)


def test_plan_then_adjust_with_confirmation():
    """拆解 → 第一天全没完成 → 暂停 → 确认调整 → 跑通并落库"""
    pid = _make_plan("2个月背完3000个英语单词")
    today = date.today().isoformat()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    try:
        tasks = _day1_tasks(pid)
        items = [{"task": t, "completed": False, "note": "没做"} for t in tasks]
        r1 = tracking_graph.invoke(
            {"plan_id": pid, "today": today, "raw_checkin": "", "checkin_items": items},
            config,
        )
        # 第一次应触发中断
        assert r1.get("__interrupt__"), "0% 应触发调整中断"

        # 确认调整，第二次 invoke
        r2 = tracking_graph.invoke(Command(resume=True), config)
        assert r2["saved"] is True
        assert "计划" in r2["feedback"]
    finally:
        _cleanup(pid)

def test_outdoor_bad_weather_switches_to_indoor(monkeypatch):
    """户外计划 + mock 差天气 → 打卡前暂停 → 确认 → day1 改成室内并落库"""
    from app.agents import tracking_agent as ta
    pid = _make_plan("1个月养成晨跑习惯")
    today = date.today().isoformat()
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}

    # 把 day1 任务改成户外任务，保证命中天气（拆解产物里 day1 可能是准备工作）
    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == pid).first()
        daily = json.loads(p.daily_plan)
        for d in daily:
            if d.get("day") == 1:
                d["task"] = "户外晨跑30分钟"
                d.pop("category", None)
        p.daily_plan = json.dumps(daily, ensure_ascii=False)
        db.commit()
    finally:
        db.close()

    # mock 差天气：降水概率 90%
    def fake_weather(lat, lon, d):
        return {"date": d, "temp_max": 18, "temp_min": 12, "precip_prob": 90}
    monkeypatch.setattr(ta, "get_weather", fake_weather)

    try:
        r1 = tracking_graph.invoke(
            {"plan_id": pid, "today": today, "raw_checkin": "",
             "checkin_items": [{"task": "户外晨跑30分钟", "completed": True, "note": "完成"}]},
            config,
        )
        assert r1.get("__interrupt__"), "差天气应在打卡前暂停"

        r2 = tracking_graph.invoke(Command(resume=True), config)
        assert r2["saved"] is True

        # day1 已改成室内
        db = SessionLocal()
        try:
            p = db.query(Plan).filter(Plan.id == pid).first()
            day1 = [d for d in json.loads(p.daily_plan) if d.get("day") == 1][0]
            assert day1["category"] == "室内"
        finally:
            db.close()
    finally:
        _cleanup(pid)
