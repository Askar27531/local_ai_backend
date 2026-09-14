"""NPC 对话线程与完整问答记录的 PostgreSQL 服务。"""

from __future__ import annotations

from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session

from npc_app.database import NpcChatRecord, NpcChatThread, NpcUser


def create_chat_thread(
    db: Session,
    user: NpcUser,
    npc_id: str,
) -> NpcChatThread:
    """为指定用户和 NPC 创建 UUID 线程并立即提交。

    提交后 refresh 取得数据库确认的字段；调用者随后可安全地使用线程 ID 读取历史、
    记忆或构造首个 NDJSON 事件。
    """
    thread_id = str(uuid4())
    thread = NpcChatThread(
        id=thread_id,
        user_id=user.id,
        npc_id=npc_id,
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
    """解析当前用户与 NPC 的线程；只有客户端未指定 ID 时才允许复用或新建。

    未传 ID 时按 updated_at 复用该用户与 NPC 最近的线程，不存在才创建。显式传入 ID
    时必须同时满足用户归属与 NPC 绑定：不存在/越权返回 404，角色不匹配返回 409，
    两种情况都不会静默创建替代线程。
    """
    if not thread_id:
        thread = (
            db.query(NpcChatThread)
            .filter(
                NpcChatThread.user_id == user.id,
                NpcChatThread.npc_id == npc_id,
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
        )
        .first()
    )
    if not thread:
        raise HTTPException(status_code=404, detail="NPC 对话线程不存在或无权访问")
    # 显式线程与 NPC 不匹配属于客户端状态错误，不能静默切换线程掩盖映射问题。
    if thread.npc_id != npc_id:
        raise HTTPException(status_code=409,detail="thread_id 与 npc_id 不匹配，请使用该 NPC 对应的线程 ID",)
    return thread


def save_chat_record(db: Session, thread_id: str, question: str, answer: str) -> NpcChatRecord:
    """原子保存完整问答并刷新线程活跃时间，提交后返回持久化记录。"""
    chat_record = NpcChatRecord(thread_id=thread_id, question=question, answer=answer)
    db.add(chat_record)
    thread = db.query(NpcChatThread).filter(NpcChatThread.id == thread_id).first()
    if thread:
        thread.updated_at = datetime.now()
    db.commit()
    db.refresh(chat_record)
    return chat_record


def list_thread_records(db: Session, thread_id: str) -> list[NpcChatRecord]:
    """按记录 ID 升序读取线程全部成功回合，保持真实对话时间顺序。"""
    return (
        db.query(NpcChatRecord)
        .filter(NpcChatRecord.thread_id == thread_id)
        .order_by(NpcChatRecord.id.asc())
        .all()
    )
