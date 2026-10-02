import os, json
from typing import TypedDict, Annotated
import operator
from langchain_core.messages import HumanMessage
from langgraph.graph import StateGraph, START, END
from langgraph.graph.message import add_messages
from langchain_ollama import ChatOllama
from pydantic import BaseModel,Field,ValidationError
from datetime import datetime, timedelta
from ..database import SessionLocal
from ..crud.plan import create_plan
from ..schemas.plan import PlanCreate


WEEKLY_PROMPT = """你是一个专业的目标拆解专家。请根据用户的目标，生成一个按周拆解的详细计划。

用户目标：{goal}

要求：
1. 将目标拆解为若干周的阶段性计划，每周有明确的任务和目标。
2. 输出必须是合法的 JSON 数组，每个元素是一个字典，包含以下字段：
   - week: 第几周（整数，从1开始）
   - focus: 本周核心目标（字符串）
   - tasks: 本周需要完成的任务列表（字符串数组）
   - milestone: 本周结束时应达成的里程碑（字符串）
3. 不要输出任何其他文字，只输出 JSON 数组。
4. 计划要具体、可执行、有时间节点。

示例输出格式：
[
  {{
    "week": 1,
    "focus": "明确方向与准备简历",
    "tasks": ["调研目标公司与岗位", "修改简历与作品集", "学习岗位相关技能"],
    "milestone": "完成简历初稿，确定3-5家目标公司"
  }}
]

请开始生成："""

DAILY_PROMPT = """你是一个高效的时间管理助手。请根据用户的总体目标和周计划，生成每日的详细执行计划。

用户目标：{goal}

周计划：
{weekly}

要求：
1. 根据周计划，拆解为每日的具体任务。
2. 输出必须是合法的 JSON 数组，每个元素是一个字典，包含以下字段：
   - day: 第几天（整数，从1开始）
   - week: 属于第几周（整数）
   - task: 当日核心任务（字符串）
   - time_estimate: 预计耗时（字符串，如"2小时"）
   - priority: 优先级（高/中/低）
   - notes: 备注或注意事项（字符串）
3. 不要输出任何其他文字，只输出 JSON 数组。
4. 每日任务要具体、可量化、可执行。

示例输出格式：
[
  {{
    "day": 1,
    "week": 1,
    "task": "调研5家目标公司的岗位要求",
    "time_estimate": "2小时",
    "priority": "高",
    "notes": "重点关注JD中的技能要求"
  }}
]
本次只拆解第 1 周的 7 天，不要输出其他周
请开始生成："""

PARSE_GOAL_PROMPT = """你是目标理解专家。把用户的自然语言目标，解析成结构化信息。
只输出一个 JSON 对象，不要解释、不要 markdown 代码块。
字段：
- title: 一句话目标标题
- domain: 领域（如 求职/学习/健身/技能/理财）
- duration_weeks: 目标持续周数（整数，从描述推断：3个月≈12周，半年≈26周，1个月≈4周）
- success_criteria: 成功标准（字符串列表）
- constraints: 约束/限制（字符串列表，如每天可投入时间、预算）
- is_real_goal: 仅当同时满足以下两点才填 true：
  (1) 表达了一个需要一段时间努力的成长目标；
  (2) 信息足以直接拆解——能明确具体方向或领域，且能推断出合理时间。
  像"变得更厉害""提升自己""越来越好""我要努力"这类没有具体方向、没有时间、
  无法判断具体要做什么的空泛愿望，以及闲聊、提问，一律填 false。
- clarification: is_real_goal 为 false 时，按"缺什么"追问：
  缺方向就问"你想提升哪个具体方面（求职/某项技能/健身/英语等）？"，
  缺时间就问"你希望多久内达成？"，并附完整示例"例如：3个月内拿到暑期实习offer"。
  为 true 时留空字符串。

用户目标：{goal}
"""

class StructuredGoal(BaseModel):
    title: str
    domain: str
    duration_weeks: int = Field(ge=1, le=100)
    success_criteria: list[str]
    constraints: list[str]
    raw_goal: str
    is_real_goal:bool = True
    clarification:str = ""


class WeekPlanItem(BaseModel):
    week: int = Field(ge=1)
    focus: str                       
    tasks: list[str]
    milestone: str

class DailyPlanItem(BaseModel):
    day: int = Field(ge=1)
    week: int = Field(ge=1)
    task: str
    time_estimate: str
    priority: str                   
    notes: str = ""                


