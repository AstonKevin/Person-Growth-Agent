from pydantic import BaseModel, ConfigDict, Field


class UserCreate(BaseModel):
    """注册请求体：与 user 表字段对齐（阶段2只做 name + password）"""
    name: str = Field(..., min_length=2, max_length=50, description="用户名")
    password: str = Field(..., min_length=6, max_length=128, description="密码")


class UserOut(BaseModel):
    """用户响应体：故意不含 password 字段，响应不泄露密码"""
    id: int
    name: str

    model_config = ConfigDict(from_attributes=True)


class Token(BaseModel):
    """登录成功返回的 token"""
    access_token: str
    token_type: str = "bearer"
