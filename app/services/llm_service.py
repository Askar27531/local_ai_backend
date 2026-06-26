# ============================================================================
# LLM 服务模块
# 功能：配置和管理本地 LLM 模型（通过 Ollama）
# ============================================================================

import re

from fastapi import HTTPException
from langchain_ollama import ChatOllama
from langchain_core.messages import HumanMessage, SystemMessage


# ============================================================================
# Ollama 服务配置
# ============================================================================
# Ollama 是一个本地 LLM 运行框架
# 这里配置 Ollama 服务的地址
OLLAMA_BASE_URL = "http://127.0.0.1:11434"

# 使用的模型名称
# "qwen3:latest" 表示使用通义千问 3 模型的最新版本
MODEL_NAME = "qwen3:latest"

# ============================================================================
# 创建 LLM 实例
# ============================================================================
# ChatOllama 是 LangChain 提供的 Ollama 客户端
# 这个实例会被整个应用共享使用
llm = ChatOllama(
    model=MODEL_NAME,  # 使用的模型
    base_url=OLLAMA_BASE_URL,  # Ollama 服务地址
    temperature=0.2,  # 温度参数（0-1）：越低越确定，越高越随机。0.2 表示相对确定的回答
    num_predict=1500,  # 最多生成 1500 个 token（词元）
    num_ctx=2048,  # 上下文窗口大小（能记住多少历史信息）
    client_kwargs={"trust_env": False},  # 不信任环境变量中的代理设置
)


# ============================================================================
# 调用本地 LLM 函数
# ============================================================================
def call_local_llm(system_prompt: str, user_prompt: str) -> str:
    """
    调用本地 LLM 模型生成回答。
    
    这是一个简单的 LLM 调用接口，用于直接调用模型。
    （注：项目的主要流程使用 Agent 服务，这个函数是备用接口）
    
    参数：
        system_prompt: 系统提示词（告诉模型它的角色和行为）
        user_prompt: 用户提示词（用户的问题或指令）
    
    返回：
        模型生成的回答文本
    
    异常：
        HTTPException: 如果调用模型失败，抛出 HTTP 500 错误
    
    示例：
        system_prompt = "你是一个 Python 专家"
        user_prompt = "什么是 Python？"
        answer = call_local_llm(system_prompt, user_prompt)
    """
    try:
        # 调用 LLM 模型
        # llm.invoke() 接收一个消息列表
        response = llm.invoke([
            # 系统消息：定义模型的角色和行为
            SystemMessage(content=system_prompt),
            # 用户消息：用户的问题或指令
            HumanMessage(content=user_prompt),
        ])
        # 提取响应内容并移除思考标签
        return response.content
    except Exception as e:
        # 如果调用失败，抛出 HTTP 500 错误
        raise HTTPException(status_code=500, detail=f"调用本地模型失败：{str(e)}")

