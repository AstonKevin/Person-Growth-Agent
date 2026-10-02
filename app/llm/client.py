"""LLM 接入层：统一封装调用与异常处理

上层业务（routers/agent）不直接接触 anthropic SDK：
- 调用入口只有一个 chat()，换模型/换中转/换 SDK 只改这里
- SDK 异常在此统一映射为项目自定义异常，格式化返回交给全局 handler
"""
import anthropic

from ..core.config import get_settings
from ..core.exceptions import (
    LLMAuthError,
    LLMError,
    LLMRateLimitError,
    LLMTimeoutError,
)

_settings = get_settings()

# SDK 内置重试：仅对连接错误/429/5xx 自动指数退避重试，无需手写循环
client = anthropic.Anthropic(
    api_key=_settings.llm_api_key,
    base_url=_settings.llm_base_url,
    timeout=_settings.llm_timeout,
    max_retries=_settings.llm_max_retries,
)


def chat(
    system: str,
    messages: list[dict],
    prefill: str | None = None,
    max_tokens: int = 1024,
) -> str:
    """统一的 LLM 调用入口，返回纯文本

    参数：
    - system: 系统提示词（角色设定/输出规则）
    - messages: [{"role": "user", "content": "..."}] 的消息列表
    - prefill: 预填充内容（如传 "{" 强制模型输出 JSON），会拼回返回文本
    - max_tokens: 单次响应的最大 token 数
    """
    final_messages = list(messages)
    if prefill:
        # 预填充 = 在 assistant 侧先塞一段文字，模型只能接着它往下写
        final_messages.append({"role": "assistant", "content": prefill})

    try:
        response = client.messages.create(
            model=_settings.llm_model,
            max_tokens=max_tokens,
            system=system,
            messages=final_messages,
        )
    except anthropic.APITimeoutError as e:
        raise LLMTimeoutError() from e
    except anthropic.RateLimitError as e:
        # 走到这里说明 SDK 已重试 max_retries 次仍被限流
        raise LLMRateLimitError() from e
    except anthropic.AuthenticationError as e:
        # Key 无效，重试无意义，直接暴露为配置问题
        raise LLMAuthError() from e
    except anthropic.APIConnectionError as e:
        # 网络连不上中转服务器（含 DNS 失败、拒绝连接）
        raise LLMError() from e
    except anthropic.APIStatusError as e:
        # 其余 4xx/5xx 兜底
        raise LLMError() from e

    text = _extract_text(response.content)
    return prefill + text if prefill else text


def _extract_text(content) -> str:
    """从响应的 content blocks 中提取纯文本"""
    texts = [block.text for block in content if block.type == "text"]
    return "".join(texts).strip()
