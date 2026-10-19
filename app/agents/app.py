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
from app.core.security import verify_password
from app.crud.user import (
    get_user_by_name,
    create_user,
    get_users_with_provider_flag,
    update_user_admin,
    count_user_stats,
)
from app.schemas.user import UserCreate
from app.agents.common import resolve_user_channel, _normalize_openai_base
from app.crud.provider import get_provider_by_user, upsert_provider, delete_provider
from app.schemas.provider import ProviderIn
from app.routers.providers import _friendly_error
from app.crud import usage as usage_crud
from app.core.config import get_settings
import openai

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


# ============ 3.5 登录 / 注册 ============
def login_user(name, password):
    """校验用户名密码，成功返回 (user, None)，失败返回 (None, 错误信息)。"""
    db = SessionLocal()
    try:
        user = get_user_by_name(db, name)
        if user and verify_password(password, user.password_hash):
            if not user.is_active:
                return None, "账号已被停用，请联系管理员"
            db.expunge(user)  # 分离出会话，避免 session 关闭后读取失效
            return user, None
        return None, "用户名或密码错误"
    finally:
        db.close()


def register_user(name, password):
    """注册新用户，成功返回 (user, None)。"""
    db = SessionLocal()
    try:
        if get_user_by_name(db, name):
            return None, "用户名已存在"
        user = create_user(db, UserCreate(name=name, password=password))
        db.expunge(user)
        return user, None
    finally:
        db.close()


def render_auth():
    """未登录时渲染登录 / 注册页。"""
    st.title("🔐 个人成长 Agent")
    t_login, t_reg = st.tabs(["登录", "注册"])

    with t_login:
        with st.form("login_form"):
            name = st.text_input("用户名")
            password = st.text_input("密码", type="password")
            login = st.form_submit_button("登录")
        if login:
            if not name.strip() or not password:
                st.error("请输入用户名和密码")
            else:
                user, err = login_user(name.strip(), password)
                if err:
                    st.error(err)
                else:
                    st.session_state.current_user = user
                    st.rerun()

    with t_reg:
        with st.form("register_form"):
            r_name = st.text_input("用户名（至少 2 个字符）")
            r_pwd = st.text_input("密码（至少 6 位）", type="password")
            r_pwd2 = st.text_input("确认密码", type="password")
            reg = st.form_submit_button("注册")
        if reg:
            if len(r_name.strip()) < 2:
                st.error("用户名至少 2 个字符")
            elif len(r_pwd) < 6:
                st.error("密码至少 6 位")
            elif r_pwd != r_pwd2:
                st.error("两次输入的密码不一致")
            else:
                user, err = register_user(r_name.strip(), r_pwd)
                if err:
                    st.error(err)
                else:
                    st.session_state.current_user = user
                    st.rerun()


# 登录拦截：未登录只显示登录页，不渲染后面的主功能
st.session_state.setdefault("current_user", None)
if st.session_state.current_user is None:
    render_auth()
    st.stop()


# ============ 4. 侧边栏 ============
with st.sidebar:
    cu = st.session_state.current_user
    role_tag = "管理员" if cu.role == "admin" else "普通用户"
    st.header(f"👤 {cu.name}")
    st.caption(f"角色：{role_tag}")
    if st.button("🚪 退出登录"):
        st.session_state.current_user = None
        st.rerun()
    st.divider()

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
    _db = SessionLocal()
    try:
        user_prov = get_provider_by_user(_db, st.session_state.current_user.id)
    finally:
        _db.close()

    if user_prov:
        st.info(f"💡 当前调用通道：**用户自带模型** `{user_prov.model}`（{user_prov.base_url}）")
    else:
        s = get_settings()
        st.caption(f"💡 当前调用通道：**平台默认通道** `{s.llm_model}`（可在「⚙️ 个人设置」中配置自带 Key）")

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
                # 按当前用户解析 LLM 通道：有自带 key 用自带的，否则走平台默认
                _db = SessionLocal()
                try:
                    _llm, _model, _provider = resolve_user_channel(
                        _db, st.session_state.current_user
                    )
                finally:
                    _db.close()
                _cfg = {
                    "configurable": {
                        "llm": _llm,
                        "user_id": st.session_state.current_user.id,
                        "model": _model,
                        "provider": _provider,
                    }
                }
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
                    },
                    _cfg,
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



# ============ 7.8 连通性测试辅助 ============
def test_provider_connection(base_url: str, api_key: str, model: str) -> tuple[bool, str]:
    """使用填写的配置发送微型请求验证连通性。"""
    try:
        client = openai.OpenAI(
            api_key=api_key,
            base_url=_normalize_openai_base(base_url),
            timeout=20,
        )
        client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        return True, "连接成功！凭证有效。"
    except Exception as exc:
        return False, _friendly_error(exc)


