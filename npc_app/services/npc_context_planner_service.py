from __future__ import annotations

import re
from dataclasses import dataclass, field

from npc_app.schemas.npc_chat import NpcChatRequest
from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import RetrievedChunk


MAX_PLANNED_MEMORY_ITEMS = 8
MAX_PLANNED_CHUNKS = 6

SENSITIVE_TERMS = (
    "Project ECHO",
    "Subject 07",
    "control device",
    "experiment subject",
    "human experiment",
)


@dataclass
class PlannedPromptContext:
    summary: str = ""
    memory_items: list[RetrievedMemoryItem] = field(default_factory=list)
    dialogue_history: list[tuple[str, str]] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)
    strategy: str = ""


@dataclass
class _PlannedMemory:
    bucket: str
    item: RetrievedMemoryItem
    score: float


def plan_prompt_context(
    req: NpcChatRequest,
    summary: str,
    memory_items: list[RetrievedMemoryItem],
    dialogue_history: list[tuple[str, str]],
    chunks: list[RetrievedChunk],
) -> PlannedPromptContext:
    """
    Convert retrieved context into a smaller, role-aware plan before prompt assembly.
    This keeps storage/retrieval unchanged while avoiding flat "dump everything" prompts.
    """
    planned_memories = _plan_memory_items(req, memory_items)
    planned_chunks = _plan_chunks(req, chunks)
    strategy = _build_strategy(req, planned_memories, planned_chunks, bool(summary), bool(dialogue_history))

    return PlannedPromptContext(
        summary=summary,
        memory_items=[_bucketed_memory(memory) for memory in planned_memories],
        dialogue_history=dialogue_history,
        chunks=planned_chunks,
        strategy=strategy,
    )


def _plan_memory_items(
    req: NpcChatRequest,
    items: list[RetrievedMemoryItem],
) -> list[_PlannedMemory]:
    query_terms = _extract_terms(req.question)
    selected: list[_PlannedMemory] = []
    seen: set[str] = set()

    for item in sorted(items, key=_memory_sort_key, reverse=True):
        normalized = _normalize_text(item.content)
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)

        if _blocked_by_story_gate(req, item.content):
            continue

        overlap = len(query_terms.intersection(_extract_terms(f"{item.content} {item.keywords}")))
        bucket = _memory_bucket(req, item, overlap)
        if bucket == "drop":
            continue

        selected.append(
            _PlannedMemory(
                bucket=bucket,
                item=item,
                score=_planned_memory_score(item, overlap, bucket),
            )
        )

    return sorted(selected, key=lambda memory: memory.score, reverse=True)[:MAX_PLANNED_MEMORY_ITEMS]


def _plan_chunks(req: NpcChatRequest, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    seen: set[str] = set()

    for chunk in chunks:
        key = _normalize_text(f"{chunk.source_file}:{chunk.section_title}:{chunk.content[:120]}")
        if not key or key in seen:
            continue
        seen.add(key)

        if chunk.unlock_level > req.unlocked_story_level:
            continue
        selected.append(chunk)

    return selected[: min(req.top_k, MAX_PLANNED_CHUNKS)]


def _memory_bucket(req: NpcChatRequest, item: RetrievedMemoryItem, overlap: int) -> str:
    memory_type = item.memory_type.lower()
    importance = max(1, min(item.importance, 5))

    if memory_type == "relationship_signal":
        return "tone_only"

    if memory_type == "unresolved_question":
        if overlap > 0 or importance >= 4 or req.annoyance_percent >= 35:
            return "repeat_or_open_loop"
        return "drop"

    if memory_type == "npc_disclosed":
        if overlap > 0 or importance >= 4:
            return "already_said"
        return "reference"

    if memory_type == "player_fact":
        if overlap > 0 or importance >= 4:
            return "must_use"
        return "reference"

    if overlap > 0 or importance >= 5:
        return "reference"

    return "drop"


def _bucketed_memory(memory: _PlannedMemory) -> RetrievedMemoryItem:
    item = memory.item
    return RetrievedMemoryItem(
        memory_type=f"{memory.bucket}:{item.memory_type}",
        content=item.content,
        keywords=item.keywords,
        importance=item.importance,
        score=memory.score,
    )


def _build_strategy(
    req: NpcChatRequest,
    memories: list[_PlannedMemory],
    chunks: list[RetrievedChunk],
    has_summary: bool,
    has_dialogue: bool,
) -> str:
    buckets = {memory.bucket for memory in memories}
    lines = [
        "Use the planned context by priority, not as a flat transcript.",
        "Hard game state, NPC identity, story unlock level, and trust rules override all retrieved context.",
    ]
    if has_summary:
        lines.append("Thread summary is a compact continuity aid; do not treat it as new world lore.")
    if "must_use" in buckets:
        lines.append("must_use memory can be acknowledged naturally when it helps answer this turn.")
    if "already_said" in buckets:
        lines.append("already_said memory marks facts this NPC has already told the player.")
    if "repeat_or_open_loop" in buckets:
        lines.append("repeat_or_open_loop memory helps detect repeated questions or unresolved requests.")
    if "tone_only" in buckets:
        lines.append("tone_only memory may affect warmth, caution, or impatience, but is not plot evidence.")
    if has_dialogue:
        lines.append("Recent dialogue is for pronouns and follow-ups; it must not override locked knowledge.")
    if chunks:
        lines.append("World RAG chunks are reference material; use only details available to this NPC now.")
    if req.trust_level <= 1 and req.npc_id != "Lab_Terminal":
        lines.append("Low trust: prefer short, guarded answers and avoid connecting many clues into a full theory.")
    return "\n".join(lines)


def _blocked_by_story_gate(req: NpcChatRequest, text: str) -> bool:
    if req.unlocked_story_level >= 4 or req.npc_id == "Lab_Terminal":
        return False
    lower = text.lower()
    return any(term.lower() in lower for term in SENSITIVE_TERMS)


def _memory_sort_key(item: RetrievedMemoryItem) -> tuple[float, int]:
    return (item.score, item.importance)


def _planned_memory_score(item: RetrievedMemoryItem, overlap: int, bucket: str) -> float:
    bucket_boost = {
        "must_use": 6.0,
        "already_said": 5.0,
        "repeat_or_open_loop": 4.0,
        "reference": 2.0,
        "tone_only": 1.0,
    }.get(bucket, 0.0)
    return float(item.score) + bucket_boost + (overlap * 2.0) + min(max(item.importance, 1), 5)


def _extract_terms(text: str) -> set[str]:
    ascii_terms = {term.lower() for term in re.findall(r"[A-Za-z0-9_]{2,}", text)}
    cjk_terms: set[str] = set()
    for phrase in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        cjk_terms.add(phrase)
        cjk_terms.update(phrase[index : index + 2] for index in range(0, len(phrase) - 1))
    return ascii_terms.union(cjk_terms)


def _normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()
