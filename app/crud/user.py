"""用户相关数据库操作：路由层调用这里，不直接写 db.query"""
from sqlalchemy.orm import Session

from app.models.user import User
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
