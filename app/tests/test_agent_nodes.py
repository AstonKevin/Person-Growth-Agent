# -*- coding: utf-8 -*-
"""Agent 节点的纯函数测试：不调 LLM、不连库，秒级跑完。"""
from app.agents.goal_breakdown import (
    extract_weekly_plans, extract_daily_plans,
    gate_after_parse, reject_goal, WeekPlanItem, DailyPlanItem,
)
from app.agents.tracking_agent import apply_changes
import copy

# apply_changes 用的固定计划
DAILY = [
    {"day": 1, "week": 1, "task": "学习卷腹", "priority": "高", "notes": ""},
    {"day": 4, "week": 1, "task": "测量体脂率基线", "priority": "中", "notes": ""},
]

# ========== extract_weekly_plans ==========
def test_extract_weekly_ok():
    text = '[{"week":1,"focus":"调研","tasks":["a","b"],"milestone":"m"}]'
    plans = extract_weekly_plans(text)
    assert len(plans) == 1
    assert isinstance(plans[0], WeekPlanItem)
    assert plans[0].week == 1

def test_extract_weekly_invalid_json_returns_empty():
    assert extract_weekly_plans("not json") == []

def test_extract_weekly_not_list_returns_empty():
    assert extract_weekly_plans('{"week":1}') == []

def test_extract_weekly_skip_bad_item():
    """坏条目被跳过，好条目保留"""
    text = '[{"week":1,"focus":"a","tasks":[],"milestone":"m"}, {"bad":"item"}]'
    assert len(extract_weekly_plans(text)) == 1

# ========== extract_daily_plans ==========
def test_extract_daily_ok():
    text = '[{"day":1,"week":1,"task":"t","time_estimate":"1h","priority":"高","notes":""}]'
    plans = extract_daily_plans(text)
    assert len(plans) == 1
    assert isinstance(plans[0], DailyPlanItem)

def test_extract_daily_notes_has_default():
    """notes 漏了也不报错，用默认空串"""
    text = '[{"day":1,"week":1,"task":"t","time_estimate":"1h","priority":"高"}]'
    assert extract_daily_plans(text)[0].notes == ""

# ========== gate_after_parse（闸门逻辑）==========
def test_gate_valid_goal_goes_to_plan_weekly():
    assert gate_after_parse({"struct_goal": {"is_real_goal": True}}) == "plan_weekly"

def test_gate_invalid_goal_goes_to_reject():
    assert gate_after_parse({"struct_goal": {"is_real_goal": False}}) == "reject"

def test_gate_none_struct_goes_to_reject():
    """parse_goal 解析失败时 struct_goal=None，也走 reject"""
    assert gate_after_parse({"struct_goal": None}) == "reject"

# ========== reject_goal（无效目标不拆解不落库）==========
def test_reject_returns_no_save_and_clarification():
    state = {"goal": "你好", "struct_goal": {"is_real_goal": False, "clarification": "请说具体目标"}}
    out = reject_goal(state)
    assert out["save_confirmed"] is False
    assert out["saved_plan_id"] is None
    assert out["final_plan"]["weekly"] == []
    assert "请说具体目标" in out["final_plan"]["warning"]

def test_reject_has_default_clarification_when_none():
    out = reject_goal({"goal": "x", "struct_goal": None})
    assert out["final_plan"]["warning"]  # 有默认提示，不为空


# ========== apply_changes（动态调整纯函数）==========
def test_apply_postpone_moves_task():
    changes = [{"task": "测量体脂率基线", "action": "postpone", "to_day": 6, "new_content": None}]
    out = apply_changes(DAILY, changes)
    moved = [d for d in out if d["task"] == "测量体脂率基线"][0]
    assert moved["day"] == 6
    assert moved["week"] == 1

def test_apply_reduce_changes_content():
    changes = [{"task": "学习卷腹", "action": "reduce", "to_day": None, "new_content": "10分钟卷腹"}]
    out = apply_changes(DAILY, changes)
    reduced = [d for d in out if d["day"] == 1][0]
    assert reduced["task"] == "10分钟卷腹"

def test_apply_changes_no_match_skip():
    """找不到匹配任务，计划不变"""
    changes = [{"task": "不存在的任务", "action": "postpone", "to_day": 9, "new_content": None}]
    out = apply_changes(DAILY, changes)
    assert out == DAILY

def test_apply_changes_fuzzy_match():
    """只给部分任务名，靠包含匹配命中"""
    changes = [{"task": "体脂率", "action": "postpone", "to_day": 7, "new_content": None}]
    out = apply_changes(DAILY, changes)
    moved = [d for d in out if "测量体脂率基线" in d["task"]][0]
    assert moved["day"] == 7

def test_apply_changes_does_not_mutate_input():
    """纯函数：原输入不被修改"""
    original = copy.deepcopy(DAILY)
    changes = [{"task": "测量体脂率基线", "action": "postpone", "to_day": 6, "new_content": None}]
    apply_changes(DAILY, changes)
    assert DAILY == original

def test_apply_lower_intensity_changes_content():
    """降强度：任务内容改变"""
    changes = [{"task": "学习卷腹", "action": "lower_intensity", "new_content": "轻松卷腹5分钟"}]
    out = apply_changes(DAILY, changes)
    item = [d for d in out if d["day"] == 1][0]
    assert item["task"] == "轻松卷腹5分钟"

def test_apply_switch_type_changes_content_and_category():
    """换类型：任务内容改变，且记录新类型 category"""
    changes = [{"task": "学习卷腹", "action": "switch_type", "new_type": "室内", "new_content": "平板支撑"}]
    out = apply_changes(DAILY, changes)
    item = [d for d in out if d["day"] == 1][0]
    assert item["task"] == "平板支撑"
    assert item["category"] == "室内"

