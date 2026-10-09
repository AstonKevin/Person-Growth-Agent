"""
执行跟踪 Agent：打卡 → 计算完成率 → 反馈 → （偏差大时）建议调整并等用户确认
"""
import json
import uuid
from typing import TypedDict, Optional, Literal
from datetime import date, timedelta
from pydantic import BaseModel, Field, ValidationError, model_validator
from langchain_core.messages import HumanMessage
from langgraph.types import interrupt, Command
from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from app.database import SessionLocal
from app.models.plan import Plan
from app.models.checkin import CheckIn
from .common import make_llm, parse_json
from .weather import is_outdoor_task, get_weather, is_weather_unfriendly, CITY_COORDS
import copy





class TrackingState(TypedDict):
    # 输入
    plan_id:int
    today: str                           
    raw_checkin: str
    daily_tasks:list[dict]
    full_daily_plan: list[dict]
    # 节点1
    checkin_items:list[dict]
    # 节点2
    completion_rate: float                # 完成率 0.0~1.0
    streak_days: int                      # 连续打卡天数
    deviation: Literal["on_track", "slightly_behind", "significantly_behind"]
    history_checkins: list[dict]          # 今天之前的打卡 [{date, rate}]
    trend: Literal["up", "down", "flat"]  # 完成率趋势
    # 节点3
    feedback: str # ai反馈
    # 节点 4/5/6
    need_adjustment: bool
    adjustment_suggestion: Optional[str]
    user_confirmed: Optional[bool]
    adjustment_changes: list[dict]
    # 节点 7
    saved: bool
    # 天气（预防性调整）
    city: Optional[str]
    weather: Optional[dict]
    weather_warning: bool


# LLM配置
llm = make_llm(temperature=0.2)


def load_today_tasks(state: TrackingState):
    print("\n=== [节点] load_today_tasks 读今日计划 ===")
    db = SessionLocal()
    try:
        plan = db.query(Plan).filter(Plan.id == state["plan_id"]).first()
        if not plan:
            print("找不到计划", state["plan_id"])
            return {"daily_tasks": []}

        all_daily = json.loads(plan.daily_plan)

        # 算今天是第几天：今天 - 计划开始日 + 1
        today = date.fromisoformat(state["today"])
        day_index = (today - plan.start_time.date()).days + 1

        # 筛出今天的任务
        today_items = [d for d in all_daily if d.get("day") == day_index]
        tasks = [{"task": d.get("task"), "notes": d.get("notes", "")} for d in today_items]

        print(f"今天是第 {day_index} 天，{len(tasks)} 项任务")
        return {"daily_tasks": tasks, "full_daily_plan": all_daily}
    finally:
        db.close()

def fetch_weather(state: TrackingState):
    print("\n=== [节点] fetch_weather 查天气 ===")
    today_tasks = state.get("daily_tasks", [])
    outdoor = [t for t in today_tasks if is_outdoor_task(t.get("task", ""))]
    if not outdoor:
        print("今天没有户外任务，跳过天气")
        return {"weather": None, "weather_warning": False}

    city = state.get("city") or "南京"
    if city not in CITY_COORDS:
        print("未知城市，跳过")
        return {"weather": None, "weather_warning": False}

    lat, lon = CITY_COORDS[city]
    try:
        w = get_weather(lat, lon, state["today"])
    except Exception as e:
        print("天气查询失败：", e)
        return {"weather": None, "weather_warning": False}

    warning = is_weather_unfriendly(w)
    print(f"{city} 天气：{w}，不宜户外? {warning}")
    return {"weather": w, "weather_warning": warning}


def load_history(state: TrackingState):
    print("\n=== [节点] load_history 读历史打卡 ===")
    db = SessionLocal()
    try:
        rows = db.query(CheckIn).filter(
            CheckIn.plan_id == state["plan_id"],
            CheckIn.checkin_date < date.fromisoformat(state["today"]),
        ).order_by(CheckIn.checkin_date).all()

        history = [
            {"date": r.checkin_date.isoformat(), "rate": (r.intensity or 0) / 5}
            for r in rows
        ]
        print(f"读到 {len(history)} 条历史记录（今天之前）")
        return {"history_checkins": history}
    finally:
        db.close()


