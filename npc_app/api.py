"""FastAPI 路由与单轮 NPC 请求的最外层编排。

本层负责 HTTP 契约、认证依赖、线程归属、上下文预取、NDJSON 响应和成功回合落库；
意图、检索、Prompt 与答案守卫委托给 dialogue 编排层完成。
"""

import json
import os
import socket
from collections.abc import Callable
from typing import Annotated, Any
from urllib.parse import urlparse

import httpx
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import text
from sqlalchemy.orm import Session

from npc_app import __version__
from npc_app.contracts import LoginRequest, RegisterRequest, TokenResponse, UnityNpcTurnRequest
from npc_app.database import NpcUser, engine, get_db
from npc_app.dialogue.orchestrator import NpcRagStreamState, run_npc_rag_chat_stream
from npc_app.services.auth_service import authenticate_user, create_access_token, get_current_user, register_user
from npc_app.services.chat_service import (
    get_or_create_chat_thread,
    list_thread_records,
    save_chat_record,
)
from npc_app.services.llm_service import MODEL_NAME, OLLAMA_BASE_URL
from npc_app.services.memory_milvus_service import MEMORY_COLLECTION_NAME, validate_memory_collection
from npc_app.services.memory_service import (
    get_memory_context,
    maybe_update_thread_memory,
    record_confirmed_turn_memories,
)
from npc_app.services.milvus_retriever_service import COLLECTION_NAME, MILVUS_URI, get_milvus_client
from npc_app.trace import NpcTurnTrace, emit_trace

router = APIRouter()
DbSession = Annotated[Session, Depends(get_db)]
CurrentUser = Annotated[NpcUser, Depends(get_current_user)]
DEPENDENCY_CHECK_TIMEOUT_SECONDS = float(os.getenv("NPC_DEPENDENCY_CHECK_TIMEOUT_SECONDS", "2.0"))


@router.post("/v1/npc/chat/stream", tags=["npc"])
def unity_npc_chat_stream(
    req: UnityNpcTurnRequest,
    db: DbSession,
    current_user: CurrentUser,
):
    """处理一次已认证的 Unity NPC 对话，并以 NDJSON 事件流返回结果。

    参数 ``req`` 已通过 Pydantic 校验，``db`` 与 ``current_user`` 由 FastAPI 依赖注入。
    函数本身先同步准备线程、历史和记忆，再返回 StreamingResponse；真正的生成、事件发送
    和成功结果持久化发生在内部生成器被 ASGI 服务器迭代时。

    返回事件以换行分隔 JSON，正常顺序为 thread → status → sources → answer_delta → done。
    检索或生成失败时编排器会输出 error → done，且不会保存空答案。
    """
    thread_id = str(req.thread_id) if req.thread_id else None
    npc_id = req.npc_id.value
    # 线程同时绑定当前用户和 NPC，避免客户端复用其他用户或其他角色的上下文。
    thread = get_or_create_chat_thread(db, current_user, thread_id, npc_id)
    history_records = list_thread_records(db, thread.id)
    npc_interaction_count = len(history_records)
    # 最近对话只用于指代消解；较长期的连续性由摘要和相关记忆承担。
    dialogue_history = [
        (record.question, record.answer)
        for record in history_records[-6:]
    ]
    # 摘要提供整体连续性，相关记忆针对当前问题召回；二者稍后仍会经过 Plan 和 Context 预算裁剪。
    memory_context = get_memory_context(db, thread, req.question)

    def event_generator():
        """延迟执行对话流，并在有效成稿后完成本轮所有持久化副作用。"""
        stream_state = NpcRagStreamState()
        turn_trace = NpcTurnTrace.create(thread.id, npc_id)
        try:
            # thread 事件必须最先发送，让 Unity 在后续事件到达前保存服务端线程 ID。
            yield _thread_event(req, thread, npc_interaction_count)
            yield from run_npc_rag_chat_stream(
                req,
                stream_state=stream_state,
                dialogue_history=dialogue_history,
                memory_summary=memory_context.summary,
                memory_items=memory_context.items,
                turn_trace=turn_trace,
            )
            # 生成失败或没有有效正文时不落库，避免把不完整回合写入后续上下文。
            if not stream_state.answer.strip():
                return
            try:
                # 完整答案生成并通过守卫后，才保存问答、确认事实并按周期更新摘要。
                save_chat_record(
                    db=db,
                    thread_id=thread.id,
                    question=req.question,
                    answer=stream_state.answer.strip(),
                )
                record_confirmed_turn_memories(db, thread, req)
                maybe_update_thread_memory(db, thread)
            except Exception:
                turn_trace.error_stage = "persistence"
                raise
        finally:
            # 无论正常、短路还是异常，Trace 都在回合结束点统一发出，保证诊断记录不遗漏失败路径。
            emit_trace(turn_trace)

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _thread_event(req: UnityNpcTurnRequest, thread, npc_interaction_count: int) -> str:
    """构造首个 NDJSON 事件，向 Unity 回传线程标识和当前关系展示值。"""
    return json.dumps(
        {
            "type": "thread",
            "data": {
                "thread_id": thread.id,
                "npc_id": req.npc_id.value,
                "annoyance_percent": req.relationship.annoyance,
                "favorability_percent": req.relationship.favorability,
                "npc_interaction_count": npc_interaction_count,
            },
        },
        ensure_ascii=False,
    ) + "\n"


