# MVP 使用手册

本手册面向第一次跑这个项目的人。按顺序操作即可。

## 1. 环境要求

- Python 3.10+
- MySQL 8（本地已启动，库 `Person_Growth_Agent` 已建）
- Ollama（本地跑 `gemma3:4b` 模型，用于 Agent 节点）

## 2. 安装依赖

```bash
pip install -r requirements.txt
