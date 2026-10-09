"""
agents 公共组件：LLM 工厂、JSON 解析
两个 Agent 都复用，避免重复配置
"""
import json
from langchain_ollama import ChatOllama


def make_llm(temperature: float = 0.3):
    """统一创建本地 Ollama 模型"""
    return ChatOllama(
        base_url="http://localhost:11434",
        model="gemma3:4b",
        temperature=temperature,
    )


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