# ============ 7.9 Tab 3：个人设置 (BYOK) ============
def render_settings_tab():
    st.subheader("⚙️ 个人设置：大模型接入配置 (BYOK)")
    st.caption("支持配置您自己的 OpenAI 兼容接口（如 OpenAI / DeepSeek / 智谱 / 中转平台）。")
    st.caption("🔒 **安全保障**：您的 API Key 将经 Fernet 强加密后入库，**绝不回显、管理员也无权查看**。")

    db = SessionLocal()
    try:
        current_prov = get_provider_by_user(db, st.session_state.current_user.id)
    finally:
        db.close()

    if current_prov:
        st.success(
            f"✅ **已配置自带模型**：`{current_prov.model}` ｜ 服务地址：`{current_prov.base_url}`"
        )
    else:
        s = get_settings()
        st.info(f"ℹ️ **当前使用平台免费默认通道**：`{s.llm_model}`")

    st.markdown("---")

    default_base_url = current_prov.base_url if current_prov else "https://api.openai.com"
    default_model = current_prov.model if current_prov else "gpt-4o-mini"

    with st.form("settings_form"):
        base_url = st.text_input("API Base URL（不带 /v1，如 https://api.openai.com）", value=default_base_url)
        api_key = st.text_input(
            "API Key（为保障安全不回显，若需修改或首次配置请填入完整 Key）",
            type="password",
            placeholder="sk-...",
        )
        model = st.text_input("模型名称（如 gpt-4o-mini、deepseek-chat 等）", value=default_model)

        c_test, c_save = st.columns(2)
        test_clicked = c_test.form_submit_button("🔌 测试连接")
        save_clicked = c_save.form_submit_button("💾 保存配置")

    if test_clicked:
        if not api_key.strip():
            st.error("测试连接必须输入 API Key。")
        elif not base_url.strip() or not model.strip():
            st.error("请完整填写 Base URL 与模型名称。")
        else:
            with st.spinner("正在发送微型测试请求验证..."):
                ok, msg = test_provider_connection(base_url.strip(), api_key.strip(), model.strip())
            if ok:
                st.success(f"🎉 {msg}")
            else:
                st.error(f"❌ 连通失败：{msg}")

    if save_clicked:
        if not api_key.strip():
            st.error("保存配置必须输入 API Key。")
        elif not base_url.strip() or not model.strip():
            st.error("请完整填写 Base URL 与模型名称。")
        else:
            with st.spinner("正在加密并保存配置..."):
                db = SessionLocal()
                try:
                    prov_in = ProviderIn(
                        provider="openai",
                        base_url=base_url.strip(),
                        api_key=api_key.strip(),
                        model=model.strip(),
                    )
                    upsert_provider(db, st.session_state.current_user.id, prov_in)
                    st.success("✅ 配置保存成功！后续目标拆解将自动采用您的自带模型。")
                except Exception as exc:
                    st.error(f"❌ 保存失败: {exc}")
                finally:
                    db.close()
            st.rerun()

    if current_prov:
        st.markdown("---")
        st.write("如不再需要自带 Key，可一键清除并恢复为平台免费模型：")
        if st.button("🗑️ 清除自定义配置（恢复平台默认）"):
            db = SessionLocal()
            try:
                delete_provider(db, st.session_state.current_user.id)
                st.success("已清除自定义配置，已恢复平台默认通道。")
            finally:
                db.close()
            st.rerun()


