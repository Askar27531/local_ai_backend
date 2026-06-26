import json
import logging
from datetime import datetime

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from app.db.database import get_db, init_db
from app.db.models import User
from app.schemas.auth import LoginRequest, RegisterRequest, TokenResponse, UserResponse
from app.schemas.chat import ChatRequest, ChatResponse, SearchItem
from app.schemas.thread import ChatRecordItem, RenameThreadRequest, ThreadResponse
from app.services.agent_service import AgentStreamState, run_agent_chat, run_agent_chat_stream
from app.services.auth_service import (
    authenticate_user,
    create_access_token,
    get_current_user,
    register_user,
)
from app.services.checkpointer_service import close_checkpointer, init_checkpointer
from app.services.llm_service import MODEL_NAME, OLLAMA_BASE_URL
from app.services.record_service import save_chat_record
from app.services.thread_service import (
    create_chat_thread,
    get_or_create_chat_thread,
    list_thread_records,
    list_user_threads,
    maybe_update_thread_title,
    rename_thread,
    soft_delete_thread,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logging.getLogger("uvicorn").setLevel(logging.INFO)
logging.getLogger("uvicorn.access").setLevel(logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Local qwen3 Backend",
    description="FastAPI backend for local qwen3 with user auth, threads, streaming agent, and PostgresSaver memory.",
    version="0.6.0",
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
    init_checkpointer()


@app.on_event("shutdown")
def shutdown():
    close_checkpointer()


@app.get("/health")
def health():
    return {
        "status": "ok",
        "model": MODEL_NAME,
        "ollama_base_url": OLLAMA_BASE_URL,
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
def me(current_user: User = Depends(get_current_user)):
    return UserResponse(id=current_user.id, username=current_user.username)


def _thread_to_response(thread) -> ThreadResponse:
    return ThreadResponse(
        id=thread.id,
        title=thread.title,
        created_at=thread.created_at,
        updated_at=thread.updated_at,
    )


@app.get("/threads", response_model=list[ThreadResponse])
def get_threads(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return [_thread_to_response(t) for t in list_user_threads(db, current_user)]


@app.post("/threads", response_model=ThreadResponse)
def new_thread(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _thread_to_response(create_chat_thread(db, current_user))


@app.patch("/threads/{thread_id}", response_model=ThreadResponse)
def update_thread_title(
    thread_id: str,
    req: RenameThreadRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _thread_to_response(rename_thread(db, current_user, thread_id, req.title))


@app.delete("/threads/{thread_id}")
def delete_thread(
    thread_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    soft_delete_thread(db, current_user, thread_id)
    return {"status": "ok"}


@app.get("/threads/{thread_id}/records", response_model=list[ChatRecordItem])
def get_thread_records(
    thread_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
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


@app.post("/chat", response_model=ChatResponse)
def chat(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = get_or_create_chat_thread(db, current_user, req.thread_id)

    result = run_agent_chat(
        question=req.question,
        checkpoint_thread_id=thread.checkpoint_thread_id,
        user_id=current_user.id,
    )

    save_chat_record(
        db=db,
        user_id=current_user.id,
        thread_id=thread.id,
        question=req.question,
        answer=result.answer,
        model=MODEL_NAME,
        used_search=result.used_search,
        sources=result.sources,
    )
    maybe_update_thread_title(db, thread, req.question)

    return ChatResponse(
        answer=result.answer,
        model=MODEL_NAME,
        thread_id=thread.id,
        used_search=result.used_search,
        sources=result.sources,
    )


@app.post("/chat/stream")
def chat_stream(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    thread = get_or_create_chat_thread(db, current_user, req.thread_id)

    def event_generator():
        stream_state = AgentStreamState()

        # 前端新对话时需要这个事件保存 thread_id
        yield json.dumps(
            {
                "type": "thread",
                "data": {
                    "thread_id": thread.id,
                    "title": thread.title,
                },
            },
            ensure_ascii=False,
        ) + "\n"

        for event_text in run_agent_chat_stream(
            question=req.question,
            checkpoint_thread_id=thread.checkpoint_thread_id,
            user_id=current_user.id,
            stream_state=stream_state,
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
                used_search=stream_state.used_search,
                sources=stream_state.sources,
            )
            maybe_update_thread_title(db, thread, req.question)

    return StreamingResponse(
        event_generator(),
        media_type="application/x-ndjson; charset=utf-8",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
