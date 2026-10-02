
"""Prompt 对比实验台：固定目标集 × 多版本 prompt，输出客观指标对比表。
从项目根目录运行：python -m app.agents.prompt_lab
"""
from langchain_ollama import ChatOllama
from langchain.messages import HumanMessage
from app.agents.goal_breakdown import parse_json


eval_llm = ChatOllama(
    base_url="http://localhost:11434",
    model="qwen3:8b", 
    temperature=0
    )

# 固定测试目标集
TEST_GOALS = [
    {"title": "拿到暑期实习offer", "domain": "求职", "duration_weeks": 4,
     "success_criteria": ["拿到至少1个offer"], "constraints": ["每天可投入2小时"]},
    {"title": "通过英语六级", "domain": "学习", "duration_weeks": 8,
     "success_criteria": ["六级总分≥425"], "constraints": ["每天可投入1.5小时", "基础一般"]},
    {"title": "减脂5公斤", "domain": "健身", "duration_weeks": 12,
     "success_criteria": ["体重下降5公斤且不反弹"], "constraints": ["每周运动4次", "不去健身房"]},
]

def goal_text(sg: dict) -> str:
    return (f"目标：{sg['title']}；领域：{sg['domain']}；持续{sg['duration_weeks']}周；"
            f"成功标准：{'、'.join(sg['success_criteria'])}；约束：{'、'.join(sg['constraints'])}")


# ---------- v1：朴素基线（接近你现在的版本）----------
WEEKLY_V1 = """你是一个目标拆解专家。根据用户目标生成按周拆解的计划。
用户目标：{goal}
要求输出合法 JSON 数组，每个元素含 week、focus、tasks、milestone 字段，只输出 JSON。
"""

# ---------- v2：在 v1 基础上只加【角色 + 硬约束】----------
WEEKLY_V2 = """你是一位资深{domain}规划教练，擅长把大目标拆成可落地的周计划。
用户目标：{goal}
硬性要求：
1. 必须严格拆成 {weeks} 周，一周不能多也不能少，week 从 1 到 {weeks}。
2. 每周 tasks 给 3~5 条，每条必须是"本周内能完成、可验证"的具体动作，
   禁止出现"学习相关知识""提升能力"这类无法验证的空话。
3. focus 用一句话写清本周重点，milestone 写本周结束时能拿出来检查的成果。
4. 节奏要循序渐进：前期打基础、中期攻坚、后期收尾/模拟。
只输出合法 JSON 数组，每个元素含 week、focus、tasks、milestone，不要输出任何解释。
"""

# ---------- v3：在 v2 基础上只加【一个高质量 few-shot 示例】----------
WEEKLY_V3 = WEEKLY_V2 + """
参考示例（注意任务的具体程度，照此质量输出）：
[
  {{
    "week": 1,
    "focus": "完成岗位调研与简历初稿",
    "tasks": ["抓取10个目标岗位JD，统计出现频率最高的10项技能",
              "按STAR法则重写3段项目经历",
              "完成简历v1并请1位前辈批改"],
    "milestone": "产出技能缺口清单1份 + 简历v1"
  }}
]
"""


def score(text: str, expected_weeks: int) -> dict:
    """对一次输出算客观指标。"""
    data = parse_json(text)
    m = {"JSON合法": isinstance(data, list), "周数匹配": False,
         "字段完整周数": 0, "平均任务数": 0}
    if isinstance(data, list) and data:
        m["周数匹配"] = (len(data) == expected_weeks)
        complete, total = 0, 0
        for w in data:
            tasks = w.get("tasks") if isinstance(w, dict) else None
            if isinstance(w, dict) and w.get("focus") and isinstance(tasks, list) and w.get("milestone"):
                complete += 1
            total += len(tasks) if isinstance(tasks, list) else 0
        m["字段完整周数"] = f"{complete}/{len(data)}"
        m["平均任务数"] = round(total / len(data), 1)
    return m

def run(prompt_template: str, sg: dict) -> str:
    prompt = prompt_template.format(
        goal=goal_text(sg), domain=sg["domain"], weeks=sg["duration_weeks"])
    return eval_llm.invoke([HumanMessage(content=prompt)]).content

if __name__ == "__main__":
    versions = {"v1朴素": WEEKLY_V1, "v2加约束": WEEKLY_V2, "v3加示例": WEEKLY_V3}
    rows = []
    for vname, tpl in versions.items():
        for sg in TEST_GOALS:
            out = run(tpl, sg)
            m = score(out, sg["duration_weeks"])
            rows.append((vname, sg["title"], m))
            print(f"{vname:8s} | {sg['title']:12s} | {m}")
