# -*- coding: utf-8 -*-
"""规则引擎测试：不同情境命中不同规则；未命中时应回落 LLM"""
from app.agents.rules_engine import RuleEngine

engine = RuleEngine()


def test_bad_weather_outdoor_switch():
    facts = {
        "weather_warning": True,
        "task_category": "outdoor",
        "deviation": "on_track",
        "trend": "flat",
        "task": "晨跑5公里",
    }
    change = engine.apply(facts)
    assert change["action"] == "switch_type"
    assert change["new_type"] == "室内"
    assert "晨跑5公里" in change["new_content"]


def test_severe_behind_reduce():
    facts = {
        "weather_warning": False,
        "task_category": "indoor",
        "deviation": "significantly_behind",
        "trend": "down",
        "task": "背100个单词",
    }
    change = engine.apply(facts)
    assert change["action"] == "reduce"
    assert "背100个单词" in change["new_content"]


def test_slightly_behind_down_lower_intensity():
    facts = {
        "weather_warning": False,
        "task_category": "indoor",
        "deviation": "slightly_behind",
        "trend": "down",
        "task": "深度专注2小时",
    }
    change = engine.apply(facts)
    assert change["action"] == "lower_intensity"


def test_consecutive_miss_postpone():
    facts = {
        "consecutive_miss": True,
        "task": "完成第3章练习",
        "current_day": 3,
    }
    change = engine.apply(facts)
    assert change["action"] == "postpone"
    assert change["to_day"] == 5   # 第3天 + 2天


def test_priority_order_weather_over_behind():
    """同时满足天气差和严重落后，应命中优先级更高的天气规则"""
    facts = {
        "weather_warning": True,
        "task_category": "outdoor",
        "deviation": "significantly_behind",
        "trend": "down",
        "task": "晨跑5公里",
    }
    assert engine.apply(facts)["action"] == "switch_type"


def test_no_match_returns_none():
    """正常且天气好、无连续未完成，规则不命中 → 交给 LLM"""
    facts = {
        "weather_warning": False,
        "task_category": "indoor",
        "deviation": "on_track",
        "trend": "flat",
        "consecutive_miss": False,
        "task": "复习笔记",
    }
    assert engine.apply(facts) is None
    assert engine.use_llm_fallback() is True


def test_threshold_classification():
    assert engine.classify_deviation(0.9) == "on_track"
    assert engine.classify_deviation(0.5) == "slightly_behind"
    assert engine.classify_deviation(0.2) == "significantly_behind"
    assert engine.is_weather_unfriendly(
        {"precip_prob": 80, "temp_max": 25, "temp_min": 18}
    ) is True
    assert engine.is_weather_unfriendly(
        {"precip_prob": 10, "temp_max": 25, "temp_min": 18}
    ) is False
