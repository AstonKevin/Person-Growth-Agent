"""计划模块路由：CRUD + 分页筛选 + AI 目标拆解

所有接口都需要登录（get_current_user），数据按 user_id 隔离。
"""
import json
import logging

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.plan import (
    PlanCreate,
    PlanOut,
    PlanUpdate,
    PlanList,
    GoalIn,
    GoalOut,
)
from app.crud.plan import (
    get_plan,
    get_plans,
    count_plans,
    create_plan,
    update_plan,
    delete_plan,
)
from app.llm.client import chat
from app.core.exceptions import NotFoundError, LLMResponseFormatError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plans", tags=["计划"])

# AI 拆解的系统提示词
SYSTEM_PROMPT = """你是一位个人成长计划教练，负责把用户的成长目标拆解成可执行的周计划。

拆解原则：
1. 每周任务量要具体、可执行，避免"多学学""多练练"这类空话
2. 周与周之间要有递进关系，先基础后进阶

输出要求：
- 只输出 JSON，不要输出任何解释文字
- JSON 结构：
{
  "summary": "一句话总结拆解思路",
  "weeks": [
    {"week": 1, "focus": "本周核心目标", "tasks": ["任务1", "任务2", "任务3"]}
  ]
}"""


# ========== CRUD ==========
@router.post("", response_model=PlanOut, status_code=status.HTTP_201_CREATED)
def create_plan_endpoint(
    plan_in: PlanCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建计划"""
    return create_plan(db, plan_in, current_user.id)


@router.get("", response_model=PlanList)
def list_plans(
    skip: int = Query(0, ge=0, description="跳过条数"),
    limit: int = Query(20, ge=1, le=100, description="每页条数"),
    status_filter: str | None = Query(None, alias="status", description="按状态筛选"),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分页查询当前用户的计划，支持按状态筛选"""
    plans = get_plans(db, current_user.id, skip, limit, status_filter)
    total = count_plans(db, current_user.id, status_filter)
    return PlanList(total=total, skip=skip, limit=limit, items=plans)


@router.get("/{plan_id}", response_model=PlanOut)
def get_plan_endpoint(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单个计划详情（不属于当前用户则 404）"""
    plan = get_plan(db, plan_id, current_user.id)
    if not plan:
        raise NotFoundError("计划不存在或无权访问")
    return plan


@router.put("/{plan_id}", response_model=PlanOut)
def update_plan_endpoint(
    plan_id: int,
    plan_in: PlanUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """更新计划（不属于当前用户则 404）"""
    plan = get_plan(db, plan_id, current_user.id)
    if not plan:
        raise NotFoundError("计划不存在或无权访问")
    return update_plan(db, plan, plan_in)


@router.delete("/{plan_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_plan_endpoint(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除计划（不属于当前用户则 404）"""
    plan = get_plan(db, plan_id, current_user.id)
    if not plan:
        raise NotFoundError("计划不存在或无权访问")
    delete_plan(db, plan)


# ========== AI 目标拆解 ==========
@router.post("/ai-decompose", response_model=GoalOut)
def ai_decompose(
    goal_in: GoalIn,
    current_user: User = Depends(get_current_user),
):
    """输入成长目标，AI 拆解为按周计划（需登录）"""
    raw = chat(
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": goal_in.goal}],
        prefill="{",
    )
    try:
        data = json.loads(raw)
        return GoalOut.model_validate({**data, "goal": goal_in.goal})
    except (json.JSONDecodeError, ValueError) as e:
        logger.error("LLM 返回无法解析为 GoalOut: %r, 原始: %.500s", e, raw)
        raise LLMResponseFormatError() from e
