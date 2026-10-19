"""管理后台相关的 Pydantic 模型。

严格铁律：管理员可看用量与状态，不可看明文 key、不可看用户目标/对话内容。
"""
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field


class UserAdminOut(BaseModel):
    """管理后台用户列表项：含角色、状态、是否配置 BYOK，绝不含密码哈希与密钥。"""
    id: int
    name: str
    role: str
    is_active: bool
    create_time: datetime
    has_provider: bool

    model_config = ConfigDict(from_attributes=True)


class UserAdminUpdate(BaseModel):
    """管理员修改用户状态或角色。"""
    role: str | None = Field(None, pattern="^(user|admin)$", description="角色：user 或 admin")
    is_active: bool | None = Field(None, description="是否启用")


class ModelUsageStat(BaseModel):
    """单个模型的用量聚合。"""
    model: str
    count: int
    prompt_tokens: int
    completion_tokens: int


class DailyUsageStat(BaseModel):
    """近 N 天某单日的用量统计。"""
    day: str
    total: int
    error: int


class UsageSummary(BaseModel):
    """系统综合用量大屏统计。"""
    total_users: int
    active_users: int
    total_requests: int
    success_requests: int
    error_requests: int
    error_rate: float
    prompt_tokens: int
    completion_tokens: int
    by_model: list[ModelUsageStat]
    recent_trend: list[DailyUsageStat]
