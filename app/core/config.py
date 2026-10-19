from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """项目全局配置：从 .env 读取，避免密码/密钥硬编码进代码"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # 数据库
    database_url: str

    # LLM 接入
    llm_api_key: str
    llm_base_url: str = "https://api.teamorouter.cn"
    llm_model: str = "glm-5.3-flash"
    llm_timeout: float = 30.0
    llm_max_retries: int = 2

    # JWT 认证
    secret_key: str = "dev-only-secret-change-in-production"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30

    # BYOK 凭证加密：不填则从 SECRET_KEY 派生（生产建议显式设置独立的合法 Fernet key）
    credential_encryption_key: str | None = None

    # LangGraph agents 的模型选择（本地 Ollama / 兼容 OpenAI 的远程）
    llm_provider: str = "ollama"
    ollama_base_url: str = "http://localhost:11434"
    ollama_model: str = "gemma3:4b"
    agent_temperature: float = 0.3


@lru_cache
def get_settings() -> Settings:
    """单例配置：进程内只读一次 .env，所有模块复用同一份"""
    return Settings()
