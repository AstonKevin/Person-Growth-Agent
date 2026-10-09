# -*- coding: utf-8 -*-
import sys
import pathlib
import json
import uuid
from datetime import date, datetime
import streamlit as st
import pandas as pd
import plotly.graph_objects as go


# ============ 1. 优先设置系统路径，防止导入 app 模块时报错 ============
ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 必须在 sys.path 生效后再导入项目内部模块
from app.database import SessionLocal
from app.models.plan import Plan
from app.models.checkin import CheckIn
from app.agents.goal_breakdown import graph as goal_graph
from app.agents.tracking_agent import graph as tracking_graph
from app.agents.weather import CITY_COORDS, get_weather, is_outdoor_task, is_weather_unfriendly
from langgraph.types import Command

# ============ 2. 页面基础配置与全局状态 ============
st.set_page_config(page_title="个人成长 Agent", layout="wide")
st.title("🌟 个人成长 Agent")

# 初始化全局会话状态
for key, default in [
    ("last_result", None),
    ("history", []),
    ("running", False),
    ("pending", None),
    ("last_checkin", None),
]:
    st.session_state.setdefault(key, default)


# ============ 3. 数据库数据访问辅助函数 ============
def load_all_plans():
    """获取所有计划"""
    db = SessionLocal()
    try:
        return db.query(Plan).order_by(Plan.id).all()
    finally:
        db.close()


def load_today_tasks_for_ui(plan_id, today_str):
    """
    读取某计划某天的任务，返回 (任务列表, 第几天)
    做足兼容性处理：支持 JSON 字符串与已被 ORM 解析为 list 的数据结构
    """
    db = SessionLocal()
    try:
        plan = db.query(Plan).filter(Plan.id == plan_id).first()
        if not plan:
            return [], None

        # 兼容 daily_plan 为空的情况
        if not plan.daily_plan:
            return [], -1

        # 兼容 list 类型与 JSON 字符串类型
        if isinstance(plan.daily_plan, list):
            all_daily = plan.daily_plan
        elif isinstance(plan.daily_plan, str):
            try:
                all_daily = json.loads(plan.daily_plan)
            except (json.JSONDecodeError, TypeError):
                return [], -1
        else:
            return [], -1

        # 兼容 start_time 为 datetime 或 str 的情况
        if isinstance(plan.start_time, str):
            start_date = datetime.fromisoformat(plan.start_time).date()
        elif hasattr(plan.start_time, "date"):
            start_date = plan.start_time.date()
        else:
            start_date = date.today()

        today = date.fromisoformat(today_str)
        day_index = (today - start_date).days + 1

        # 提取当天的具体任务
        today_items = [
            d for d in all_daily if isinstance(d, dict) and d.get("day") == day_index
        ]
        tasks = [
            {"task": d.get("task", "未命名任务"), "notes": d.get("notes", "")}
            for d in today_items
        ]
        return tasks, day_index
    finally:
        db.close()


def load_checkin_history(plan_id):
    """查询某计划的全部打卡记录，按时间排序"""
    db = SessionLocal()
    try:
        rows = (
            db.query(CheckIn)
            .filter(CheckIn.plan_id == plan_id)
            .order_by(CheckIn.checkin_date)
            .all()
        )
        return [
            {
                "日期": r.checkin_date,
                "类型": r.task_type,
                "时长": r.duration,
                "强度": r.intensity,
                "完成率": (r.intensity or 0) / 5,
            }
            for r in rows
        ]
    finally:
        db.close()


# ============ 4. 侧边栏 ============
with st.sidebar:
    st.header("使用说明")
    st.write("🎯 **目标拆解**：填目标 → AI 拆解 → 存库。")
    st.write("✅ **每日打卡**：勾选/文字 → 计算进度 → 反馈。")
    all_plans_count = len(load_all_plans())
    st.write(f"当前数据库共有计划：**{all_plans_count}** 个")
    st.write(f"本次会话生成：**{len(st.session_state.history)}** 个")
    if st.button("🗑️ 清空当前页面状态"):
        st.session_state.clear()
        st.rerun()


