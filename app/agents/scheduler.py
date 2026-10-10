# -*- coding: utf-8 -*-
"""第三个 Agent：调度调整 Agent。
结合天气与执行偏差，生成调整建议，经用户确认后写回计划；
时段推荐与日历(.ics)导出由 calendar 工具模块承担。
"""
import json
from typing import TypedDict, Optional
from pydantic import ValidationError
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import interrupt
from langchain_core.messages import HumanMessage
from app.database import SessionLocal
from app.models.plan import Plan
from .common import make_llm, parse_json
from .weather import get_weather, CITY_COORDS
from .tracking_agent import PlanChange, apply_changes
from .calendar import build_plan_schedule
from app.core.resilience import with_retry
import requests

class SchedulerState(TypedDict):
    # 输入
    plan_id: int
    today: str
    city: str
    mode: str                                   # "schedule" / "adjust"
    current_rate: Optional[float]
    checkin_summary: Optional[str]
    # 上下文
    plan_goal: str
    daily_plan: list[dict]
    weather: Optional[dict]
    # 产出
    events: list[dict]
    ics_text: str
    suggestion: str
    changes: list[dict]
    user_confirmed: Optional[bool]
    saved: bool

llm = make_llm(temperature=0.2)

SCHEDULER_ADJUST_PROMPT = """你是一个调度调整顾问。请结合天气和执行情况，重新安排任务。

目标：{goal}
今天天气：{weather}
今日完成率：{rate}
打卡情况：{checkin_summary}

原每日任务：
{daily_tasks}

请选择操作。action 字段的值必须是以下四个英文词之一（禁止中文）：
- "postpone"：延期，必须再给 to_day，且 to_day 是纯整数（如 2，不能写"第2天"）
- "reduce"：减量，必须给 new_content
- "lower_intensity"：降强度，必须给 new_content
- "switch_type"：换类型，必须给 new_type 和 new_content

严格按下面格式输出（字段名和值都用英文，引号不能少；suggestion 可用中文）：
{{
  "suggestion": "1-2句话",
  "changes": [
    {{"task": "任务原文", "action": "postpone", "to_day": 2}},
    {{"task": "任务原文", "action": "switch_type", "new_type": "室内", "new_content": "室内任务"}}
  ]
}}

只输出 JSON，不要输出其他文字。天气差时优先 switch_type，完成率低时优先 reduce 或 postpone。"""

def load_context(state: SchedulerState):
    print("\n=== [Scheduler] load_context 读计划 + 天气 ===")
    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == state["plan_id"]).first()
        if not p:
            return {"daily_plan": []}
        goal = p.goal
        daily = json.loads(p.daily_plan)
    finally:
        db.close()

    # 查天气（城市有效才查）：瞬时错误重试，全失败降级为 None（不阻断流程）
    weather = None
    city = state.get("city") or "南京"
    if city in CITY_COORDS:
        lat, lon = CITY_COORDS[city]
        weather = with_retry(
            get_weather, lat, lon, state["today"],
            retries=2, base_delay=0.5,
            retry_on=(requests.RequestException, ConnectionError, TimeoutError),
            fallback=lambda e: (print("天气查询失败:", e), None)[1],
        )

    print(f"目标:{goal}，模式:{state['mode']}，天气:{weather}")
    return {"plan_goal": goal, "daily_plan": daily, "weather": weather}


def make_schedule(state: SchedulerState):
    print("\n=== [Scheduler] make_schedule 排日程 ===")
    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == state["plan_id"]).first()
        events, ics_text = build_plan_schedule(p)
    finally:
        db.close()
    print(f"排出 {len(events)} 个时段")
    return {"events": events, "ics_text": ics_text, "saved": True}

def make_adjustment(state: SchedulerState):
    print("\n=== [Scheduler] make_adjustment 生成调整 ===")
    w = state.get("weather")
    weather_text = "无数据"
    if w:
        weather_text = f"降水{w['precip_prob']}%，{w['temp_min']}~{w['temp_max']}℃"
    daily_text = "\n".join(
        f"- 第{d.get('day')}天：{d.get('task')}"
        for d in state.get("daily_plan", []) if isinstance(d, dict)
    )
    rate_text = f"{state['current_rate']:.0%}" if state.get("current_rate") is not None else "未知"
    resp = llm.invoke([HumanMessage(content=SCHEDULER_ADJUST_PROMPT.format(
        goal=state.get("plan_goal"),
        weather=weather_text,
        rate=rate_text,
        checkin_summary=state.get("checkin_summary", ""),
        daily_tasks=daily_text,
    ))])
    data = parse_json(resp.content)
    if not isinstance(data, dict):
        data = {}
    suggestion = data.get("suggestion", "建议调整计划")
    changes = []
    for c in data.get("changes", []):
        if isinstance(c, dict):
            try:
                changes.append(PlanChange(**c).model_dump())
            except ValidationError:
                continue
    print(f"调整建议：{suggestion}，有效操作 {len(changes)} 条")
    return {"suggestion": suggestion, "changes": changes}

def confirm_adjustment(state: SchedulerState):
    print("\n=== [Scheduler] confirm_adjustment 暂停等确认 ===")
    w = state.get("weather")
    weather_text = ""
    if w:
        weather_text = f"降水{w['precip_prob']}%，{w['temp_min']}~{w['temp_max']}℃"
    payload = {
        "question": "系统建议调整计划，是否确认？",
        "suggestion": state.get("suggestion", ""),
        "weather": weather_text,
    }
    answer = interrupt(payload)
    confirmed = answer in (True, "yes", "是", "确认")
    print(f"用户确认：{confirmed}")
    return {"user_confirmed": confirmed}

def finalize(state: SchedulerState):
    print("\n=== [Scheduler] finalize 应用调整、导出日历 ===")
    if not state.get("user_confirmed"):
        print("用户取消，保持原计划")
        return {"saved": False}

    base = state.get("daily_plan", [])
    changes = state.get("changes", [])
    new_plan = apply_changes(base, changes)

    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == state["plan_id"]).first()
        if p and new_plan:
            p.daily_plan = json.dumps(new_plan, ensure_ascii=False)
            db.commit()
            print(f"调整落库，{len(changes)} 条操作")
            events, ics_text = build_plan_schedule(p)   # 基于新计划重排日程
        else:
            events, ics_text = [], ""
    finally:
        db.close()

    return {"saved": True, "events": events, "ics_text": ics_text}


def route_mode(state: SchedulerState) -> str:
    return state["mode"]


builder = StateGraph(SchedulerState)
builder.add_node("load_context", load_context)
builder.add_node("make_schedule", make_schedule)
builder.add_node("make_adjustment", make_adjustment)
builder.add_node("confirm_adjustment", confirm_adjustment)
builder.add_node("finalize", finalize)

builder.add_edge(START, "load_context")
builder.add_conditional_edges(
    "load_context",
    route_mode,
    {
        "schedule": "make_schedule",
        "adjust": "make_adjustment",
    }
)
builder.add_edge("make_schedule", END)
builder.add_edge("make_adjustment", "confirm_adjustment")
builder.add_edge("confirm_adjustment", "finalize")
builder.add_edge("finalize", END)

scheduler_graph = builder.compile(checkpointer=InMemorySaver())













