"""用户 LLM 配置（BYOK）的数据库操作：路由层调用这里，不直接写 db.query。"""
from datetime import datetime

from sqlalchemy.orm import Session

from app.models.provider import UserProvider
from app.schemas.provider import ProviderIn
from app.core.crypto import encrypt


def get_provider_by_user(db: Session, user_id: int) -> UserProvider | None:
    """读取某用户的 LLM 配置（每用户 1 条）；未配置返回 None。"""
    return db.query(UserProvider).filter(UserProvider.user_id == user_id).first()


def upsert_provider(db: Session, user_id: int, data: ProviderIn) -> UserProvider:
    """新建或更新某用户的配置；api_key 加密后落库。
    保存新配置后重置 verified（需重新验证）。"""
    cfg = get_provider_by_user(db, user_id)
    if cfg:
        cfg.provider = data.provider
        cfg.base_url = data.base_url.rstrip("/")
        cfg.encrypted_api_key = encrypt(data.api_key)
        cfg.model = data.model
        cfg.verified = False
        cfg.last_verified_at = None
        cfg.update_time = datetime.now()
    else:
        cfg = UserProvider(
            user_id=user_id,
            provider=data.provider,
            base_url=data.base_url.rstrip("/"),
            encrypted_api_key=encrypt(data.api_key),
            model=data.model,
            verified=False,
        )
        db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


def delete_provider(db: Session, user_id: int) -> bool:
    """删除某用户的配置；未配置返回 False。"""
    cfg = get_provider_by_user(db, user_id)
    if not cfg:
        return False
    db.delete(cfg)
    db.commit()
    return True