# ============ 7.10 Tab 4：管理后台 (仅 Admin 可见) ============
def render_admin_tab():
    cu = st.session_state.current_user
    if cu.role != "admin":
        st.error("⛔ 权限不足：此页面仅限系统管理员访问。")
        return

    st.subheader("🛠️ 系统管理后台")
    st.caption("🔒 严格铁律：管理员仅可查阅用量元数据与系统状态，不可查阅用户目标、对话内容及密钥明文。")

    db = SessionLocal()
    try:
        user_stats = count_user_stats(db)
        totals = usage_crud.total_counts(db)
        models = usage_crud.counts_by_model(db)
        daily = usage_crud.counts_by_day(db, days=30)
        users_list = get_users_with_provider_flag(db)
    finally:
        db.close()

    total_req = totals["total"]
    err_req = totals["error"]
    err_rate = (err_req / total_req * 100) if total_req > 0 else 0.0
    total_tokens = totals["prompt_tokens"] + totals["completion_tokens"]

    # 1. 指标概览
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("总注册用户", f"{user_stats['total']} 人")
    m2.metric("当前活跃用户", f"{user_stats['active']} 人")
    m3.metric("总调用请求", f"{total_req} 次")
    m4.metric("请求错误率", f"{err_rate:.1f}%", delta=f"{err_req} 次失败", delta_color="inverse")
    m5.metric("累计消耗 Token", f"{total_tokens:,}")

    st.markdown("---")

    # 2. 图表展示
    c_chart1, c_chart2 = st.columns(2)

    with c_chart1:
        st.write("**📈 近 30 天调用趋势**")
        if daily:
            df_daily = pd.DataFrame(daily)
            fig_trend = go.Figure()
            fig_trend.add_trace(go.Scatter(
                x=df_daily["day"], y=df_daily["total"],
                name="总请求", mode="lines+markers", line=dict(color="#4f46e5", width=2)
            ))
            fig_trend.add_trace(go.Scatter(
                x=df_daily["day"], y=df_daily["error"],
                name="失败请求", mode="lines+markers", line=dict(color="#ef4444", width=2)
            ))
            fig_trend.update_layout(
                xaxis_title="日期", yaxis_title="次数",
                height=320, margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(fig_trend, use_container_width=True)
        else:
            st.caption("暂无用量记录。")

    with c_chart2:
        st.write("**🤖 各模型使用分布**")
        if models:
            df_models = pd.DataFrame(models)
            fig_model = go.Figure(go.Bar(
                x=df_models["model"],
                y=df_models["count"],
                marker_color="#06b6d4",
                text=df_models["count"],
                textposition="auto",
            ))
            fig_model.update_layout(
                xaxis_title="模型", yaxis_title="调用次数",
                height=320, margin=dict(l=10, r=10, t=30, b=10),
            )
            st.plotly_chart(fig_model, use_container_width=True)
        else:
            st.caption("暂无模型记录。")

    st.markdown("---")

    # 3. 用户管理表格与操作
    st.write("👥 **用户列表与权限管理**")
    if users_list:
        df_u = pd.DataFrame(users_list).rename(
            columns={
                "id": "用户 ID",
                "name": "用户名",
                "role": "角色",
                "is_active": "启用状态",
                "has_provider": "已配 BYOK",
                "create_time": "注册时间",
            }
        )
        df_u["角色"] = df_u["角色"].map({"admin": "👑 管理员", "user": "👤 普通用户"})
        df_u["启用状态"] = df_u["启用状态"].map({True: "🟢 启用", False: "🔴 停用"})
        df_u["已配 BYOK"] = df_u["已配 BYOK"].map({True: "✅ 是", False: "⚪ 否"})
        st.dataframe(df_u, use_container_width=True, hide_index=True)

        st.write("**修改用户权限 / 启用状态**")
        with st.form("admin_user_form"):
            user_options = {u["id"]: f"[ID {u['id']}] {u['name']} ({u['role']})" for u in users_list}
            selected_uid = st.selectbox("选择目标用户", options=list(user_options.keys()), format_func=lambda uid: user_options[uid])
            target_user = next((u for u in users_list if u["id"] == selected_uid), None)

            c_role, c_active = st.columns(2)
            new_role = c_role.selectbox("分配角色", options=["user", "admin"], index=0 if target_user and target_user["role"] == "user" else 1)
            new_active = c_active.checkbox("账号启用", value=target_user["is_active"] if target_user else True)

            submitted = st.form_submit_button("确认修改")

        if submitted:
            if selected_uid == cu.id and not new_active:
                st.error("❌ 禁止停用当前登录的管理员账号！")
            elif selected_uid == cu.id and new_role != "admin":
                st.error("❌ 禁止取消自身的管理员权限！")
            else:
                db = SessionLocal()
                try:
                    update_user_admin(db, selected_uid, role=new_role, is_active=new_active)
                    st.success(f"✅ 已成功更新用户 ID={selected_uid} 的状态与角色！")
                except Exception as exc:
                    st.error(f"❌ 更新失败: {exc}")
                finally:
                    db.close()
                st.rerun()


# ============ 8. 页面主入口渲染 ============
cu = st.session_state.current_user
tab_names = ["🎯 目标拆解", "✅ 每日打卡", "⚙️ 个人设置"]
if cu and cu.role == "admin":
    tab_names.append("🛠️ 管理后台")

tabs = st.tabs(tab_names)
with tabs[0]:
    render_goal_tab()
with tabs[1]:
    render_checkin_tab()
with tabs[2]:
    render_settings_tab()
if cu and cu.role == "admin" and len(tabs) > 3:
    with tabs[3]:
        render_admin_tab()
