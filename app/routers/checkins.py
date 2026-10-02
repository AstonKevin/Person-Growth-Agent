"""打卡模块路由：创建/列表/详情/统计，全部需登录"""
from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.checkin import CheckInCreate, CheckInOut, CheckInList, CheckInStats
from app.crud.checkin import (
    get_checkin,
    get_duplicate_checkin,
    create_checkin,
    list_checkins,
    count_checkins,
    get_plan_stats,
)
from app.crud.plan import get_plan
from app.core.exceptions import NotFoundError, ConflictError

router = APIRouter(prefix="/checkins", tags=["打卡"])


@router.post("", response_model=CheckInOut, status_code=status.HTTP_201_CREATED)
def create_checkin_endpoint(
    checkin_in: CheckInCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建打卡：同一天同一计划同一类型只允许一次"""
    # 校验计划存在且属于当前用户
    plan = get_plan(db, checkin_in.plan_id, current_user.id)
    if not plan:
        raise NotFoundError("计划不存在或无权访问")

    checkin_date = checkin_in.checkin_date or date.today()
    # 重复打卡检查
    duplicate = get_duplicate_checkin(
        db, current_user.id, checkin_in.plan_id, checkin_in.task_type, checkin_date
    )
    if duplicate:
        raise ConflictError(f"该计划今日已打卡「{checkin_in.task_type}」，请勿重复打卡")

    return create_checkin(db, checkin_in, current_user.id)


@router.get("", response_model=CheckInList)
def list_checkins_endpoint(
    plan_id: int | None = Query(None, description="按计划筛选"),
    start_date: date | None = Query(None, description="起始日期"),
    end_date: date | None = Query(None, description="结束日期"),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """分页查询打卡记录，支持按计划和日期范围筛选"""
    items = list_checkins(db, current_user.id, plan_id, start_date, end_date, skip, limit)
    total = count_checkins(db, current_user.id, plan_id, start_date, end_date)
    return CheckInList(total=total, skip=skip, limit=limit, items=items)


@router.get("/stats/plan/{plan_id}", response_model=CheckInStats)
def get_plan_stats_endpoint(
    plan_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按计划统计打卡：次数/总时长/平均强度/打卡天数/完成率"""
    stats = get_plan_stats(db, plan_id, current_user.id)
    if stats is None:
        raise NotFoundError("计划不存在或无权访问")
    return stats


@router.get("/{checkin_id}", response_model=CheckInOut)
def get_checkin_endpoint(
    checkin_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """获取单条打卡详情（不属于当前用户则 404）"""
    checkin = get_checkin(db, checkin_id, current_user.id)
    if not checkin:
        raise NotFoundError("打卡记录不存在或无权访问")
    return checkin