# ============ 5. 结果展示函数 ============
def show_result(result):
    """展示目标拆解结果"""
    plan = result.get("final_plan") or {}
    if not result.get("save_confirmed") and not plan.get("weekly"):
        st.info(plan.get("warning") or "我没理解这个目标，请换个说法。")
        return
    if result.get("save_confirmed"):
        st.success(f"✅ 计划已保存到数据库，计划 ID = {result.get('saved_plan_id')}")
        st.info("👉 计划已就绪，请到上方「✅ 每日打卡」标签，选择这个计划开始打卡。")

    else:
        st.error(f"❌ 保存失败：{result.get('save_error')}")

    st.subheader("📅 周计划")
    if plan.get("weekly"):
        wdf = pd.DataFrame(plan["weekly"]).rename(
            columns={
                "week": "周",
                "focus": "本周重点",
                "tasks": "任务",
                "milestone": "里程碑",
            }
        )
        wdf["任务"] = wdf["任务"].apply(
            lambda x: "、".join(x) if isinstance(x, list) else x
        )
        st.dataframe(wdf, use_container_width=True, hide_index=True)

    st.subheader("🗓️ 日计划（第 1 周）")
    if plan.get("daily"):
        ddf = pd.DataFrame(plan["daily"]).rename(
            columns={
                "day": "第几天",
                "week": "周",
                "task": "任务",
                "time_estimate": "预计时长",
                "priority": "优先级",
                "notes": "备注",
            }
        )
        st.dataframe(ddf, use_container_width=True, hide_index=True)

    if plan.get("warning"):
        st.warning(plan["warning"])


def show_checkin_result(r):
    """展示打卡结果"""
    dev_map = {
        "on_track": "正常",
        "slightly_behind": "轻微落后",
        "significantly_behind": "严重落后",
    }
    dev_icon = {
        "on_track": "🟢",
        "slightly_behind": "🟡",
        "significantly_behind": "🔴",
    }
    trend_icon = {"up": "📈 上升", "down": "📉 下降", "flat": "➡️ 平稳"}
    rate = float(r.get("completion_rate", 0))

    st.success("🎉 打卡完成！")
    c_left, c_right = st.columns(2)

    # 左：环形图（中心显示百分比）
    with c_left:
        fig = go.Figure(go.Pie(
            values=[rate, 1 - rate],
            labels=["已完成", "未完成"],
            hole=0.7,
            marker_colors=["#22c55e", "#e5e7eb"],
        ))
        # 中心百分比
        fig.add_annotation(
            text=f"{rate:.0%}", x=0.5, y=0.5,
            font_size=30, showarrow=False
        )
        fig.update_layout(
            showlegend=False, height=300,
            margin=dict(t=10, b=10, l=10, r=10),
        )
        st.plotly_chart(fig, use_container_width=True)

    # 右：指标卡
    with c_right:
        deviation = r.get("deviation", "")
        st.metric("进度状态", dev_icon.get(deviation, "⚪") + dev_map.get(deviation, "未知"))
        st.metric("连续打卡", f"{r.get('streak_days', 0)} 天")
        st.metric("完成率趋势", trend_icon.get(r.get("trend"), "➡️ 平稳"))

    # 明细
    if r.get("checkin_items"):
        st.write("**打卡明细**")
        df = pd.DataFrame(r["checkin_items"]).rename(
            columns={"task": "任务", "completed": "完成", "note": "说明"}
        )
        df["完成"] = df["完成"].map({True: "✅", False: "❌"})
        st.dataframe(df, use_container_width=True, hide_index=True)

    if r.get("feedback"):
        st.info("**教练反馈**\n\n" + r["feedback"])

