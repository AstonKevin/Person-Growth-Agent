from datetime import datetime
from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.provider import UserProvider
from app.schemas.user import UserCreate
from app.core.security import hash_password


def get_user_by_name(db: Session, name: str) -> User | None:
    """按用户名查用户（注册查重、登录验证用）"""
    return db.query(User).filter(User.name == name).first()


def get_user_by_id(db: Session, user_id: int) -> User | None:
    """按 id 查用户（JWT 解析后取当前用户用）"""
    return db.query(User).filter(User.id == user_id).first()


def create_user(db: Session, user_in: UserCreate) -> User:
    """创建用户：密码自动哈希后存储"""
    user = User(
        name=user_in.name,
        password_hash=hash_password(user_in.password),
    )
    db.add(user)
    db.commit()
    db.refresh(user)  # 刷新后拿到自增 id
    return user


def get_users_with_provider_flag(
    db: Session, skip: int = 0, limit: int = 100
) -> list[dict]:
    """查询用户列表，并携带是否配置了 BYOK provider 的标志（单条 SQL left join）。"""
    stmt = (
        select(User, UserProvider.id.isnot(None).label("has_provider"))
        .outerjoin(UserProvider, User.id == UserProvider.user_id)
        .order_by(User.id)
        .offset(skip)
        .limit(limit)
    )
    rows = db.execute(stmt).all()
    results = []
    for user, has_provider in rows:
        results.append(
            {
                "id": user.id,
                "name": user.name,
                "role": user.role,
                "is_active": user.is_active,
                "create_time": user.create_time,
                "has_provider": bool(has_provider),
            }
        )
    return results


def update_user_admin(
    db: Session,
    user_id: int,
    *,
    role: str | None = None,
    is_active: bool | None = None,
) -> User | None:
    """管理员更新用户角色或启用/停用状态。"""
    user = get_user_by_id(db, user_id)
    if not user:
        return None
    if role is not None:
        user.role = role
    if is_active is not None:
        user.is_active = is_active
    user.update_time = datetime.now()
    db.commit()
    db.refresh(user)
    return user


def count_user_stats(db: Session) -> dict:
    """统计总用户数与活跃用户数（单条 SQL 聚合）。"""
    stmt = select(
        func.count(User.id),
        func.coalesce(func.sum(case((User.is_active.is_(True), 1), else_=0)), 0),
    )
    total, active = db.execute(stmt).one()
    return {
        "total": int(total),
        "active": int(active),
    }
