"""密码哈希与 JWT 工具

密码用 pbkdf2_hmac（慢哈希，自带加盐），salt 和 hash 拼成
"salt_hex$hash_hex" 单个字符串，存进 user.password_hash 字段。
JWT 用 HS256，payload 含 sub（用户 id）和 exp（过期时间）。
"""
import hashlib
import os
from datetime import datetime, timedelta, timezone

from jose import JWTError, jwt

from .config import get_settings


# ========== 密码哈希 ==========
def hash_password(password: str) -> str:
    """明文密码 → "salt_hex$hash_hex"，可直接存进 password_hash 字段"""
    salt = os.urandom(16)
    pw_hash = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100_000)
    return f"{salt.hex()}${pw_hash.hex()}"


def verify_password(plain_password: str, stored: str) -> bool:
    """校验明文密码与存储的 salt$hash 是否匹配"""
    try:
        salt_hex, hash_hex = stored.split("$", 1)
    except ValueError:
        return False
    salt = bytes.fromhex(salt_hex)
    new_hash = hashlib.pbkdf2_hmac("sha256", plain_password.encode("utf-8"), salt, 100_000)
    return new_hash.hex() == hash_hex


# ========== JWT ==========
def create_access_token(subject: str, expires_delta: timedelta | None = None) -> str:
    """签发 access_token，subject 一般传用户 id 的字符串形式"""
    settings = get_settings()
    expire = datetime.now(timezone.utc) + (
        expires_delta or timedelta(minutes=settings.access_token_expire_minutes)
    )
    payload = {"sub": subject, "exp": expire}
    return jwt.encode(payload, settings.secret_key, algorithm=settings.algorithm)


def decode_access_token(token: str) -> dict | None:
    """解析 access_token，过期/签名错误返回 None"""
    try:
        settings = get_settings()
        return jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
    except JWTError:
        return None
