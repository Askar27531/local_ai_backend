"""长期记忆的业务事实、召回回退、摘要更新与 Milvus 同步。

PostgreSQL 保存原始记忆记录和线程摘要；Milvus 为这些记录建立可丢失、可重建的语义索引。
结构化 Unity 状态可产生 player_fact，对话中的模型提取只能产生较低信任的 player_claim
等记忆类型。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.database import NpcChatRecord, NpcChatThread, NpcMemoryItem, NpcThreadMemory
from npc_app.services.llm_service import llm
from npc_app.utils import dialogue_config, extract_json_object, message_text, search_terms, truncate_text

NPC_MEMORY_UPDATE_INTERVAL = int(os.getenv("NPC_MEMORY_UPDATE_INTERVAL", "6"))
NPC_MEMORY_MAX_RECORDS_PER_UPDATE = int(os.getenv("NPC_MEMORY_MAX_RECORDS_PER_UPDATE", "12"))
NPC_THREAD_SUMMARY_MAX_CHARS = int(os.getenv("NPC_THREAD_SUMMARY_MAX_CHARS", "500"))
NPC_MEMORY_RETRIEVAL_CANDIDATE_LIMIT = int(os.getenv("NPC_MEMORY_RETRIEVAL_CANDIDATE_LIMIT", "80"))
_MEMORY_EXTRACTION_CONFIG = dialogue_config()["memory_extraction"]


@dataclass
class RetrievedMemoryItem:
    """统一表示来自 Milvus 或 PostgreSQL 回退检索的一条长期记忆。

    importance 是写入时的长期重要度，score 是针对当前问题的召回相关性；两者会在
    Context Compiler 中再次组合，但不会改变 memory_type 所表达的可信等级。
    """

    memory_type: str
    content: str
    keywords: str = ""
    importance: int = 3
    score: float = 0.0

    def __post_init__(self) -> None:
        if not 1 <= self.importance <= 5:
            raise ValueError("memory importance must be between 1 and 5")


@dataclass
class MemoryContext:
    """提供给单轮编排器的线程摘要和相关长期记忆集合。"""

    summary: str = ""
    items: list[RetrievedMemoryItem] = field(default_factory=list)


def get_memory_context(
    db: Session,
    thread: NpcChatThread,
    question: str,
    limit: int = 5,
) -> MemoryContext:
    """组合线程连续性摘要和与当前问题相关的长期记忆。"""
    memory = db.query(NpcThreadMemory).filter(NpcThreadMemory.thread_id == thread.id).first()
    return MemoryContext(
        summary=memory.summary if memory else "",
        items=retrieve_memory_items(db, thread, question, limit=limit),
    )


def retrieve_memory_items(
    db: Session,
    thread: NpcChatThread,
    question: str,
    limit: int = 5,
) -> list[RetrievedMemoryItem]:
    """优先使用 Milvus 语义召回；无结果时回退到 PostgreSQL 关键词评分。"""
    from npc_app.services.memory_milvus_service import retrieve_memory_items_from_milvus

    milvus_items = retrieve_memory_items_from_milvus(
        user_id=thread.user_id,
        thread_id=thread.id,
        npc_id=thread.npc_id,
        question=question,
        limit=limit,
    )
    # PostgreSQL 保存业务真相，因此语义索引不可用或无命中时仍可提供有限的记忆能力。
    if milvus_items:
        return milvus_items

    # 回退只扫描近期高重要度候选上限，避免线程增长后每次请求全表计算关键词重叠。
    candidates = (
        db.query(NpcMemoryItem)
        .filter(NpcMemoryItem.thread_id == thread.id)
        .order_by(NpcMemoryItem.importance.desc(), NpcMemoryItem.id.desc())
        .limit(NPC_MEMORY_RETRIEVAL_CANDIDATE_LIMIT)
        .all()
    )
    query_terms = search_terms(question)
    scored: list[RetrievedMemoryItem] = []
    for item in candidates:
        haystack = f"{item.content} {item.keywords}"
        item_terms = search_terms(haystack)
        overlap = len(query_terms.intersection(item_terms))
        if query_terms and overlap == 0 and item.importance < 5:
            continue
        # 当前问题词项重叠权重大于 importance，使“现在相关”优先于“长期重要但无关”。
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
    """累计足够的新对话后，分批更新线程摘要和结构化长期记忆。"""
    memory = _get_or_create_memory(db, thread)
    latest_record = (
        db.query(NpcChatRecord)
        .filter(NpcChatRecord.thread_id == thread.id)
        .order_by(NpcChatRecord.id.desc())
        .first()
    )
    # last_record_id 是增量游标；已消费过的问答不会重复进入摘要或重复提取记忆。
    latest_record_id = int(latest_record.id) if latest_record else 0
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
    # 摘要不是每轮生成，按间隔批处理可控制额外 LLM 调用成本。
    if pending_count < NPC_MEMORY_UPDATE_INTERVAL:
        return

    # 单次只消费有限批次，长时间未更新的线程可在后续成功回合继续推进游标。
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
    memory.summary = truncate_text(update.summary, NPC_THREAD_SUMMARY_MAX_CHARS)
    memory.last_record_id = records[-1].id
    _upsert_memory_items(db, thread, update.items)
    db.commit()


def record_confirmed_turn_memories(
    db: Session,
    thread: NpcChatThread,
    req: UnityNpcTurnRequest,
) -> None:
    """只把本轮 Unity 结构化状态确认的事实以 player_fact 持久化。"""
    items = [
        {
            "type": "player_fact",
            "content": f"玩家曾向当前 NPC 展示物品：{item_id}",
            "keywords": [item_id, "presented_item"],
            "importance": 4,
        }
        for item_id in dict.fromkeys(req.player.presented_items)
        if item_id.strip()
    ]
    if not items:
        return
    _upsert_memory_items(db, thread, items, allow_player_fact=True)
    db.commit()


@dataclass
class _MemoryUpdate:
    """一次 LLM 记忆提取返回的滚动摘要与候选结构化条目。"""

    summary: str
    items: list[dict[str, Any]]


def _get_or_create_memory(db: Session, thread: NpcChatThread) -> NpcThreadMemory:
    """取得线程唯一摘要行；新建时仅 flush 以加入调用方当前事务。"""
    memory = (
        db.query(NpcThreadMemory)
        .filter(NpcThreadMemory.thread_id == thread.id)
        .first()
    )
    if memory:
        return memory

    memory = NpcThreadMemory(
        thread_id=thread.id,
    )
    db.add(memory)
    db.flush()
    return memory


def _build_memory_update(
    npc_id: str,
    previous_summary: str,
    records: list[NpcChatRecord],
) -> _MemoryUpdate:
    """让 LLM 从新增对话中提取摘要和候选记忆，但不在这里提升其可信等级。"""
    dialogue_text = "\n".join(
        f"玩家：{record.question}\n{npc_id}：{record.answer}"
        for record in records
    )
    # 旧摘要与本批新增问答同时提供，模型生成滚动摘要而非孤立的批次摘要。
    prompt = "\n".join(
        [
            *_MEMORY_EXTRACTION_CONFIG["instructions"],
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
            SystemMessage(content=str(_MEMORY_EXTRACTION_CONFIG["system_prompt"])),
            HumanMessage(content=prompt),
        ]
    )
    content = message_text(response)
    # 记忆是派生能力，非严格解析失败时保留旧摘要并忽略条目，不影响已经生成的回答。
    data = extract_json_object(content, strict=False)
    summary = str(data.get("summary") or previous_summary or "").strip()
    items = data.get("items", [])
    if not isinstance(items, list):
        items = []
    return _MemoryUpdate(summary=summary, items=[item for item in items if isinstance(item, dict)])


def _upsert_memory_items(
    db: Session,
    thread: NpcChatThread,
    items: list[dict[str, Any]],
    *,
    allow_player_fact: bool = False,
) -> None:
    """规范化并写入 PostgreSQL，再把同一业务记录同步到 Milvus 索引。"""
    # 每次更新限制候选数量，并在服务边界统一裁剪内容、关键词和 importance。
    for raw_item in items[:8]:
        content = truncate_text(str(raw_item.get("content") or "").strip(), 180)
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
        keywords = truncate_text(keywords, 512)
        try:
            importance = int(raw_item.get("importance", 3))
        except (TypeError, ValueError):
            importance = 3
        importance = min(max(importance, 1), 5)
        memory_type = _normalize_memory_type(raw_item.get("type"), allow_player_fact)

        # 内容相同视为同一业务记忆：合并关键词、只提高重要度，再刷新向量索引。
        if existing:
            existing.keywords = keywords or existing.keywords
            existing.importance = max(existing.importance, importance)
            db.flush()
            _sync_memory_item_to_milvus(existing, thread)
            continue

        memory_item = NpcMemoryItem(
            thread_id=thread.id,
            memory_type=memory_type,
            content=content,
            keywords=keywords,
            importance=importance,
        )
        db.add(memory_item)
        db.flush()
        _sync_memory_item_to_milvus(memory_item, thread)


def _normalize_memory_type(value: Any, allow_player_fact: bool) -> str:
    """把模型输出限制到允许类型，并保护 player_fact 只来自结构化确认路径。"""
    # 对话中的玩家陈述默认只是 claim；只有显式授权的 Unity 结构化证据才能成为 fact。
    memory_type = str(value or "player_claim").strip().lower()
    if memory_type == "player_fact":
        return "player_fact" if allow_player_fact else "player_claim"
    allowed = {"player_claim", "npc_disclosed", "unresolved_question", "relationship_signal"}
    return memory_type if memory_type in allowed else "player_claim"


def _sync_memory_item_to_milvus(item: NpcMemoryItem, thread: NpcChatThread) -> None:
    """把已获得 PostgreSQL ID 的业务记忆同步为带用户/线程/NPC 隔离键的向量记录。"""
    from npc_app.services.memory_milvus_service import upsert_memory_item_to_milvus

    upsert_memory_item_to_milvus(
        memory_item_id=item.id,
        user_id=thread.user_id,
        thread_id=item.thread_id,
        npc_id=thread.npc_id,
        memory_type=item.memory_type,
        content=item.content,
        keywords=item.keywords,
        importance=item.importance,
    )
