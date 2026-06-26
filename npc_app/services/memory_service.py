from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from npc_app.db.models import NpcChatRecord, NpcChatThread, NpcMemoryItem, NpcThreadMemory
from npc_app.services.llm_service import llm


NPC_MEMORY_UPDATE_INTERVAL = int(os.getenv("NPC_MEMORY_UPDATE_INTERVAL", "6"))
NPC_MEMORY_MAX_RECORDS_PER_UPDATE = int(os.getenv("NPC_MEMORY_MAX_RECORDS_PER_UPDATE", "12"))
NPC_THREAD_SUMMARY_MAX_CHARS = int(os.getenv("NPC_THREAD_SUMMARY_MAX_CHARS", "500"))
NPC_MEMORY_RETRIEVAL_CANDIDATE_LIMIT = int(os.getenv("NPC_MEMORY_RETRIEVAL_CANDIDATE_LIMIT", "80"))


@dataclass
class RetrievedMemoryItem:
    memory_type: str
    content: str
    keywords: str = ""
    importance: int = 3
    score: float = 0.0


@dataclass
class MemoryContext:
    summary: str = ""
    items: list[RetrievedMemoryItem] = field(default_factory=list)


def get_memory_context(
    db: Session,
    thread: NpcChatThread,
    question: str,
    limit: int = 5,
) -> MemoryContext:
    return MemoryContext(
        summary=get_thread_memory_summary(db, thread.id),
        items=retrieve_memory_items(db, thread, question, limit=limit),
    )


def get_thread_memory_summary(db: Session, thread_id: str) -> str:
    memory = (
        db.query(NpcThreadMemory)
        .filter(NpcThreadMemory.thread_id == thread_id)
        .first()
    )
    return memory.summary if memory else ""


def retrieve_memory_items(
    db: Session,
    thread: NpcChatThread,
    question: str,
    limit: int = 5,
) -> list[RetrievedMemoryItem]:
    from npc_app.services.memory_milvus_service import retrieve_memory_items_from_milvus

    milvus_items = retrieve_memory_items_from_milvus(
        user_id=thread.user_id,
        thread_id=thread.id,
        npc_id=thread.npc_id,
        question=question,
        limit=limit,
    )
    if milvus_items:
        return milvus_items

    candidates = (
        db.query(NpcMemoryItem)
        .filter(
            NpcMemoryItem.thread_id == thread.id,
            NpcMemoryItem.npc_id == thread.npc_id,
        )
        .order_by(NpcMemoryItem.importance.desc(), NpcMemoryItem.last_seen_at.desc())
        .limit(NPC_MEMORY_RETRIEVAL_CANDIDATE_LIMIT)
        .all()
    )
    query_terms = _extract_terms(question)
    scored: list[RetrievedMemoryItem] = []
    for item in candidates:
        haystack = f"{item.content} {item.keywords}"
        item_terms = _extract_terms(haystack)
        overlap = len(query_terms.intersection(item_terms))
        if query_terms and overlap == 0 and item.importance < 5:
            continue
        score = overlap * 3.0 + min(max(item.importance, 1), 5)
        scored.append(
            RetrievedMemoryItem(
                memory_type=item.memory_type,
                content=item.content,
                keywords=item.keywords,
                importance=item.importance,
                score=score,
            )
        )
    return sorted(scored, key=lambda item: item.score, reverse=True)[:limit]


def maybe_update_thread_memory(db: Session, thread: NpcChatThread) -> None:
    memory = _get_or_create_memory(db, thread)
    latest_record_id = _latest_record_id(db, thread.id)
    if latest_record_id <= memory.last_record_id:
        return

    pending_count = (
        db.query(NpcChatRecord)
        .filter(
            NpcChatRecord.thread_id == thread.id,
            NpcChatRecord.id > memory.last_record_id,
        )
        .count()
    )
    if pending_count < NPC_MEMORY_UPDATE_INTERVAL:
        return

    records = (
        db.query(NpcChatRecord)
        .filter(
            NpcChatRecord.thread_id == thread.id,
            NpcChatRecord.id > memory.last_record_id,
        )
        .order_by(NpcChatRecord.id.asc())
        .limit(NPC_MEMORY_MAX_RECORDS_PER_UPDATE)
        .all()
    )
    if not records:
        return

    update = _build_memory_update(thread.npc_id, memory.summary, records)
    memory.summary = _truncate(update.summary, NPC_THREAD_SUMMARY_MAX_CHARS)
    memory.last_record_id = records[-1].id
    memory.updated_at = datetime.now()
    _upsert_memory_items(db, thread, records[-1].id, update.items)
    db.commit()


@dataclass
class _MemoryUpdate:
    summary: str
    items: list[dict[str, Any]]


def _get_or_create_memory(db: Session, thread: NpcChatThread) -> NpcThreadMemory:
    memory = (
        db.query(NpcThreadMemory)
        .filter(NpcThreadMemory.thread_id == thread.id)
        .first()
    )
    if memory:
        return memory

    memory = NpcThreadMemory(
        user_id=thread.user_id,
        thread_id=thread.id,
        npc_id=thread.npc_id,
    )
    db.add(memory)
    db.flush()
    return memory