class CheckinItem(BaseModel):
    task: str
    completed: bool
    note: str = ""

class PlanChange(BaseModel):
    task: str
    action: Literal["postpone", "reduce", "lower_intensity", "switch_type"]
    to_day: Optional[int] = None
    new_content: Optional[str] = None
    new_type: Optional[str] = None

    @model_validator(mode="after")
    def check_required(self):
        # 延期必须给目标天数
        if self.action == "postpone" and self.to_day is None:
            raise ValueError("postpone 操作必须给出 to_day")
        # 减量必须给新内容
        if self.action == "reduce" and not self.new_content:
            raise ValueError("reduce 操作必须给出 new_content")
        # 降强度必须给新内容
        if self.action == "lower_intensity" and not self.new_content:
            raise ValueError("lower_intensity 操作必须给出 new_content")
        # 换类型必须给新类型和新内容
        if self.action == "switch_type":
            if not self.new_type or not self.new_content:
                raise ValueError("switch_type 操作必须给出 new_type 和 new_content")
        return self

CHECKIN_PROMPT = """你是一个打卡理解助手。
用户今天的计划任务如下：
{tasks}

用户的打卡原文：
{checkin}

请逐项判断每个任务是否完成，输出一个 JSON 数组，每个元素格式：
{{"task": "任务名", "completed": true或false, "note": "简短说明"}}

只输出 JSON，不要输出其他文字。"""

def receive_checkin(state: TrackingState):
    print("\n=== [节点] receive_checkin 理解打卡 ===")

    # —— 勾选方式：前端已构造好结构化 checkin_items，直接用，跳过 LLM ——
    if state.get("checkin_items"):
        items = state["checkin_items"]
        done = sum(1 for i in items if i["completed"])
        print(f"勾选方式：共 {len(items)} 项，完成 {done} 项（跳过 LLM）")
        return {"checkin_items": items}

    # —— 文字方式：调 LLM 逐项判断（原有逻辑） ——
    tasks_text = "\n".join(
        f"- {i + 1}. {t.get('task')}" for i, t in enumerate(state["daily_tasks"])
    )
    resp = llm.invoke([HumanMessage(content=CHECKIN_PROMPT.format(
        tasks=tasks_text, checkin=state["raw_checkin"]
    ))])

    data = parse_json(resp.content)
    if not isinstance(data, list):
        data = []

    # Pydantic 校验，坏条目跳过（和目标拆解一样的容错思路）
    items = []
    for d in data:
        if isinstance(d, dict):
            try:
                items.append(CheckinItem(**d).model_dump())
            except ValidationError:
                continue

    done = sum(1 for i in items if i["completed"])
    print(f"文字方式：共 {len(items)} 项，完成 {done} 项")
    return {"checkin_items": items}

def calculate_progress(state: TrackingState):
    print("\n=== [节点] calculate_progress 计算进度 ===")
    items = state["checkin_items"]
    total = len(items)
    completed = sum(1 for i in items if i["completed"])

    # 完成率
    rate = completed / total if total > 0 else 0.0
    today_d = date.fromisoformat(state["today"])

    # —— 连续打卡天数（口径：有记录即算）——
    history = state.get("history_checkins", [])
    date_set = {date.fromisoformat(h["date"]) for h in history}
    date_set.add(today_d)          # 今天本次打卡
    streak = 0
    d = today_d
    while d in date_set:
        streak += 1
        d -= timedelta(days=1)     # 往前一天

    # —— 偏差等级 ——
    if rate >= 0.8:
        deviation = "on_track"
    elif rate >= 0.4:
        deviation = "slightly_behind"
    else:
        deviation = "significantly_behind"

    # —— 趋势：今天 vs 历史平均（±10% 算波动）——
    if history:
        avg = sum(h["rate"] for h in history) / len(history)
        if rate > avg + 0.1:
            trend = "up"
        elif rate < avg - 0.1:
            trend = "down"
        else:
            trend = "flat"
    else:
        trend = "flat"

    print(f"完成率 {rate:.0%}，连续 {streak} 天，趋势 {trend}，偏差 {deviation}")
    return {
        "completion_rate": rate,
        "streak_days": streak,
        "deviation": deviation,
        "trend": trend,
    }



