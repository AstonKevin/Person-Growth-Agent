"""调整规则库加载器：读取项目根 rules.yaml，进程内缓存。

规则集中在 YAML，代码只负责"怎么用规则"，规则"是什么"由配置决定。
"""
from functools import lru_cache
from pathlib import Path

import yaml


def _rules_path() -> Path:
    # 本文件在 app/core/rules.py，项目根是上两级
    return Path(__file__).resolve().parents[2] / "rules.yaml"


@lru_cache
def get_rules() -> dict:
    """加载并缓存 rules.yaml；只读取一次"""
    path = _rules_path()
    if not path.exists():
        raise FileNotFoundError(f"找不到规则文件：{path}")
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError(f"规则文件格式错误：{path}")
    return data


def reload_rules() -> dict:
    """改了 rules.yaml 后手动刷新（清缓存重读）"""
    get_rules.cache_clear()
    return get_rules()
