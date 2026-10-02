import pytest
from pydantic import ValidationError
from langchain_core.messages import HumanMessage
from app.agents.goal_breakdown import extract_structured_goal, StructuredGoal


def test_clean_json_ok():
    """干净 JSON 能正确解析"""
    text = '{"title":"拿实习","domain":"求职","duration_weeks":12,"success_criteria":["拿到offer"],"constraints":["每天2小时"]}'
    sg = extract_structured_goal("3个月拿实习", text)
    assert sg.duration_weeks == 12
    assert sg.domain == "求职"

def test_json_with_markdown_fence_ok():
    """带 ```json ... ``` 包裹也能解析"""
    text = '```json\n{"title":"健身","domain":"健身","duration_weeks":26,"success_criteria":["瘦10斤"],"constraints":[]}\n```'
    sg = extract_structured_goal("半年健身", text)
    assert sg.duration_weeks == 26

def test_missing_field_raises():
    """缺字段 → 校验失败"""
    text = '{"title":"x"}'   # 缺 domain/duration_weeks 等
    with pytest.raises(ValidationError):
        extract_structured_goal("随便", text)

def test_weeks_out_of_range_raises():
    """周数离谱（如 -3 或 9999）→ 被 Field(ge=1, le=100) 挡掉"""
    bad = '{"title":"x","domain":"求职","duration_weeks":-3,"success_criteria":[],"constraints":[]}'
    with pytest.raises(ValidationError):
        extract_structured_goal("随便", bad)

# 这层慢、依赖本地 Ollama，跑的时候加 -m integration

@pytest.mark.integration
@pytest.mark.parametrize("raw, expect_domain_kw, weeks_range", [
    ("3个月内拿到暑期实习offer", "求职", (8, 16)),
    ("半年内通过英语六级", "学习", (20, 32)),
    ("一个月减5公斤", "健身", (3, 6)),
])
def test_real_goal_accuracy(raw, expect_domain_kw, weeks_range):
    from app.agents.goal_breakdown import llm, PARSE_GOAL_PROMPT
    resp = llm.invoke([HumanMessage(content=PARSE_GOAL_PROMPT.format(goal=raw))])
    sg = extract_structured_goal(raw, resp.content)
    assert sg.title.strip() != ""
    assert weeks_range[0] <= sg.duration_weeks <= weeks_range[1], \
        f"{raw} 推出 {sg.duration_weeks}周，超出合理区间"
