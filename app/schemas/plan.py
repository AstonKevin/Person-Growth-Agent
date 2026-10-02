"""计划模块 schema：CRUD 用 + AI 拆解用（从 goal.py 迁移）"""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


# ========== 计划 CRUD ==========
class PlanBase(BaseModel):
    goal: str = Field(..., min_length=2, max_length=255)
    daily_plan: str
    week_plan: str
    start_time: datetime
    end_time: datetime


class PlanCreate(PlanBase):
    """创建计划请求体"""
    status: str = Field(default="未完成", description="计划状态")


class PlanUpdate(BaseModel):
    """更新计划请求体：所有字段可选，只传要改的"""
    goal: Optional[str] = Field(None, min_length=2, max_length=255)
    daily_plan: Optional[str] = None
    week_plan: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    status: Optional[str] = None


class PlanOut(PlanBase):
    """计划响应体：status 对外暴露，内部映射到模型的 stauts 字段"""
    id: int
    user_id: int
    create_time: datetime
    update_time: Optional[datetime] = None
    status: str = Field(validation_alias="stauts")  # 从 ORM 的 stauts 列读取

    model_config = ConfigDict(from_attributes=True)


class PlanList(BaseModel):
    """分页列表响应"""
    total: int
    skip: int
    limit: int
    items: list[PlanOut]


# ========== AI 目标拆解（从 schemas/goal.py 迁移） ==========
class GoalIn(BaseModel):
    """目标拆解请求体"""
    goal: str = Field(min_length=2, max_length=255, description="用户的成长目标")


class WeekPlan(BaseModel):
    """单周计划"""
    week: int = Field(description="第几周，从 1 开始")
    focus: str = Field(description="本周核心目标")
    tasks: list[str] = Field(description="本周具体任务清单")


class GoalOut(BaseModel):
    """目标拆解结果"""
    goal: str
    summary: str = Field(description="一句话总结拆解思路")
    weeks: list[WeekPlan]
