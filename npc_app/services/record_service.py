from datetime import datetime

from sqlalchemy.orm import Session

from npc_app.db.models import NpcChatRecord, NpcChatThread, NpcSearchSource
from npc_app.schemas.chat import SearchItem


def save_chat_record(
    db: Session,
    user_id: int,
    thread_id: str,
    question: str,
    answer: str,
    model: str,
    used_search: bool,
    sources: list[SearchItem],
) -> NpcChatRecord:
    chat_record = NpcChatRecord(
        user_id=user_id,
        thread_id=thread_id,
        question=question,
        answer=answer,
        model=model,
        used_search=used_search,
    )
    db.add(chat_record)
    db.flush()

    for item in sources:
        db.add(
            NpcSearchSource(
                chat_id=chat_record.id,
                title=item.title,
                url=item.url,
                snippet=item.snippet,
            )
        )

    thread = db.query(NpcChatThread).filter(NpcChatThread.id == thread_id).first()
    if thread:
        thread.updated_at = datetime.now()

    db.commit()
    db.refresh(chat_record)
    return chat_record