FEEDBACK_PROMPT = """你是一个友好的个人成长教练。
用户今天的打卡情况：
- 今日任务：
{tasks}
- 完成情况：{completed}/{total}
- 完成率：{rate}
- 连续打卡：{streak} 天
- 进度状态：{deviation}

请写一段 2-3 句话的反馈，要求：
- 对完成的任务给予具体肯定
- 对没完成的任务给出明确的补做提醒
- 语气鼓励，不要说教
- 进度正常时：鼓励保持节奏
- 轻微落后时：提醒补做、语气轻松
- 严重落后时：温和指出差距，不要批评

直接输出反馈文本，不要加引号或其他格式。"""


def generate_feedback(state: TrackingState):
    print("\n=== [节点] generate_feedback 生成反馈 ===")
    items = state["checkin_items"]
    total = len(items)
    completed = sum(1 for i in items if i["completed"])

    tasks_text = "\n".join(
        f"- {i['task']}:{'✓' if i['completed'] else '✗'} {i.get('note', '')}"
        for i in items
    )

    deviation_map = {
        "on_track": "正常",
        "slightly_behind": "轻微落后",
        "significantly_behind": "严重落后",
    }

    resp = llm.invoke([HumanMessage(content=FEEDBACK_PROMPT.format(
        tasks=tasks_text,
        completed=completed,
        total=total,
        rate=f"{state['completion_rate']:.0%}",
        streak=state["streak_days"],
        deviation=deviation_map.get(state["deviation"], "未知"),
    ))])

    feedback = resp.content.strip()
    print(f"反馈：{feedback}")
    return {"feedback": feedback}


def route_after_feedback(state: TrackingState) -> str:
    """严重落后才走调整分支，其他直接存打卡"""
    if state["deviation"] == "significantly_behind":
        return "adjust"
    return "save"

ADJUSTMENT_PROMPT = """你是一个目标调整顾问。用户的计划执行严重落后，请给出具体、可执行的调整方案。

原计划的每日任务（供参考）：
{daily_tasks}

实际执行情况：
- 今日完成率：{rate}
- 打卡明细：
{items}

请给出调整建议，并用操作指令说明如何改任务。可选择的操作：
- 延期（postpone）：任务挪到以后，需给出 to_day
- 减量（reduce）：任务量减少，需给出 new_content
- 降强度（lower_intensity）：难度降低（如快跑改慢跑），需给出 new_content
- 换类型（switch_type）：换成别的类型（如户外改室内），需给出 new_type 和 new_content

只输出一个 JSON，格式如下：
{{
  "suggestion": "2-3句话的调整建议",
  "changes": [
    {{"task": "任务原文", "action": "postpone", "to_day": 6}},
    {{"task": "任务原文", "action": "lower_intensity", "new_content": "降低难度后的任务"}}
  ]
}}

只输出 JSON，不要输出其他文字。调整要现实可执行，根据落后程度选择最合适的策略。"""

WEATHER_ADJUST_PROMPT = """你是一个运动计划顾问。今天天气不适合户外运动，需要把户外任务调整为可执行的方案。

今天的天气：
- 最高温 {temp_max}℃，最低温 {temp_min}℃
- 降水概率 {precip_prob}%

今天的户外任务：
{outdoor_tasks}

请给出调整方案，用操作指令说明：
- 换类型（switch_type）：把户外任务换成室内运动，需给出 new_type（如"室内"）和 new_content
- 降强度（lower_intensity）：若无法完全室内替代，就降低强度，需给出 new_content

只输出 JSON：
{{
  "suggestion": "1-2句话，说明天气情况和建议",
  "changes": [
    {{"task": "户外任务原文", "action": "switch_type", "new_type": "室内", "new_content": "室内替代任务"}}
  ]
}}

只输出 JSON，替代运动要现实可行。"""

