"""跨模块复用的依赖：鉴权、分页等"""
from fastapi import Depends
from fastapi.security import OAuth2PasswordBearer
from sqlalchemy.orm import Session

from app.database import get_db
from app.core.security import decode_access_token
from app.crud.user import get_user_by_id
from app.models.user import User
from app.core.exceptions import UnauthorizedError

# tokenUrl 指向登录接口地址，Swagger 的 Authorize 按钮靠它自动获取 token
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db),
) -> User:
    """从请求头 Authorization: Bearer <token> 解析出当前用户
    任何一步失败都返回 401，路由函数不会执行"""
    payload = decode_access_token(token)
    if not payload or "sub" not in payload:
        raise UnauthorizedError("无效的认证凭证")
    user = get_user_by_id(db, int(payload["sub"]))
    if not user:
        raise UnauthorizedError("用户不存在")
    return user
