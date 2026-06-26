from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from app.db.models import ChatRecord, ChatThread, User


def create_chat_thread(db: Session, user: User, title: str = "新对话") -> ChatThread:
    thread_id = str(uuid4())
    thread = ChatThread(
        id=thread_id,
        user_id=user.id,
        checkpoint_thread_id=f"user:{user.id}:thread:{thread_id}",
        title=title,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


def get_or_create_chat_thread(db: Session, user: User, thread_id: str | None) -> ChatThread:
    if not thread_id:
        return create_chat_thread(db=db, user=user)

    thread = (
        db.query(ChatThread)
        .filter(
            ChatThread.id == thread_id,
            ChatThread.user_id == user.id,
            ChatThread.is_deleted == False,
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="对话线程不存在或无权访问")
    return thread


def list_user_threads(db: Session, user: User) -> list[ChatThread]:
    return (
        db.query(ChatThread)
        .filter(ChatThread.user_id == user.id, ChatThread.is_deleted == False)
        .order_by(ChatThread.updated_at.desc())
        .all()
    )


def get_thread_for_user(db: Session, user: User, thread_id: str) -> ChatThread:
    thread = (
        db.query(ChatThread)
        .filter(
            ChatThread.id == thread_id,
            ChatThread.user_id == user.id,
            ChatThread.is_deleted == False,
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="对话线程不存在或无权访问")
    return thread


def rename_thread(db: Session, user: User, thread_id: str, title: str) -> ChatThread:
    thread = get_thread_for_user(db, user, thread_id)
    thread.title = title.strip() or "新对话"
    thread.updated_at = datetime.now()
    db.commit()
    db.refresh(thread)
    return thread


def soft_delete_thread(db: Session, user: User, thread_id: str) -> None:
    thread = get_thread_for_user(db, user, thread_id)
    thread.is_deleted = True
    thread.updated_at = datetime.now()
    db.commit()


def list_thread_records(db: Session, user: User, thread_id: str) -> list[ChatRecord]:
    get_thread_for_user(db, user, thread_id)
    return (
        db.query(ChatRecord)
        .options(selectinload(ChatRecord.sources))
        .filter(ChatRecord.user_id == user.id, ChatRecord.thread_id == thread_id)
        .order_by(ChatRecord.created_at.asc())
        .all()
    )


def maybe_update_thread_title(db: Session, thread: ChatThread, question: str) -> None:
    if thread.title and thread.title != "新对话":
        return
    title = question.strip().replace("\n", " ")[:30] or "新对话"
    thread.title = title
    thread.updated_at = datetime.now()
    db.commit()