def weather_adjust_plan(state: TrackingState):
    print("\n=== [节点] weather_adjust_plan 生成天气调整 ===")
    w = state.get("weather") or {}
    outdoor_tasks = [t for t in state.get("daily_tasks", []) if is_outdoor_task(t.get("task", ""))]
    tasks_text = "\n".join(f"- {t['task']}" for t in outdoor_tasks)

    resp = llm.invoke([HumanMessage(content=WEATHER_ADJUST_PROMPT.format(
        temp_max=w.get("temp_max"),
        temp_min=w.get("temp_min"),
        precip_prob=w.get("precip_prob"),
        outdoor_tasks=tasks_text,
    ))])
    data = parse_json(resp.content)
    if not isinstance(data, dict):
        data = {}

    suggestion = data.get("suggestion", "今天天气不宜户外，建议改为室内运动")
    changes = []
    for c in data.get("changes", []):
        if isinstance(c, dict):
            try:
                changes.append(PlanChange(**c).model_dump())
            except ValidationError:
                continue
    print(f"天气建议：{suggestion}")
    print(f"有效操作 {len(changes)} 条")
    return {"adjustment_suggestion": suggestion, "adjustment_changes": changes}


def confirm_weather_adjust(state: TrackingState):
    print("\n=== [节点] confirm_weather_adjust 暂停等确认 ===")
    w = state.get("weather") or {}
    payload = {
        "question": "今天天气不宜户外，是否调整计划？",
        "suggestion": state.get("adjustment_suggestion", ""),
        "weather": f"降水概率 {w.get('precip_prob')}%，{w.get('temp_min')}~{w.get('temp_max')}℃",
    }
    answer = interrupt(payload)
    confirmed = answer in (True, "yes", "是", "确认")
    print(f"用户确认：{confirmed}")
    return {"user_confirmed": confirmed}


def maybe_adjust_plan(state: TrackingState):
    print("\n=== [节点] maybe_adjust_plan 生成调整建议 ===")
    full = state.get("full_daily_plan", [])

    # 完整每日计划给 LLM 看（第几天 + 任务原文）
    daily_text = "\n".join(
        f"- 第{d.get('day')}天：{d.get('task')}"
        for d in full if isinstance(d, dict)
    )
    items_text = "\n".join(
        f"- {i['task']}：{'✓' if i['completed'] else '✗'} {i.get('note', '')}"
        for i in state["checkin_items"]
    )
    resp = llm.invoke([HumanMessage(content=ADJUSTMENT_PROMPT.format(
        daily_tasks=daily_text,
        rate=f"{state['completion_rate']:.0%}",
        items=items_text,
    ))])

    data = parse_json(resp.content)
    if not isinstance(data, dict):
        data = {}

    suggestion = data.get("suggestion", "建议适当降低任务量、延后截止时间")

    # 解析操作指令，Pydantic 校验，坏操作跳过
    changes = []
    for c in data.get("changes", []):
        if isinstance(c, dict):
            try:
                changes.append(PlanChange(**c).model_dump())
            except ValidationError:
                continue

    print(f"调整建议：{suggestion}")
    print(f"有效操作 {len(changes)} 条")
    return {
        "need_adjustment": True,
        "adjustment_suggestion": suggestion,
        "adjustment_changes": changes,
    }

def apply_weather_adjust(state: TrackingState):
    print("\n=== [节点] apply_weather_adjust 应用天气调整 ===")
    if not state.get("user_confirmed"):
        print("用户选择保持户外原计划")
        return {}

    base_plan = state.get("full_daily_plan", [])
    changes = state.get("adjustment_changes", [])
    new_plan = apply_changes(base_plan, changes)

    today = date.fromisoformat(state["today"])
    new_tasks = []
    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == state["plan_id"]).first()
        if p and new_plan:
            p.daily_plan = json.dumps(new_plan, ensure_ascii=False)
            db.commit()
            print(f"已按天气更新计划 {p.id}")
            day_index = (today - p.start_time.date()).days + 1
            today_items = [d for d in new_plan if d.get("day") == day_index]
            new_tasks = [{"task": d.get("task"), "notes": d.get("notes", "")} for d in today_items]
        else:
            print("未更新")
    finally:
        db.close()

    if new_tasks:
        return {"full_daily_plan": new_plan, "daily_tasks": new_tasks}
    return {}


