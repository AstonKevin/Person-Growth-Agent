"""用户路由：受保护接口，必须携带有效 JWT"""
from fastapi import APIRouter, Depends

from app.schemas.user import UserOut
from app.deps import get_current_user
from app.models.user import User

router = APIRouter(prefix="/users", tags=["用户"])


@router.get("/me", response_model=UserOut)
def get_me(current_user: User = Depends(get_current_user)):
    """获取当前登录用户信息——必须带有效 token，不带返回 401"""
    return current_user
