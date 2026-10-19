"""凭证加解密：用户自带的 API key 用 Fernet 对称加密后存库。

密钥来源（优先级）：
1. 环境变量 CREDENTIAL_ENCRYPTION_KEY（必须是合法 Fernet key，32 字节 base64）；
2. 未设置时从 SECRET_KEY 派生（SHA256 → urlsafe base64），并告警。

铁律：密钥只存在于环境变量，不进数据库、不进前端；任何接口不回显明文。
"""
import base64
import hashlib
import logging
from functools import lru_cache

from cryptography.fernet import Fernet

from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _derive_key(secret: str) -> bytes:
    """从任意字符串派生一个合法的 32 字节 Fernet key（确定性）。"""
    digest = hashlib.sha256(secret.encode("utf-8")).digest()  # 32 bytes
    return base64.urlsafe_b64encode(digest)


@lru_cache
def get_fernet_key() -> bytes:
    """返回 Fernet 密钥：优先用显式配置，否则从 SECRET_KEY 派生。"""
    s = get_settings()
    if s.credential_encryption_key:
        return s.credential_encryption_key.encode("utf-8")
    logger.warning(
        "未设置 CREDENTIAL_ENCRYPTION_KEY，已从 SECRET_KEY 派生凭证加密密钥；"
        "建议生产环境显式设置独立密钥。"
    )
    return _derive_key(s.secret_key)


@lru_cache
def get_fernet() -> Fernet:
    """构造 Fernet（进程内复用）。"""
    return Fernet(get_fernet_key())


def encrypt(plaintext: str) -> str:
    """加密明文，返回字符串 token。"""
    if not plaintext:
        raise ValueError("待加密内容不能为空")
    return get_fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """解密 token，返回明文；token 非法抛 cryptography.fernet.InvalidToken。"""
    if not token:
        raise ValueError("待解密内容不能为空")
    return get_fernet().decrypt(token.encode("utf-8")).decode("utf-8")
