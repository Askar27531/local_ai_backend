import json
import logging

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from npc_app.db.database import get_db, init_db
from npc_app.db.models import NpcUser
from npc_app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from npc_app.schemas.chat import SearchItem
from npc_app.schemas.thread import ChatRecordItem, ThreadResponse
from npc_app.services.auth_service import (
    authenticate_user,
    create_access_token,
    get_current_user,
    register_user,
)
from npc_app.services.llm_service import MODEL_NAME, OLLAMA_BASE_URL
from npc_app.services.memory_service import get_memory_context, maybe_update_thread_memory
from npc_app.services.record_service import save_chat_record
from npc_app.services.thread_service import (
    get_or_create_chat_thread,
    list_thread_records,
    list_user_threads,
    maybe_update_thread_title,
)
from npc_app.schemas.npc_chat import NpcChatRequest
from npc_app.services.npc_rag_service import NpcRagStreamState, run_npc_rag_chat_stream


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)

app = FastAPI(
    title="Guichao Island NPC Backend",
    description="FastAPI backend for RAG-powered NPC chat.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
def startup():
    init_db()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "ollama_base_url": OLLAMA_BASE_URL,
        "app": "npc",
    }


@app.post("/auth/register", response_model=TokenResponse)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    user = register_user(db=db, username=req.username, password=req.password)
    return TokenResponse(access_token=create_access_token(user))


@app.post("/auth/login", response_model=TokenResponse)
def login(req: LoginRequest, db: Session = Depends(get_db)):
    user = authenticate_user(db=db, username=req.username, password=req.password)
    return TokenResponse(access_token=create_access_token(user))


@app.get("/auth/me", response_model=UserResponse)
def me(current_user: NpcUser = Depends(get_current_user)):
    return UserResponse(id=current_user.id, username=current_user.username)


def _thread_to_response(thread) -> ThreadResponse:
    return ThreadResponse(
        id=thread.id,
        npc_id=thread.npc_id,
        title=thread.title,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


@app.get("/threads", response_model=list[ThreadResponse])
def get_threads(
    db: Session = Depends(get_db),
    current_user: NpcUser = Depends(get_current_user),
):
    return [_thread_to_response(t) for t in list_user_threads(db, current_user)]


@app.get("/threads/{thread_id}/records", response_model=list[ChatRecordItem])
def get_thread_records(
    thread_id: str,
    db: Session = Depends(get_db),
    current_user: NpcUser = Depends(get_current_user),
):
    records = list_thread_records(db, current_user, thread_id)
    return [
        ChatRecordItem(
            id=record.id,
            question=record.question,
            answer=record.answer,
            model=record.model,
            used_search=record.used_search,
            sources=[
                SearchItem(title=s.title, url=s.url, snippet=s.snippet)
                for s in record.sources
            ],
            created_at=record.created_at,
        )
        for record in records
    ]


@app.post("/npc/chat/stream")
def npc_chat_stream(
    req: NpcChatRequest,
    db: Session = Depends(get_db),
    current_user: NpcUser = Depends(get_current_user),
):
    thread = get_or_create_chat_thread(db, current_user, req.thread_id, req.npc_id)
    history_records = list_thread_records(db, current_user, thread.id)
    req = req.model_copy(
        update={
            "thread_id": thread.id,
            "npc_interaction_count": max(req.npc_interaction_count, len(history_records)),
        }
    )
    recent_history_records = history_records[-6:]
    dialogue_history = [
        (record.question, record.answer)
        for record in recent_history_records
    ]
    memory_context = get_memory_context(db, thread, req.question)

    def event_generator():
        stream_state = NpcRagStreamState()

        yield json.dumps(
            {
                "type": "thread",
                "data": {
                    "thread_id": thread.id,
                    "title": thread.title,
                    "npc_id": req.npc_id,
                    "annoyance_percent": req.annoyance_percent,
                    "favorability_percent": req.favorability_percent,
                    "npc_interaction_count": req.npc_interaction_count,
                },
            },
            ensure_ascii=False,
        ) + "\n"

        for event_text in run_npc_rag_chat_stream(
            req,
            stream_state=stream_state,
            dialogue_history=dialogue_history,
            memory_summary=memory_context.summary,
            memory_items=memory_context.items,
        ):
            yield event_text

        if stream_state.answer.strip():
            save_chat_record(
                db=db,
                user_id=current_user.id,
                thread_id=thread.id,
                question=req.question,
                answer=stream_state.answer.strip(),
                model=MODEL_NAME,
                used_search=stream_state.used_rag,
                sources=stream_state.sources,
            )
            maybe_update_thread_title(db, thread, req.question)
            maybe_update_thread_memory(db, thread)

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