# ============ 6. Tab 1：目标拆解 ============
def render_goal_tab():
    with st.form("goal_form"):
        goal = st.text_input("你的成长目标", placeholder="例如：3个月拿到暑期实习offer")
        submitted = st.form_submit_button(
            "🚀 开始拆解", disabled=st.session_state.running
        )

    if submitted:
        goal = goal.strip()
        error = None
        if not goal:
            error = "目标不能为空，请输入你想达成的事。"
        elif len(goal) < 4:
            error = "目标太短了，请写清「多久 + 达成什么」。"
        elif goal.lower() in {"你好", "您好", "hi", "hello", "谢谢", "感谢", "thanks"}:
            error = "这看起来是个打招呼～请告诉我具体目标。"

        if error:
            st.warning(error)
        else:
            st.session_state.running = True
            with st.spinner("正在理解目标、拆解并保存..."):
                result = goal_graph.invoke(
                    {
                        "goal": goal,
                        "messages": [],
                        "weekly_plans": [],
                        "daily_plans": [],
                        "is_valid": False,
                        "validation_feedback": "",
                        "retry_count": 0,
                        "final_plan": {},
                        "struct_goal": None,
                        "saved_plan_id": None,
                        "save_confirmed": False,
                        "save_error": "",
                    }
                )
            st.session_state.running = False
            st.session_state.last_result = result
            st.session_state.history.append({"goal": goal, "result": result})
            st.session_state["checkin_plan_select"] = result["saved_plan_id"]

    if st.session_state.last_result is not None:
        show_result(st.session_state.last_result)