def confirm_adjustment(state: TrackingState):
    print("\n=== [节点] confirm_adjustment 暂停等用户确认 ===")
    payload = {
        "question": "系统建议调整计划，是否确认？",
        "suggestion": state["adjustment_suggestion"],
        "current_rate": f"{state['completion_rate']:.0%}",
    }
    # 在这里暂停，等用户点确认/取消
    user_answer = interrupt(payload)
    # resume 后：user_answer = Command(resume=...) 传的值
    confirmed = user_answer in (True, "yes", "是", "确认")
    print(f"用户确认：{confirmed}")
    return {"user_confirmed": confirmed}

def apply_changes(daily_plan: list[dict], changes: list[dict]) -> list[dict]:
    """
    纯函数：根据操作指令生成调整后的新 daily_plan
    - postpone：把任务挪到指定天数
    - reduce / lower_intensity：把任务改成新内容
    - switch_type：改任务内容并记录新类型 category
    不修改原数据；找不到匹配任务的操作跳过。
    """
    new_plan = copy.deepcopy(daily_plan)
    used = set()

    for ch in changes:
        target_task = ch.get("task", "")
        idx = _find_task_index(new_plan, target_task, used)
        if idx is None:
            continue
        used.add(idx)

        item = new_plan[idx]
        action = ch.get("action")

        if action == "postpone":
            to_day = ch.get("to_day")
            if to_day is not None:
                item["day"] = to_day
                item["week"] = (to_day - 1) // 7 + 1
        elif action in ("reduce", "lower_intensity"):
            new_content = ch.get("new_content")
            if new_content:
                item["task"] = new_content
        elif action == "switch_type":
            new_content = ch.get("new_content")
            if new_content:
                item["task"] = new_content
            if ch.get("new_type"):
                item["category"] = ch["new_type"]   # 记录换后的类型

    return new_plan


def _find_task_index(plan: list[dict], task_text: str, used: set):
    """先完全匹配，再包含匹配；跳过已用索引"""
    # 完全匹配
    for i, item in enumerate(plan):
        if i not in used and item.get("task") == task_text:
            return i
    # 包含匹配（任一方包含另一方）
    for i, item in enumerate(plan):
        if i in used:
            continue
        t = item.get("task", "")
        if task_text and t and (task_text in t or t in task_text):
            return i
    return None


def apply_adjustment(state: TrackingState):
    print("\n=== [节点] apply_adjustment 应用调整 ===")
    if not state.get("user_confirmed"):
        print("用户取消，保持原计划")
        return {"feedback": state["feedback"] + "\n\n已保持原计划，建议加快进度。"}

    # 确认：算新计划 → 写回 plan 表
    base_plan = state.get("full_daily_plan", [])
    changes = state.get("adjustment_changes", [])
    new_plan = apply_changes(base_plan, changes)

    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == state["plan_id"]).first()
        if p and new_plan:
            p.daily_plan = json.dumps(new_plan, ensure_ascii=False)
            db.commit()
            print(f"已更新计划 {p.id}，应用 {len(changes)} 条操作")
            note = f"\n\n✅ 计划已按你的确认调整（{len(changes)} 项）。"
        else:
            print("找不到计划或无有效操作，未更新")
            note = "\n\n⚠️ 调整未能应用。"
    finally:
        db.close()

    return {"feedback": state["feedback"] + note}

def route_after_weather(state: TrackingState) -> str:
    """fetch_weather 后：天气差 → 预防调整，否则直接打卡"""
    if state.get("weather_warning"):
        return "weather_adjust"
    return "checkin"


def save_checkin(state: TrackingState):
    print("\n=== [节点] save_checkin 写打卡记录 ===")
    db = SessionLocal()
    try:
        rate = state["completion_rate"]
        checkin = CheckIn(
            plan_id=state["plan_id"],
            user_id=1,                                    # TODO: 临时写死，正式版从登录态取
            checkin_date=date.fromisoformat(state["today"]),
            task_type="学习",                              # TODO: 可从 plan 领域推断
            duration=None,
            intensity=int(round(rate * 5)),               # 完成率 0~1 映射强度 0~5
        )
        db.add(checkin)
        db.commit()
        db.refresh(checkin)
        print(f"打卡已保存，id={checkin.id}")
        return {"saved": True}
    finally:
        db.close()


