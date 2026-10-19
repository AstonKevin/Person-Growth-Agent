"""
agents 公共组件：LLM 工厂、JSON 解析。
- 平台默认 make_llm：从 .env 读取（provider=openai 时远程为主、本地 Ollama 降级）。
- 每用户 make_llm_for_user：优先用用户自带的 UserProvider（BYOK），否则走平台默认。
"""
import json
import time
import logging

from langchain_ollama import ChatOllama
from app.core.config import get_settings

logger = logging.getLogger(__name__)


def _normalize_openai_base(base_url: str) -> str:
    """OpenAI SDK 要求 base_url 带 /v1（它自己只拼 /chat/completions）。"""
    return (base_url or "").rstrip("/") + "/v1"


def _openai_chat(api_key, base_url, model, temperature):
    """构造 ChatOpenAI；统一补 /v1，超时/重试从配置读。"""
    from langchain_openai import ChatOpenAI
    s = get_settings()
    return ChatOpenAI(
        api_key=api_key,
        base_url=_normalize_openai_base(base_url),
        model=model,
        temperature=temperature,
        timeout=s.llm_timeout,
        max_retries=s.llm_max_retries,
    )


def make_llm(temperature: float | None = None):
    """平台默认 LLM；provider=openai 时远程为主、本地 Ollama 自动降级。"""
    s = get_settings()
    temp = temperature if temperature is not None else s.agent_temperature

    local = ChatOllama(
        base_url=s.ollama_base_url,
        model=s.ollama_model,
        temperature=temp,
    )

    if s.llm_provider == "openai":
        primary = _openai_chat(s.llm_api_key, s.llm_base_url, s.llm_model, temp)
        # 远程失败 → 自动降级本地 Ollama
        return primary.with_fallbacks([local])

    return local


def make_llm_for_provider(base_url, api_key, model, temperature: float | None = None):
    """按用户提交的凭证构造 ChatOpenAI（不做 Ollama 降级）。"""
    s = get_settings()
    temp = temperature if temperature is not None else s.agent_temperature
    return _openai_chat(api_key, base_url, model, temp)


def resolve_user_channel(db, user, temperature: float | None = None):
    """解析当前用户的 LLM 通道，返回 (llm, model, provider)：
    - 配置了 UserProvider → 解密 key，用其自带凭证；
    - 没配置 → 平台默认通道（.env 模型）。
    """
    from app.crud.provider import get_provider_by_user
    from app.core.crypto import decrypt

    cfg = get_provider_by_user(db, user.id)
    if cfg:
        llm = make_llm_for_provider(
            cfg.base_url, decrypt(cfg.encrypted_api_key), cfg.model, temperature
        )
        return llm, cfg.model, cfg.provider
    s = get_settings()
    return make_llm(temperature), s.llm_model, s.llm_provider


def make_llm_for_user(db, user, temperature: float | None = None):
    """只返回当前用户该用的 LLM（不带 model/provider 信息）。"""
    return resolve_user_channel(db, user, temperature)[0]


def tracked_chat(llm, messages, *, user_id: int, model: str, provider: str | None = None):
    """调用 LLM 并记录用量（计时 / token / 错误）。

    best-effort：记录失败只记日志，绝不影响主流程；业务异常照常向上抛。
    """
    t0 = time.perf_counter()
    status_name, error_code = "success", None
    prompt_tokens = completion_tokens = None
    try:
        resp = llm.invoke(messages)
        try:
            um = getattr(resp, "usage_metadata", None) or {}
            prompt_tokens = um.get("input_tokens")
            completion_tokens = um.get("output_tokens")
        except Exception:
            pass
        return resp
    except Exception as exc:
        status_name, error_code = "error", type(exc).__name__
        raise
    finally:
        latency_ms = int((time.perf_counter() - t0) * 1000)
        try:
            from app.database import SessionLocal
            from app.crud.usage import add_usage
            db = SessionLocal()
            try:
                add_usage(
                    db, user_id=user_id, model=model, provider=provider,
                    status=status_name, prompt_tokens=prompt_tokens,
                    completion_tokens=completion_tokens, latency_ms=latency_ms,
                    error_code=error_code,
                )
            finally:
                db.close()
        except Exception:
            logger.exception("用量记录写入失败（不影响主流程）")


def parse_json(text: str):
    """从 LLM 输出解析 JSON，兼容 ```json 代码块，失败返回 None"""
    text = (text or "").strip()
    # 先尝试从代码块里提取
    if "```" in text:
        for part in text.split("```"):
            p = part.strip()
            if p.lower().startswith("json"):
                p = p[4:].strip()
            if p.startswith("[") or p.startswith("{"):
                try:
                    return json.loads(p)
                except Exception:
                    pass
    # 再尝试直接解析
    try:
        return json.loads(text)
    except Exception:
        return None