# ============ 7. Tab 2：每日打卡 ============
def render_checkin_tab():
    # 7.1 中断恢复区 (LangGraph Human-in-the-loop)
    if st.session_state.pending:
        payload = st.session_state.pending["payload"]
        is_weather = "weather" in payload

        if is_weather:
            st.warning("🌦️ 今天天气不宜户外，建议调整为室内运动：")
            st.write("**天气情况：**", payload.get("weather", ""))
            st.write("**调整建议：**", payload.get("suggestion", ""))
        else:
            st.warning("⚠️ 进度严重落后，系统建议调整计划，请确认：")
            st.write("**调整建议：**", payload.get("suggestion", ""))
            st.write("**当前完成率：**", payload.get("current_rate", ""))

        c1, c2 = st.columns(2)
        if c1.button("✅ 确认调整", use_container_width=True):
            cfg = st.session_state.pending["config"]
            plan_id = st.session_state.pending.get("plan_id")
            with st.spinner("正在应用调整并保存..."):
                result = tracking_graph.invoke(Command(resume=True), cfg)
            st.session_state.pending = None
            if result:
                result["plan_id"] = plan_id
                st.session_state.last_checkin = result
            st.rerun()

        if c2.button("❌ 保持原计划", use_container_width=True):
            cfg = st.session_state.pending["config"]
            plan_id = st.session_state.pending.get("plan_id")
            with st.spinner("正在保存..."):
                result = tracking_graph.invoke(Command(resume=False), cfg)
            st.session_state.pending = None
            if result:
                result["plan_id"] = plan_id
                st.session_state.last_checkin = result
            st.rerun()
        return


    # 7.2 加载计划列表
    plans = load_all_plans()
    if not plans:
        st.warning("还没有任何计划，请先在「🎯 目标拆解」页生成一个。")
        return

    plan_map = {p.id: p for p in plans}
    selected_id = st.selectbox(
        "选择要打卡的计划",
        options=list(plan_map.keys()),
        format_func=lambda pid: f"[ID {pid}] {plan_map[pid].goal}",
        key="checkin_plan_select",
    )
    checkin_date = st.date_input("打卡日期", value=date.today())
    city = st.selectbox("所在城市（户外天气判断）", options=list(CITY_COORDS.keys()), key="city_select")

    # 7.3 获取当日任务
    tasks, day_index = load_today_tasks_for_ui(selected_id, checkin_date.isoformat())

    # 7.4 任务表单部分（有任务才渲染表单，没任务只出提示，不中断整个页面渲染）
    if day_index == -1:
        st.error("该计划的日任务数据格式异常，请换一个计划。")
    elif not tasks:
        st.warning(f"第 {day_index} 天没有任务（可能尚未开始、已结束或为休息日）。")
    else:
        st.caption(f"今天是第 {day_index} 天，共 {len(tasks)} 项任务")
        # 有户外任务时，主动展示天气卡片
        outdoor = [t for t in tasks if is_outdoor_task(t["task"])]
        if outdoor:
            try:
                w = get_weather(*CITY_COORDS[city], checkin_date.isoformat())
                c1, c2, c3 = st.columns(3)
                c1.metric("最高温", f"{w['temp_max']}℃")
                c2.metric("最低温", f"{w['temp_min']}℃")
                c3.metric("降水概率", f"{w['precip_prob']}%")
                if is_weather_unfriendly(w):
                    st.warning("🌦️ 今天天气不宜户外，提交后可改为室内运动")
            except Exception:
                st.caption("天气获取失败")

        with st.form("checkin_form"):
            mode = st.radio("打卡方式", ["勾选任务", "文字描述"], horizontal=True)
            checks = {}
            text = ""
            if mode == "勾选任务":
                for i, t in enumerate(tasks):
                    label = t["task"] + (f"（{t['notes']}）" if t.get("notes") else "")
                    # 明确赋予全局唯一的 key，避免同名任务冲突
                    checks[i] = st.checkbox(
                        label, key=f"chk_{selected_id}_{day_index}_{i}"
                    )
            else:
                text = st.text_area(
                    "说说你今天做了什么",
                    placeholder="例如：我按计划完成了所有训练，并在晚上进行了复盘",
                )
            submitted = st.form_submit_button("提交打卡")

        # 7.5 提交处理
        if submitted:
            config = {"configurable": {"thread_id": str(uuid.uuid4())}}
            if mode == "勾选任务":
                items = [
                    {
                        "task": tasks[i]["task"],
                        "completed": checks.get(i, False),
                        "note": "前端勾选",
                    }
                    for i in range(len(tasks))
                ]
                inputs = {
                    "plan_id": selected_id,
                    "today": checkin_date.isoformat(),
                    "raw_checkin": "",
                    "checkin_items": items,
                    "city": city,
                }
            else:
                if not text.strip():
                    st.warning("请输入打卡内容后再提交。")
                    return
                inputs = {
                    "plan_id": selected_id,
                    "today": checkin_date.isoformat(),
                    "raw_checkin": text.strip(),
                    "city": city,
                }

            with st.spinner("正在处理打卡..."):
                result = tracking_graph.invoke(inputs, config)

            if result.get("__interrupt__"):
                st.session_state.pending = {
                    "config": config,
                    "payload": result["__interrupt__"][0].value,
                    "plan_id": selected_id,
                }
                st.rerun()
            else:
                result["plan_id"] = selected_id
                st.session_state.last_checkin = result
                st.rerun()

    # 7.6 展示本次打卡结果（绑定 plan_id，防止切换计划后状态串味）
    if (
        st.session_state.last_checkin
        and st.session_state.last_checkin.get("plan_id") == selected_id
    ):
        show_checkin_result(st.session_state.last_checkin)

    # 7.7 历史打卡记录与趋势（无论当天有无任务，均正常显示）
    with st.expander("📊 历史打卡记录与趋势", expanded=True):
        history = load_checkin_history(selected_id)
        if not history:
            st.caption("该计划还没有历史打卡记录。")
        else:
            hdf = pd.DataFrame(history)
            st.dataframe(hdf, use_container_width=True, hide_index=True)

                        # 按日期聚合完成率
            agg = hdf.groupby("日期")["完成率"].mean().sort_index()
            fig = go.Figure(go.Scatter(
                x=[d.isoformat() for d in agg.index],
                y=agg.values,
                mode="lines+markers",
                line=dict(color="#6366f1", width=3),
                marker=dict(size=10),
                fill="tozeroy",          # 折线下方淡色填充
            ))
            fig.update_layout(
                xaxis_title="日期", yaxis_title="完成率",
                yaxis_range=[0, 1], height=350,
                margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(fig, use_container_width=True)



# ============ 8. 页面主入口渲染 ============
tab1, tab2 = st.tabs(["🎯 目标拆解", "✅ 每日打卡"])
with tab1:
    render_goal_tab()
with tab2:
    render_checkin_tab()