@router.post("/auth/register", response_model=TokenResponse, tags=["auth"])
def register(req: RegisterRequest, db: DbSession):
    """创建账号并直接签发该账号的访问 Token。"""
    user = register_user(db=db, username=req.username, password=req.password)
    return TokenResponse(access_token=create_access_token(user))


@router.post("/auth/login", response_model=TokenResponse, tags=["auth"])
def login(req: LoginRequest, db: DbSession):
    """校验账号凭据并签发访问 Token。"""
    user = authenticate_user(db=db, username=req.username, password=req.password)
    return TokenResponse(access_token=create_access_token(user))


@router.get("/health", tags=["runtime"])
def health():
    """返回进程存活状态；不探测数据库、Ollama 或 Milvus。"""
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "ollama_base_url": OLLAMA_BASE_URL,
        "app": "npc",
        "version": __version__,
    }


@router.get("/ready", tags=["runtime"])
def ready():
    """聚合所有运行依赖的只读检查；任一失败即以 HTTP 503 拒绝流量。"""
    dependencies = check_runtime_dependencies()
    ready_for_traffic = all(item["status"] == "ok" for item in dependencies.values())
    payload = {
        "status": "ready" if ready_for_traffic else "not_ready",
        "app": "npc",
        "version": __version__,
        "dependencies": dependencies,
    }
    return payload if ready_for_traffic else JSONResponse(status_code=503, content=payload)


def check_runtime_dependencies() -> dict[str, dict[str, Any]]:
    """执行轻量只读依赖检查，避免就绪探针触发模型加载或基础设施创建。"""
    return {
        "database": _checked(_check_database, "Check NPC_DATABASE_URL and the PostgreSQL service."),
        "ollama": _checked(_check_ollama, f"Start Ollama at {OLLAMA_BASE_URL} and install {MODEL_NAME}."),
        "milvus": _checked(_check_milvus, f"Start Milvus and ingest collection {COLLECTION_NAME}."),
        "memory_milvus": _checked(
            _check_memory_milvus,
            f"Run python -m scripts.init_memory_milvus for collection {MEMORY_COLLECTION_NAME}.",
        ),
    }


def _checked(check: Callable[[], dict[str, Any]], hint: str) -> dict[str, Any]:
    """把单项依赖异常转换为稳定状态结构，避免一个失败阻断其他检查结果。"""
    try:
        return {"status": "ok", **check()}
    except Exception as exc:
        return {"status": "error", "error_type": type(exc).__name__, "hint": hint}


def _check_database() -> dict[str, Any]:
    """通过最小查询确认 PostgreSQL Engine 能建立并使用连接。"""
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {}


def _check_ollama() -> dict[str, Any]:
    """确认 Ollama 可访问且配置的模型已经安装。"""
    response = httpx.get(f"{OLLAMA_BASE_URL.rstrip('/')}/api/tags", timeout=DEPENDENCY_CHECK_TIMEOUT_SECONDS)
    response.raise_for_status()
    model_names = {
        str(item.get("name", "")) for item in response.json().get("models", []) if isinstance(item, dict)
    }
    if MODEL_NAME not in model_names:
        raise RuntimeError(f"configured model is not installed: {MODEL_NAME}")
    return {"model": MODEL_NAME}


def _check_milvus() -> dict[str, Any]:
    """先探测 Milvus TCP 端点，再确认世界知识 Collection 存在。"""
    _check_tcp_endpoint(MILVUS_URI)
    collections = get_milvus_client().list_collections(timeout=DEPENDENCY_CHECK_TIMEOUT_SECONDS)
    if COLLECTION_NAME not in collections:
        raise RuntimeError(f"configured collection does not exist: {COLLECTION_NAME}")
    return {"collection": COLLECTION_NAME}


def _check_memory_milvus() -> dict[str, Any]:
    """确认 Milvus 可访问并校验长期记忆 Collection 的 Schema 与索引。"""
    _check_tcp_endpoint(MILVUS_URI)
    return validate_memory_collection()


def _check_tcp_endpoint(uri: str) -> None:
    """用短超时建立 TCP 连接，为 Milvus SDK 调用提供更清晰的网络故障边界。"""
    parsed = urlparse(uri if "://" in uri else f"tcp://{uri}")
    with socket.create_connection(
        (parsed.hostname or "127.0.0.1", parsed.port or 19530), timeout=DEPENDENCY_CHECK_TIMEOUT_SECONDS
    ):
        return
