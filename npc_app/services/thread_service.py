from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session, selectinload

from npc_app.db.models import NpcChatRecord, NpcChatThread, NpcUser


def create_chat_thread(
    db: Session,
    user: NpcUser,
    npc_id: str,
    title: str = "新对话",
) -> NpcChatThread:
    thread_id = str(uuid4())
    thread = NpcChatThread(
        id=thread_id,
        user_id=user.id,
        npc_id=npc_id,
        checkpoint_thread_id=f"npc:user:{user.id}:npc:{npc_id}:thread:{thread_id}",
        title=title,
    )
    db.add(thread)
    db.commit()
    db.refresh(thread)
    return thread


def get_or_create_chat_thread(
    db: Session,
    user: NpcUser,
    thread_id: str | None,
    npc_id: str,
) -> NpcChatThread:
    if not thread_id:
        thread = (
            db.query(NpcChatThread)
            .filter(
                NpcChatThread.user_id == user.id,
                NpcChatThread.npc_id == npc_id,
                NpcChatThread.is_deleted == False,
            )
            .order_by(NpcChatThread.updated_at.desc())
            .first()
        )
        if thread:
            return thread
        return create_chat_thread(db=db, user=user, npc_id=npc_id)

    thread = (
        db.query(NpcChatThread)
        .filter(
            NpcChatThread.id == thread_id,
            NpcChatThread.user_id == user.id,
            NpcChatThread.is_deleted == False,
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="NPC 对话线程不存在或无权访问")
    if thread.npc_id != npc_id:
        existing_npc_thread = (
            db.query(NpcChatThread)
            .filter(
                NpcChatThread.user_id == user.id,
                NpcChatThread.npc_id == npc_id,
                NpcChatThread.is_deleted == False,
            )
            .order_by(NpcChatThread.updated_at.desc())
            .first()
        )
        if existing_npc_thread:
            return existing_npc_thread
        return create_chat_thread(db=db, user=user, npc_id=npc_id)
    return thread


def list_user_threads(db: Session, user: NpcUser) -> list[NpcChatThread]:
    return (
        db.query(NpcChatThread)
        .filter(NpcChatThread.user_id == user.id, NpcChatThread.is_deleted == False)
        .order_by(NpcChatThread.updated_at.desc())
        .all()
    )


def get_thread_for_user(db: Session, user: NpcUser, thread_id: str) -> NpcChatThread:
    thread = (
        db.query(NpcChatThread)
        .filter(
            NpcChatThread.id == thread_id,
            NpcChatThread.user_id == user.id,
            NpcChatThread.is_deleted == False,
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="NPC 对话线程不存在或无权访问")
    return thread


def list_thread_records(db: Session, user: NpcUser, thread_id: str) -> list[NpcChatRecord]:
    get_thread_for_user(db, user, thread_id)
    return (
        db.query(NpcChatRecord)
        .options(selectinload(NpcChatRecord.sources))
        .filter(NpcChatRecord.user_id == user.id, NpcChatRecord.thread_id == thread_id)
        .order_by(NpcChatRecord.created_at.asc())
        .all()
    )


def maybe_update_thread_title(db: Session, thread: NpcChatThread, question: str) -> None:
    if thread.title and thread.title != "新对话":
        return
    title = question.strip().replace("\n", " ")[:30] or "新对话"
    thread.title = title
    thread.updated_at = datetime.now()
    db.commit()
