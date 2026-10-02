# -*- coding: utf-8 -*-
import sys, pathlib
import streamlit as st

ROOT = pathlib.Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agents.goal_breakdown import graph

st.set_page_config(page_title="目标拆解 Agent", layout="wide")
st.title("🎯 目标拆解 Agent")

# 1. 会话状态初始化：setdefault 只在首次创建，rerun 不覆盖已有值
st.session_state.setdefault("last_result", None)   # 最近一次结果
st.session_state.setdefault("history", [])          # 全部历史
st.session_state.setdefault("running", False)       # 防重复提交的锁

# 侧边栏：说明 + 历史计数 + 清空
with st.sidebar:
    st.header("使用说明")
    st.write("填目标 → 前端校验 → AI 判断拆解 → 存 MySQL。")
    st.write(f"已生成计划：{len(st.session_state.history)} 个")
    if st.button("🗑️ 清空所有记录"):
        st.session_state.clear()
        st.rerun()

# 2. 表单：内部输入不触发 rerun，点提交才处理
with st.form("goal_form"):
    goal = st.text_input("你的成长目标", placeholder="例如：3个月拿到暑期实习offer")
    submitted = st.form_submit_button("🚀 开始拆解", disabled=st.session_state.running)

# 3. 提交：前端验证 → 调 graph → 结果存进 session_state
if submitted:
    goal = goal.strip()
    error = None
    if not goal:
        error = "目标不能为空，请输入你想达成的事。"
    elif len(goal) < 4:
        error = "目标太短了，请写清「多久 + 达成什么」，例如：3个月拿到暑期实习offer。"
    elif goal.lower() in {"你好", "您好", "hi", "hello", "谢谢", "感谢", "thanks"}:
        error = "这看起来是打招呼～请告诉我具体目标，例如：3个月拿到暑期实习offer。"

    if error:
        st.warning(error)          # 前端拦下：不调模型
    else:
        st.session_state.running = True
        with st.spinner("正在理解目标、拆解并保存..."):
            result = graph.invoke({
                "goal": goal,
                "messages": [], "weekly_plans": [], "daily_plans": [],
                "is_valid": False, "validation_feedback": "",
                "retry_count": 0, "final_plan": {}, "struct_goal": None,
                "saved_plan_id": None, "save_confirmed": False, "save_error": "",
            })
        st.session_state.running = False
        st.session_state.last_result = result
        st.session_state.history.append({"goal": goal, "result": result})

# 4. 结果展示：从 session_state 读，所以 rerun 之后结果不丢
def show_result(result):
    plan = result["final_plan"]
    if not result.get("save_confirmed") and not plan.get("weekly"):
        st.info(plan.get("warning") or "我没理解这个目标，请换个说法。")
        return
    if result.get("save_confirmed"):
        st.success(f"✅ 计划已保存到数据库，计划 ID = {result['saved_plan_id']}")
    else:
        st.error(f"❌ 保存失败：{result.get('save_error')}")
    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📅 周计划")
        st.json(plan.get("weekly", []))
    with col2:
        st.subheader("🗓️ 日计划（第1周）")
        st.json(plan.get("daily", []))
    if plan.get("warning"):
        st.warning(plan["warning"])

if st.session_state.last_result is not None:
    show_result(st.session_state.last_result)