def extract_structured_goal(raw_goal: str, text: str) -> StructuredGoal:
    """把 LLM 返回的文本解析成结构化目标；解析/校验失败抛 ValidationError。"""
    data = parse_json(text)
    if not isinstance(data, dict):
        data = {}
    data["raw_goal"] = raw_goal
    return StructuredGoal(**data)


llm = ChatOllama(
    base_url="http://localhost:11434",
    model="qwen3:8b",
    temperature=0.3,
)

class GoalState(TypedDict):
    goal: str
    messages: Annotated[list, add_messages]
    weekly_plans: list
    daily_plans: list
    is_valid: bool
    validation_feedback: str
    retry_count: Annotated[int, operator.add]
    final_plan: dict
    struct_goal: dict | None

    saved_plan_id: int | None
    save_confirmed: bool
    save_error: str


MAX_PARSE_RETRIES = 2   # 解析失败时最多重试 2 次

def parse_goal(state: GoalState):
    print("===[节点]parse_goal理解目标")
    for attempt in range(MAX_PARSE_RETRIES + 1):
        resp = llm.invoke([HumanMessage(content=PARSE_GOAL_PROMPT.format(goal=state["goal"]))])
        try:
            sg = extract_structured_goal(state["goal"], resp.content)
            print(f"解析成功：{sg.title}/{sg.domain}/{sg.duration_weeks}")
            return {"struct_goal": sg.model_dump()}
        except ValidationError as e:
            print(f"第{attempt + 1}次解析失败：{e}")
            if attempt == MAX_PARSE_RETRIES:
                print("重试耗尽，标记为无效目标")
                return {"struct_goal": None}
    return {"struct_goal": None}


def parse_json(text):
    text = text.strip()
    if "```" in text:
        for part in text.split("```"):
            p = part.strip()
            if p.lower().startswith("json"):
                p = p[4:].strip()
            if p.startswith("[") or p.startswith("{"):
                try:
                    return json.loads(p)
                except Exception:
                    pass
    try:
        return json.loads(text)
    except Exception:
        return None

def gate_after_parse(state: GoalState) -> str:
    """parse_goal 之后的闸门：判断要不要继续拆解。"""
    sg = state.get("struct_goal")
    if sg and sg.get("is_real_goal", False):
        return "plan_weekly"
    return "reject"

def reject_goal(state: GoalState):
    """无效目标：只给提示，不拆解、不落库。"""
    print("=== [节点] reject_goal 目标无效 ===")
    sg = state.get("struct_goal") or {}
    tip = sg.get("clarification") or (
        "我没太理解你的目标，请用「我想在X时间内达成Y」的形式描述，"
        "例如：3个月拿到暑期实习offer")
    return {
        "final_plan": {"goal": state["goal"], "weekly": [], "daily": [],
                       "is_valid": False, "warning": tip},
        "save_confirmed": False, "saved_plan_id": None, "save_error": "",
    }


def extract_weekly_plans(text: str) -> list[WeekPlanItem]:
    data = parse_json(text)
    if not isinstance(data, list):
        return []
    result = []
    for w in data:
        if not isinstance(w, dict):
            continue
        try:
            result.append(WeekPlanItem(**w))
        except ValidationError:
            continue          # 单条坏数据跳过，不影响其他
    return result

def extract_daily_plans(text: str) -> list[DailyPlanItem]:
    data = parse_json(text)
    if not isinstance(data, list):
        return []
    result = []
    for d in data:
        if not isinstance(d, dict):
            continue
        try:
            result.append(DailyPlanItem(**d))
        except ValidationError:
            continue
    return result


def plan_weekly(state: GoalState):
    print("\n=== [节点] plan_weekly 生成周计划 ===")
    sg = state.get("struct_goal")
    goal_text = state["goal"] if not sg else (
        f"目标：{sg['title']}；领域：{sg['domain']}；"
        f"持续{sg['duration_weeks']}周；成功标准：{'、'.join(sg['success_criteria'])}；"
        f"约束：{'、'.join(sg['constraints'])}"
    )
    resp = llm.invoke([HumanMessage(content=WEEKLY_PROMPT.format(goal=goal_text))])
    weekly = [w.model_dump() for w in extract_weekly_plans(resp.content)]
    return {"weekly_plans": weekly, "messages": [HumanMessage(content=f"已生成{len(weekly)}周计划")]}

