"""正式台词生成与辅助规划使用的 Ollama 模型实例。

两个实例复用同一基础模型和 Context 窗口，但采用不同的采样、输出长度和格式约束：
正式模型追求自然角色台词，Planner 模型追求短小、确定且可解析的 JSON 决策。
"""

import os

from langchain_ollama import ChatOllama

OLLAMA_BASE_URL = os.getenv("NPC_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL_NAME = os.getenv("NPC_MODEL_NAME", "qwen3:14b")


def _env_bool(name: str, default: bool = False) -> bool:
    """把常见环境变量布尔写法解析为 bool，缺失时使用显式默认值。"""
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}

# 正式回答允许少量温度以保持角色表达自然；num_predict 和 num_ctx 同时约束延迟与上下文成本。
llm = ChatOllama(
    model=MODEL_NAME,
    base_url=OLLAMA_BASE_URL,
    temperature=float(os.getenv("NPC_MODEL_TEMPERATURE", "0.2")),
    num_predict=int(os.getenv("NPC_MODEL_NUM_PREDICT", "256")),
    num_ctx=int(os.getenv("NPC_MODEL_NUM_CTX", "2048")),
    reasoning=_env_bool("NPC_MODEL_REASONING"),
    # 本地 Ollama 不继承系统 HTTP 代理，避免 localhost 请求被错误转发到代理服务器。
    client_kwargs={"trust_env": False},
)

# Planner 关闭随机性与 reasoning，并强制 JSON；它只输出策略，不直接生成玩家可见台词。
planner_llm = ChatOllama(
    model=MODEL_NAME,
    base_url=OLLAMA_BASE_URL,
    temperature=0.0,
    num_predict=int(os.getenv("NPC_PLANNER_NUM_PREDICT", "128")),
    num_ctx=int(os.getenv("NPC_MODEL_NUM_CTX", "2048")),
    reasoning=False,
    format="json",
    client_kwargs={"trust_env": False},
)
