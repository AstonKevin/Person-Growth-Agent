"""
agents 公共组件：LLM 工厂、JSON 解析
三个 Agent 都复用，避免重复配置。
LLM 来源由 .env 的 LLM_PROVIDER 决定（本地 Ollama / 兼容 OpenAI 的远程）。
"""
import json
from langchain_ollama import ChatOllama
from app.core.config import get_settings


def make_llm(temperature: float | None = None):
    """统一创建模型；不传 temperature 时用配置默认值

    provider=openai 时，远程为主、本地 Ollama 作为降级（远程调用失败自动切本地）。
    """
    s = get_settings()
    temp = temperature if temperature is not None else s.agent_temperature

    local = ChatOllama(
        base_url=s.ollama_base_url,
        model=s.ollama_model,
        temperature=temp,
    )

    if s.llm_provider == "openai":
        # 延迟导入：默认本地 Ollama 不依赖 langchain_openai
        from langchain_openai import ChatOpenAI
        primary = ChatOpenAI(
            api_key=s.llm_api_key,
            base_url=s.llm_base_url,
            model=s.llm_model,
            temperature=temp,
            timeout=s.llm_timeout,
            max_retries=s.llm_max_retries,
        )
        # 远程失败 → 自动降级本地 Ollama
        return primary.with_fallbacks([local])

    return local


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