def _latest_record_id(db: Session, thread_id: str) -> int:
    record = (
        db.query(NpcChatRecord)
        .filter(NpcChatRecord.thread_id == thread_id)
        .order_by(NpcChatRecord.id.desc())
        .first()
    )
    return int(record.id) if record else 0


def _build_memory_update(
    npc_id: str,
    previous_summary: str,
    records: list[NpcChatRecord],
) -> _MemoryUpdate:
    dialogue_text = "\n".join(
        f"玩家：{record.question}\n{npc_id}：{record.answer}"
        for record in records
    )
    prompt = "\n".join(
        [
            "请为游戏 NPC 对话更新长期记忆。",
            "输出必须是 JSON，不要包裹 markdown，不要输出解释。",
            "JSON 格式：",
            '{"summary":"不超过250字的线程概览","items":[{"type":"player_fact|npc_disclosed|unresolved_question|relationship_signal","content":"一条可检索长期记忆","keywords":["关键词"],"importance":1-5}]}',
            "",
            "规则：",
            "- 只记录这个 NPC 与玩家之间已经发生、已经说出口的信息。",
            "- 不要新增设定，不要剧透未解锁真相，不要把猜测写成事实。",
            "- items 每条必须短，最多 60 字；最多输出 8 条。",
            "- summary 是极短概览，不是完整历史。",
            "",
            f"当前 NPC：{npc_id}",
            "",
            f"已有短摘要：\n{previous_summary or '无'}",
            "",
            f"新增对话：\n{dialogue_text}",
        ]
    )
    response = llm.invoke(
        [
            SystemMessage(content="你负责维护游戏 NPC 的可检索长期记忆。"),
            HumanMessage(content=prompt),
        ]
    )
    content = _message_to_text(response)
    data = _parse_json_object(content)
    summary = str(data.get("summary") or previous_summary or "").strip()
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    return _MemoryUpdate(summary=summary, items=[item for item in items if isinstance(item, dict)])


def _upsert_memory_items(
    db: Session,
    thread: NpcChatThread,
    source_record_id: int,
    items: list[dict[str, Any]],
) -> None:
    for raw_item in items[:8]:
        content = _truncate(str(raw_item.get("content") or "").strip(), 180)
        if not content:
            continue
        existing = (
            db.query(NpcMemoryItem)
            .filter(
                NpcMemoryItem.thread_id == thread.id,
                NpcMemoryItem.content == content,
            )
            .first()
        )
        keywords_value = raw_item.get("keywords", [])
        if isinstance(keywords_value, list):
            keywords = ", ".join(str(keyword) for keyword in keywords_value[:8])
        else:
            keywords = str(keywords_value or "")
        importance = _coerce_importance(raw_item.get("importance", 3))
        memory_type = str(raw_item.get("type") or "fact")[:50]

        if existing:
            existing.keywords = keywords or existing.keywords
            existing.importance = max(existing.importance, importance)
            existing.last_seen_at = datetime.now()
            db.flush()
            _sync_memory_item_to_milvus(existing)
            continue

        memory_item = NpcMemoryItem(
            user_id=thread.user_id,
            thread_id=thread.id,
            npc_id=thread.npc_id,
            memory_type=memory_type,
            content=content,
            keywords=keywords,
            importance=importance,
            source_record_id=source_record_id,
        )
        db.add(memory_item)
        db.flush()
        _sync_memory_item_to_milvus(memory_item)


def _sync_memory_item_to_milvus(item: NpcMemoryItem) -> None:
    from npc_app.services.memory_milvus_service import upsert_memory_item_to_milvus

    upsert_memory_item_to_milvus(
        memory_item_id=item.id,
        user_id=item.user_id,
        thread_id=item.thread_id,
        npc_id=item.npc_id,
        memory_type=item.memory_type,
        content=item.content,
        keywords=item.keywords,
        importance=item.importance,
        source_record_id=item.source_record_id,
    )


def _parse_json_object(text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.S)
        if not match:
            return {}
        try:
            data = json.loads(match.group(0))
            return data if isinstance(data, dict) else {}
        except json.JSONDecodeError:
            return {}


def _extract_terms(text: str) -> set[str]:
    ascii_terms = {term.lower() for term in re.findall(r"[A-Za-z0-9_]{2,}", text)}
    cjk_terms: set[str] = set()
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        cjk_terms.add(phrase)
        cjk_terms.update(phrase[index : index + 2] for index in range(0, len(phrase) - 1))
    return ascii_terms.union(cjk_terms)


def _coerce_importance(value: Any) -> int:
    try:
        importance = int(value)
    except (TypeError, ValueError):
        importance = 3
    return min(max(importance, 1), 5)


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _message_to_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content.strip()
    return str(content).strip() if content else ""
