"""打卡模块 schema"""
from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, Field, ConfigDict


class CheckInCreate(BaseModel):
    """创建打卡请求体"""
    plan_id: int = Field(..., description="所属计划 id")
    task_type: str = Field(..., min_length=1, max_length=50, description="打卡类型：学习/运动/...")
    duration: Optional[float] = Field(None, ge=0, description="时长（小时）")
    intensity: Optional[int] = Field(None, ge=1, le=5, description="强度 1-5")
    checkin_date: Optional[date] = Field(None, description="打卡日期，默认今天")


class CheckInOut(BaseModel):
    """打卡响应体"""
    id: int
    plan_id: int
    user_id: int
    checkin_date: date
    task_type: str
    duration: Optional[float] = None
    intensity: Optional[int] = None

    model_config = ConfigDict(from_attributes=True)


class CheckInList(BaseModel):
    """分页列表响应"""
    total: int
    skip: int
    limit: int
    items: list[CheckInOut]


class CheckInStats(BaseModel):
    """按计划统计"""
    plan_id: int
    total_checkins: int = Field(description="打卡总次数")
    total_duration: float = Field(description="总时长（小时）")
    avg_intensity: Optional[float] = Field(None, description="平均强度")
    checkin_days: int = Field(description="打卡天数（去重）")
    completion_rate: float = Field(description="完成率：打卡天数/计划总天数 × 100")
