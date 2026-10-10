# -*- coding: utf-8 -*-
"""日历工具：任务类型识别、最佳时段推荐、iCalendar(.ics) 生成、排程。

规则（时段、分类关键词、默认类型）来自项目根 rules.yaml。
只负责"时间安排"，不掺和 Scheduler 的编排与 LLM 决策。
"""
import json
import uuid
from datetime import timedelta

from app.core.rules import get_rules
from .weather import is_outdoor_task

_rules = get_rules()

# 任务类型 -> 推荐时段（YAML 里 [start, end]）
CATEGORY_SLOTS = {cat: tuple(t) for cat, t in _rules["slots"].items()}


def classify_task(task_text: str) -> str:
    """按规则库顺序判断任务类型；命中锻炼类时再用户外关键词细分。"""
    text = task_text or ""
    for category, keywords in _rules["classify_keywords"].items():
        if any(k in text for k in keywords):
            if category == "锻炼/健身" and is_outdoor_task(text):
                return "户外运动"
            return category
    return _rules["default_category"]


def recommend_slot(task_text: str) -> dict:
    """推荐一个任务的最佳时段。"""
    category = classify_task(task_text)
    start, end = CATEGORY_SLOTS[category]
    return {"category": category, "start": start, "end": end}


def _ics_dt(date_str: str, hhmm: str) -> str:
    """日期+时间转 iCalendar 格式：2026-10-11 + 07:00 -> 20261011T070000"""
    h, m = hhmm.split(":")
    return f"{date_str.replace('-', '')}T{h}{m}00"


def build_ics(events: list[dict]) -> str:
    """把 events 转成标准 iCalendar 文本。
    events: [{"date", "task", "start", "end", "category"}]
    """
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Person Growth Agent//CN//",
        "CALSCALE:GREGORIAN",
    ]
    for ev in events:
        lines += [
            "BEGIN:VEVENT",
            f"UID:{uuid.uuid4()}@person-growth-agent",
            f"DTSTART:{_ics_dt(ev['date'], ev['start'])}",
            f"DTEND:{_ics_dt(ev['date'], ev['end'])}",
            f"SUMMARY:{ev['task']}",
            f"DESCRIPTION:推荐类型：{ev.get('category', '')}",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return "\r\n".join(lines)


def build_plan_schedule(plan) -> tuple[list[dict], str]:
    """给一个 plan 排完整日程：任务按 day 算实际日期、按类型推荐时段。
    返回 (events, ics_text)。
    """
    daily = json.loads(plan.daily_plan)
    start = plan.start_time.date()
    events = []
    for item in daily:
        day = item.get("day")
        event_date = (start + timedelta(days=day - 1)).isoformat()
        slot = recommend_slot(item.get("task", ""))
        events.append({
            "date": event_date,
            "task": item.get("task"),
            "start": slot["start"],
            "end": slot["end"],
            "category": slot["category"],
        })
    return events, build_ics(events)
