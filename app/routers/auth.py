"""认证路由：注册 + 登录（OAuth2 表单 + JWT 签发）"""
from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database import get_db
from app.schemas.user import UserCreate, UserOut, Token
from app.crud.user import get_user_by_name, create_user
from app.core.security import verify_password, create_access_token
from app.core.exceptions import ConflictError, UnauthorizedError

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    """用户注册：用户名唯一，密码哈希存储"""
    if get_user_by_name(db, user_in.name):
        raise ConflictError("用户名已存在")
    return create_user(db, user_in)


@router.post("/login", response_model=Token)
def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """用户登录：OAuth2 表单格式（username/password 字段），成功返回 JWT"""
    # OAuth2 表单字段固定叫 username，映射到我们的 name 字段
    user = get_user_by_name(db, form_data.username)
    # 用户名不存在或密码错误都返回同一个错误，防用户枚举
    if not user or not verify_password(form_data.password, user.password_hash):
        raise UnauthorizedError("用户名或密码错误")
    access_token = create_access_token(subject=str(user.id))
    return {"access_token": access_token, "token_type": "bearer"}
