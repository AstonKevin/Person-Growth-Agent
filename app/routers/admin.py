"""管理后台路由：用户管理与综合用量审计。

权限：所有接口必须携带管理员 JWT（role == 'admin'），否则统一返回 403。
铁律：管理员可看用量与状态，不可看明文 key、不可看用户目标/对话内容。
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_admin
from app.models.user import User
from app.schemas.admin import (
    UserAdminOut,
    UserAdminUpdate,
    UsageSummary,
    ModelUsageStat,
    DailyUsageStat,
)
from app.crud.user import (
    get_user_by_id,
    get_users_with_provider_flag,
    update_user_admin,
    count_user_stats,
)
from app.crud.provider import get_provider_by_user
from app.crud import usage as usage_crud
from app.core.exceptions import NotFoundError, ConflictError

router = APIRouter(prefix="/admin", tags=["管理后台"], dependencies=[Depends(get_current_admin)])


@router.get("/users", response_model=list[UserAdminOut])
def list_users(
    skip: int = 0,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """获取用户列表（含 BYOK 配置标识）。"""
    return get_users_with_provider_flag(db, skip=skip, limit=limit)


@router.patch("/users/{user_id}", response_model=UserAdminOut)
def update_user(
    user_id: int,
    payload: UserAdminUpdate,
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """管理员修改用户状态或角色。

    防御规则：禁止管理员停用自身或降低自身为普通用户，避免系统失锁。
    """
    target = get_user_by_id(db, user_id)
    if not target:
        raise NotFoundError("用户不存在")

    if current_admin.id == user_id:
        if payload.is_active is False:
            raise ConflictError("禁止停用当前登录的管理员账号")
        if payload.role is not None and payload.role != "admin":
            raise ConflictError("禁止取消自身的管理员权限")

    updated = update_user_admin(
        db, user_id, role=payload.role, is_active=payload.is_active
    )
    provider = get_provider_by_user(db, user_id)
    return {
        "id": updated.id,
        "name": updated.name,
        "role": updated.role,
        "is_active": updated.is_active,
        "create_time": updated.create_time,
        "has_provider": provider is not None,
    }


@router.get("/stats", response_model=UsageSummary)
def get_stats(
    db: Session = Depends(get_db),
    current_admin: User = Depends(get_current_admin),
):
    """获取系统综合用量大屏统计（汇总、错误率、按模型聚合、近期趋势）。"""
    user_stats = count_user_stats(db)
    totals = usage_crud.total_counts(db)
    models = usage_crud.counts_by_model(db)
    daily = usage_crud.counts_by_day(db, days=30)

    total_req = totals["total"]
    err_req = totals["error"]
    error_rate = round(err_req / total_req, 4) if total_req > 0 else 0.0

    return UsageSummary(
        total_users=user_stats["total"],
        active_users=user_stats["active"],
        total_requests=total_req,
        success_requests=totals["success"],
        error_requests=err_req,
        error_rate=error_rate,
        prompt_tokens=totals["prompt_tokens"],
        completion_tokens=totals["completion_tokens"],
        by_model=[
            ModelUsageStat(
                model=m["model"],
                count=m["count"],
                prompt_tokens=m["prompt_tokens"],
                completion_tokens=m["completion_tokens"],
            )
            for m in models
        ],
        recent_trend=[
            DailyUsageStat(
                day=d["day"],
                total=d["total"],
                error=d["error"],
            )
            for d in daily
        ],
    )
