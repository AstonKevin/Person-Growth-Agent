# -*- coding: utf-8 -*-
"""端到端集成测试：真实调 Ollama + MySQL，验证完整图链路。
需 Ollama 和 MySQL 都启动。跑完自动删除测试产生的 plan 记录。
跑：python -m pytest app/tests/test_e2e.py -v -m integration
"""
import pytest
from app.agents.goal_breakdown import graph
from app.database import SessionLocal
from app.models.plan import Plan

pytestmark = pytest.mark.integration


def _invoke(goal: str) -> dict:
    return graph.invoke({
        "goal": goal, "messages": [], "weekly_plans": [], "daily_plans": [],
        "is_valid": False, "validation_feedback": "",
        "retry_count": 0, "final_plan": {}, "struct_goal": None,
        "saved_plan_id": None, "save_confirmed": False, "save_error": "",
    })


def _plan_count() -> int:
    db = SessionLocal()
    try:
        return db.query(Plan).count()
    finally:
        db.close()


def test_e2e_valid_goal_full_flow():
    """真目标：走完全程 → 落库 → 返回 id → 周/日计划非空"""
    before = _plan_count()
    result = _invoke("3个月内拿到暑期实习offer")
    after = _plan_count()

    assert result["save_confirmed"] is True
    assert result["saved_plan_id"] is not None
    assert result["final_plan"]["weekly"], "周计划不应为空"
    assert result["final_plan"]["daily"], "日计划不应为空"
    assert after == before + 1, "真目标应新增一条 plan 记录"

    # 清理测试数据
    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == result["saved_plan_id"]).first()
        if p:
            db.delete(p)
            db.commit()
    finally:
        db.close()


def test_e2e_invalid_goal_rejects_no_save():
    """无效目标：reject → 不落库 → weekly 为空"""
    before = _plan_count()
    result = _invoke("你好")
    after = _plan_count()

    assert result["save_confirmed"] is False
    assert result["saved_plan_id"] is None
    assert result["final_plan"]["weekly"] == []
    assert result["final_plan"]["warning"]   # 有澄清提示
    assert after == before, "无效目标不应写库"
