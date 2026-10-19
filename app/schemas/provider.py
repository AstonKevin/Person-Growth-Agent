"""BYOK 相关的 Pydantic 模型。"""
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ProviderIn(BaseModel):
    """用户提交的 LLM 配置（保存 PUT /me、测试 POST /me/test 都用它）。

    MVP 每次都要求重填 api_key，不支持"只改地址、保留旧 key"。
    """
    provider: str = Field("openai", max_length=20, description="服务商类型，MVP 固定 openai")
    base_url: str = Field(..., max_length=255, description="如 https://api.openai.com（不带 /v1）")
    api_key: str = Field(..., min_length=1, max_length=255, description="API Key（仅传输，绝不回显）")
    model: str = Field(..., max_length=100, description="模型名，如 gpt-4o-mini")


class ProviderOut(BaseModel):
    """返回给前端的配置：绝不包含 api_key / 密文。"""
    provider: str
    base_url: str
    model: str
    verified: bool
    last_verified_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class ProviderTestResult(BaseModel):
    """连通测试结果：测试失败也返回 200，用 ok 表示，便于前端展示。"""
    ok: bool
    error: str | None = None