builder = StateGraph(TrackingState)

# 添加节点
builder.add_node("receive_checkin", receive_checkin)
builder.add_node("calculate_progress", calculate_progress)
builder.add_node("generate_feedback", generate_feedback)
builder.add_node("maybe_adjust_plan", maybe_adjust_plan)
builder.add_node("confirm_adjustment", confirm_adjustment)
builder.add_node("apply_adjustment", apply_adjustment)
builder.add_node("save_checkin", save_checkin)
builder.add_node("load_today_tasks", load_today_tasks)
builder.add_node("load_history", load_history)
builder.add_node("fetch_weather", fetch_weather)
builder.add_node("weather_adjust_plan", weather_adjust_plan)
builder.add_node("confirm_weather_adjust", confirm_weather_adjust)
builder.add_node("apply_weather_adjust", apply_weather_adjust)


# 入口：读今日任务 → 查天气
builder.add_edge(START, "load_today_tasks")
builder.add_edge("load_today_tasks", "fetch_weather")

# 天气分流：差则预防性调整，否则直接打卡
builder.add_conditional_edges(
    "fetch_weather",
    route_after_weather,
    {
        "weather_adjust": "weather_adjust_plan",
        "checkin": "receive_checkin",
    }
)
builder.add_edge("weather_adjust_plan", "confirm_weather_adjust")
builder.add_edge("confirm_weather_adjust", "apply_weather_adjust")
builder.add_edge("apply_weather_adjust", "receive_checkin")

# 打卡 → 历史 → 计算 → 反馈
builder.add_edge("receive_checkin", "load_history")
builder.add_edge("load_history", "calculate_progress")
builder.add_edge("calculate_progress", "generate_feedback")

# 反馈后分流
builder.add_conditional_edges(
    "generate_feedback",
    route_after_feedback,
    {
        "adjust": "maybe_adjust_plan",
        "save": "save_checkin",
    }
)

# 进度调整分支
builder.add_edge("maybe_adjust_plan", "confirm_adjustment")
builder.add_edge("confirm_adjustment", "apply_adjustment")
builder.add_edge("apply_adjustment", "save_checkin")
builder.add_edge("save_checkin", END)

# 编译：必须挂 checkpointer，因为 interrupt 需要它
graph = builder.compile(checkpointer=InMemorySaver())



def run_agent(plan_id, today, raw_checkin, resume_value=None):
    # 每次调用用独立 thread，避免场景间状态串扰
    config = {"configurable": {"thread_id": str(uuid.uuid4())}}
    inputs = {
        "plan_id": plan_id,
        "today": today,
        "raw_checkin": raw_checkin,
    }
    r1 = graph.invoke(inputs, config)

    if r1.get("__interrupt__"):
        print("图暂停，等待确认...")
        print("payload:", r1["__interrupt__"][0].value)

        # 关键保护：没给选择时不传 None（规避 LangGraph 1.2.11 resume=None 的 bug），保持暂停
        if resume_value is None:
            print("（未传 resume_value，图保持暂停。确认后再调用）")
            return r1

        r2 = graph.invoke(Command(resume=resume_value), config)
        print("\n最终 feedback:", r2["feedback"])
        print("saved:", r2["saved"])
        return r2
    else:
        print("\n一次跑完，无需调整")
        print("feedback:", r1["feedback"])
        print("saved:", r1["saved"])
        return r1


if __name__ == "__main__":
    # 场景 1：完成今日任务（买好装备）→ 100% → 一次跑完
    print("=" * 50)
    print("场景 1：买好装备（完成）")
    run_agent(20, "2026-10-03", "我已经买好了健身装备")

    # 场景 2：没完成（练了动作没买装备）→ 0% → 暂停 → 确认调整
    print("\n" + "=" * 50)
    print("场景 2：没买装备（暂停，确认调整）")
    run_agent(20, "2026-10-03", "今天练了动作但没买装备", resume_value=True)

    # 场景 3：暂停但没传选择 → 保持暂停，不报错
    print("\n" + "=" * 50)
    print("场景 3：暂停，未做选择")
    run_agent(20, "2026-10-03", "今天练了动作但没买装备")
