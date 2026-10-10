# -*- coding: utf-8 -*-
"""
规则引擎：读取 config/adjustment_rules.yaml
1. 用配置里的阈值做分类（偏差等级 / 是否不宜户外 / 是否连续未完成）
2. 按优先级匹配第一条确定性规则，返回 PlanChange；未命中返回 None，
   上层再决定是否交给 LLM（fallback）。

设计要点：规则引擎只做"确定性、可预测"的判断，不调 LLM；
这样便宜、稳定、不会幻觉，且改规则只动 YAML，不用改代码。
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml

from app.core.rules import get_rules

_RULES_PATH = Path(__file__).parent / "config" / "adjustment_rules.yaml"


class RuleEngine:
    def __init__(self, rules_path: Path | str = _RULES_PATH):
        with open(rules_path, "r", encoding="utf-8") as f:
            self.config = yaml.safe_load(f)
        # 按 priority 升序，数字小的先判；阈值统一由 rules.yaml 提供
        self.rules = sorted(
            self.config.get("rules", []),
            key=lambda r: r.get("priority", 99),
        )

    # ---------- 用阈值做分类（阈值统一来自 rules.yaml，单一事实来源） ----------
    @staticmethod
    def classify_deviation(rate: float) -> str:
        """完成率 → 偏差等级"""
        p = get_rules()["progress"]
        if rate >= p["on_track_above"]:
            return "on_track"
        if rate >= p["slightly_behind_above"]:
            return "slightly_behind"
        return "significantly_behind"

    @staticmethod
    def is_weather_unfriendly(weather: dict) -> bool:
        """天气是否不宜户外（阈值来自 rules.yaml）"""
        t = get_rules()["weather"]["unfriendly"]
        return (
            weather.get("precip_prob", 0) > t["precip_prob_above"]
            or weather.get("temp_max", 20) >= t["temp_max_above"]
            or weather.get("temp_min", 20) <= t["temp_min_below"]
        )

    @staticmethod
    def is_consecutive_miss(consecutive_miss_days: int) -> bool:
        """连续未完成天数是否达到阈值（来自 rules.yaml）"""
        return consecutive_miss_days >= get_rules()["progress"]["consecutive_miss"]

    # ---------- 规则匹配 ----------
    @staticmethod
    def _match(facts: dict, condition: dict) -> bool:
        """条件里的每个字段都要与事实相等才算命中"""
        for key, expected in condition.items():
            if facts.get(key) != expected:
                return False
        return True

    @staticmethod
    def _render_change(rule: dict, facts: dict) -> dict:
        """把命中的规则渲染成 PlanChange 结构（{task} 等占位符替换）"""
        then = rule["then"]
        change: dict[str, Any] = {
            "task": facts.get("task", ""),
            "action": then["action"],
        }

        if "new_content" in then:
            change["new_content"] = then["new_content"].format(**facts)
        if "new_type" in then:
            change["new_type"] = then["new_type"]
        if "delay_days" in then:
            # 延期：当前天数 + 延迟天数
            change["to_day"] = facts.get("current_day", 0) + then["delay_days"]

        return change

    def apply(self, facts: dict) -> Optional[dict]:
        """按优先级返回第一条命中的规则；未命中返回 None"""
        for rule in self.rules:
            if self._match(facts, rule["when"]):
                return self._render_change(rule, facts)
        return None

    def use_llm_fallback(self) -> bool:
        """规则未命中时是否允许交给 LLM"""
        return bool(self.config.get("fallback", {}).get("use_llm", True))


@lru_cache
def get_rule_engine() -> RuleEngine:
    """进程内单例：规则只读一次"""
    return RuleEngine()
