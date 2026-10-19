"""Provider（BYOK）路由：当前用户 LLM 配置的查 / 存 / 删 + 连通测试。

所有接口都需要登录，数据按 user_id 隔离；任何响应都不回显 api_key。
"""
import logging

import openai
from fastapi import APIRouter, Depends, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.deps import get_current_user
from app.models.user import User
from app.schemas.provider import ProviderIn, ProviderOut, ProviderTestResult
from app.crud.provider import (
    get_provider_by_user,
    upsert_provider,
    delete_provider,
)
from app.agents.common import _normalize_openai_base
from app.core.exceptions import NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/providers", tags=["LLM配置"])


@router.get("/me", response_model=ProviderOut)
def get_my_provider(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """读取当前用户的配置（脱敏，不含 key）；未配置返回 404。"""
    cfg = get_provider_by_user(db, current_user.id)
    if not cfg:
        raise NotFoundError("尚未配置 LLM")
    return cfg


@router.put("/me", response_model=ProviderOut)
def save_my_provider(
    data: ProviderIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新建或更新当前用户的配置；api_key 加密后存储。"""
    return upsert_provider(db, current_user.id, data)


@router.delete("/me", status_code=status.HTTP_204_NO_CONTENT)
def remove_my_provider(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """删除当前用户的配置（之后自动回退平台默认通道）。"""
    delete_provider(db, current_user.id)


@router.post("/me/test", response_model=ProviderTestResult)
def test_my_provider(data: ProviderIn):
    """用提交的配置发一个最小 chat 请求，验证可用性（**不保存**）。

    失败也返回 200，用 ok=false + 友好错误信息呈现，便于前端在保存前拦下错误配置。
    """
    try:
        client = openai.OpenAI(
            api_key=data.api_key,
            base_url=_normalize_openai_base(data.base_url),
            timeout=20,
        )
        client.chat.completions.create(
            model=data.model,
            messages=[{"role": "user", "content": "hi"}],
            max_tokens=5,
        )
        return ProviderTestResult(ok=True)
    except Exception as exc:
        logger.info("provider 连通测试失败: %r", exc)
        return ProviderTestResult(ok=False, error=_friendly_error(exc))


def _friendly_error(exc: Exception) -> str:
    """把 OpenAI 异常映射成中文提示。"""
    if isinstance(exc, openai.AuthenticationError):
        return "API Key 无效或已过期"
    if isinstance(exc, openai.PermissionDeniedError):
        return "该 Key 没有调用此模型的权限"
    if isinstance(exc, openai.NotFoundError):
        return "模型名或地址路径不对（404）"
    if isinstance(exc, openai.RateLimitError):
        return "触发限流或额度不足"
    if isinstance(exc, openai.APITimeoutError):
        return "连接超时，请稍后重试或更换模型"
    if isinstance(exc, openai.APIConnectionError):
        return "无法连接到该地址，请检查 Base URL 和网络"
    return f"{type(exc).__name__}: {str(exc)[:200]}"
