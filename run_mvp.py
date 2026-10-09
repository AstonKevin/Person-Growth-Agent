# -*- coding: utf-8 -*-
"""MVP 三 Agent 协同端到端演示：
Planner 拆解 → Scheduler 排日程 → Tracker 打卡 → （落后时）Scheduler 调整
跑：python -X utf8 run_mvp.py
"""
import json
import uuid
from datetime import date
from langgraph.types import Command
from app.agents.goal_breakdown import graph as planner
from app.agents.scheduler import scheduler_graph
from app.agents.tracking_agent import graph as tracker
from app.database import SessionLocal
from app.models.plan import Plan


def cfg():
    return {"configurable": {"thread_id": str(uuid.uuid4())}}


PLANNER_INIT = {
    "goal": "", "messages": [], "weekly_plans": [], "daily_plans": [],
    "is_valid": False, "validation_feedback": "",
    "retry_count": 0, "final_plan": {}, "struct_goal": None,
    "saved_plan_id": None, "save_confirmed": False, "save_error": "",
}


def _day1_tasks(plan_id):
    db = SessionLocal()
    try:
        p = db.query(Plan).filter(Plan.id == plan_id).first()
        return [d["task"] for d in json.loads(p.daily_plan) if d.get("day") == 1]
    finally:
        db.close()


def run_mvp(goal, city="南京"):
    today = date.today().isoformat()

    # 1) Planner 拆解
    inp = dict(PLANNER_INIT)
    inp["goal"] = goal
    r1 = planner.invoke(inp)
    plan_id = r1["saved_plan_id"]
    print(f"\n[1] Planner 拆解完成 → plan_id={plan_id}")

    # 2) Scheduler 排日程
    r2 = scheduler_graph.invoke(
        {"plan_id": plan_id, "today": today, "city": city, "mode": "schedule"},
        cfg(),
    )
    print(f"[2] Scheduler 排好日程 → {len(r2['events'])} 个事件")

    # 3) Tracker 打卡（day1 全部完成，正常）
    day1 = _day1_tasks(plan_id)
    items = [{"task": t, "completed": True, "note": "完成"} for t in day1]
    r3 = tracker.invoke(
        {"plan_id": plan_id, "today": today, "raw_checkin": "", "checkin_items": items},
        cfg(),
    )
    print(f"[3] Tracker 打卡 → 完成率 {r3['completion_rate']:.0%}")

    # 4) 模拟落后，Scheduler 调整（resume 复用同一 config）
    print("\n[4] 模拟进度落后（0%），Scheduler 调整")
    adj_cfg = cfg()
    r4 = scheduler_graph.invoke(
        {"plan_id": plan_id, "today": today, "city": city, "mode": "adjust",
         "current_rate": 0.0, "checkin_summary": "任务没完成"},
        adj_cfg,
    )
    if r4.get("__interrupt__"):
        print("    建议:", r4["__interrupt__"][0].value.get("suggestion"))
        r5 = scheduler_graph.invoke(Command(resume=True), adj_cfg)
        print("    调整落库:", r5["saved"])

    print("\n✅ 三 Agent 协同流程结束")


if __name__ == "__main__":
    run_mvp("1个月养成晨跑习惯")
