import os

from fastapi import HTTPException
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_ollama import ChatOllama


OLLAMA_BASE_URL = os.getenv("NPC_OLLAMA_BASE_URL", "http://127.0.0.1:11434")
MODEL_NAME = os.getenv("NPC_MODEL_NAME", "qwen3:latest")

llm = ChatOllama(
    model=MODEL_NAME,
    base_url=OLLAMA_BASE_URL,
    temperature=float(os.getenv("NPC_MODEL_TEMPERATURE", "0.2")),
    num_predict=int(os.getenv("NPC_MODEL_NUM_PREDICT", "1500")),
    num_ctx=int(os.getenv("NPC_MODEL_NUM_CTX", "2048")),
    client_kwargs={"trust_env": False},
)


def call_local_llm(system_prompt: str, user_prompt: str) -> str:
    try:
        response = llm.invoke(
            [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_prompt),
            ]
        )
        return response.content
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"调用本地 NPC 模型失败：{exc}")