def plan_daily(state: GoalState):
    print("=== [节点] plan_daily 生成日计划 ===")
    resp = llm.invoke([HumanMessage(content=DAILY_PROMPT.format(
        goal=state["goal"], 
        weekly=json.dumps(state["weekly_plans"], ensure_ascii=False))
    )])
    daily = [d.model_dump() for d in extract_daily_plans(resp.content)]
    return {"daily_plans": daily}

def validate(state: GoalState):
    print("=== [节点] validate 校验 ===")
    errors = []
    weekly = state.get("weekly_plans") or []
    if not weekly:
        errors.append("周计划为空")
    else:
        for i, w in enumerate(weekly):
            if not isinstance(w, dict):
                errors.append(f"第{i+1}周不是字典"); continue
            if "week" not in w: errors.append(f"第{i+1}周缺 week 字段")
            if not w.get("focus"): errors.append(f"第{i+1}周缺 focus 字段")
    if errors:
        fb = "; ".join(errors)
        print(f"  校验未通过: {fb}")
        return {"is_valid": False, "validation_feedback": fb, "retry_count": 1}
    print("  校验通过")
    return {"is_valid": True, "validation_feedback": "", "retry_count": 0}

def output(state: GoalState):
    print("=== [节点] output 汇总 ===")
    return {"final_plan": {
        "goal": state["goal"],
        "weekly": state.get("weekly_plans", []),
        "daily": state.get("daily_plans", []),
        "is_valid": state["is_valid"],
        "warning": "" if state["is_valid"] else "自动拆解草稿，未通过校验，请人工调整",
    }}

def route(state: GoalState) -> str:
    if state["is_valid"] or state["retry_count"] >= 3:
        return "output"
    return "plan_weekly"

USER_ID = 1   # TODO: 临时写死，正式版应从登录态/JWT 传入；当前 user 表 id=1 为测试用户

def save_plan(state: GoalState):
    print("=== [节点] save_plan 存 MySQL ===")

    sg = state.get("struct_goal") or {}
    weeks = sg.get("duration_weeks", 12)          
    start = datetime.now()
    end = start + timedelta(weeks=weeks)

    # 2. 组装入库数据：周/日计划是 list，表字段是 Text → json.dumps 成字符串
    plan_in = PlanCreate(
        goal=state["goal"],
        week_plan=json.dumps(state.get("weekly_plans", []), ensure_ascii=False),
        daily_plan=json.dumps(state.get("daily_plans", []), ensure_ascii=False),
        start_time=start,
        end_time=end,
        status="未完成",
    )

    # 3. 开独立会话，用完即关
    db = SessionLocal()
    try:
        plan = create_plan(db, plan_in, user_id=USER_ID)   # crud 里已 commit + refresh
        print(f"  已写入 plan 表，id={plan.id}")
        return {"saved_plan_id": plan.id, "save_confirmed": True, "save_error": ""}
    except Exception as e:
        print(f"  存储失败: {e}")
        return {"saved_plan_id": None, "save_confirmed": False, "save_error": str(e)}
    finally:
        db.close()


builder = StateGraph(GoalState)
builder.add_node("parse_goal",parse_goal)
builder.add_node("plan_weekly", plan_weekly)
builder.add_node("plan_daily", plan_daily)
builder.add_node("validate", validate)
builder.add_node("output", output)
builder.add_node("save_plan",save_plan)
builder.add_node("reject_goal",reject_goal)

builder.add_edge(START, "parse_goal")
builder.add_conditional_edges("parse_goal",gate_after_parse,{  
    "plan_weekly":"plan_weekly",
    "reject":"reject_goal",
})
builder.add_edge("reject_goal",END)
builder.add_edge("plan_weekly", "plan_daily")
builder.add_edge("plan_daily", "validate")
builder.add_conditional_edges("validate", route, {
    "plan_weekly": "plan_weekly",
    "output": "output",
})
builder.add_edge("output", "save_plan")
builder.add_edge("save_plan", END)
graph = builder.compile()

if __name__ == "__main__":
    import sys
    goal = sys.argv[1] if len(sys.argv) > 1 else "3个月内拿到暑期实习offer"
    result = graph.invoke({
        "goal": goal,
        "messages": [], "weekly_plans": [], "daily_plans": [],
        "is_valid": False, "validation_feedback": "",
        "retry_count": 0, "final_plan": {},
        "saved_plan_id": None, "save_confirmed": False, "save_error": "",
    })
    print(json.dumps(result["final_plan"], ensure_ascii=False, indent=2))
    print("存储确认:", result.get("save_confirmed"), "计划ID:", result.get("saved_plan_id"))