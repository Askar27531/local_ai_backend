from __future__ import annotations

import os
from dataclasses import dataclass, field

from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import RetrievedChunk


SUMMARY_CHARS = int(os.getenv("NPC_PROMPT_SUMMARY_CHARS", "500"))
MEMORY_ITEMS_CHARS = int(os.getenv("NPC_PROMPT_MEMORY_ITEMS_CHARS", "900"))
DIALOGUE_CHARS = int(os.getenv("NPC_PROMPT_DIALOGUE_CHARS", "900"))
WORLD_RAG_CHARS = int(os.getenv("NPC_PROMPT_WORLD_RAG_CHARS", "1600"))


@dataclass
class BudgetedPromptContext:
    summary: str = ""
    memory_items: list[RetrievedMemoryItem] = field(default_factory=list)
    dialogue_history: list[tuple[str, str]] = field(default_factory=list)
    chunks: list[RetrievedChunk] = field(default_factory=list)


def budget_prompt_context(
    summary: str,
    memory_items: list[RetrievedMemoryItem],
    dialogue_history: list[tuple[str, str]],
    chunks: list[RetrievedChunk],
) -> BudgetedPromptContext:
    return BudgetedPromptContext(
        summary=_truncate(summary, SUMMARY_CHARS),
        memory_items=_budget_memory_items(memory_items, MEMORY_ITEMS_CHARS),
        dialogue_history=_budget_dialogue(dialogue_history, DIALOGUE_CHARS),
        chunks=_budget_chunks(chunks, WORLD_RAG_CHARS),
    )


def _budget_memory_items(
    items: list[RetrievedMemoryItem],
    max_chars: int,
) -> list[RetrievedMemoryItem]:
    selected: list[RetrievedMemoryItem] = []
    used = 0
    for item in sorted(items, key=lambda value: value.score, reverse=True):
        content = _truncate(item.content, 220)
        cost = len(content) + len(item.memory_type) + len(item.keywords) + 24
        if selected and used + cost > max_chars:
            break
        selected.append(
            RetrievedMemoryItem(
                memory_type=item.memory_type,
                content=content,
                keywords=_truncate(item.keywords, 120),
                importance=item.importance,
                score=item.score,
            )
        )
        used += cost
    return selected


def _budget_dialogue(
    history: list[tuple[str, str]],
    max_chars: int,
) -> list[tuple[str, str]]:
    selected_reversed: list[tuple[str, str]] = []
    used = 0
    for question, answer in reversed(history):
        question_text = _truncate(question, 180)
        answer_text = _truncate(answer, 220)
        cost = len(question_text) + len(answer_text) + 16
        if selected_reversed and used + cost > max_chars:
            break
        selected_reversed.append((question_text, answer_text))
        used += cost
    return list(reversed(selected_reversed))


def _budget_chunks(
    chunks: list[RetrievedChunk],
    max_chars: int,
) -> list[RetrievedChunk]:
    selected: list[RetrievedChunk] = []
    used = 0
    for chunk in chunks:
        content = _truncate(chunk.content, 500)
        cost = len(content) + len(chunk.source_file) + len(chunk.section_title) + 48
        if selected and used + cost > max_chars:
            break
        selected.append(
            RetrievedChunk(
                source_file=chunk.source_file,
                section_title=chunk.section_title,
                content=content,
                npc_id=chunk.npc_id,
                unlock_level=chunk.unlock_level,
                topics=chunk.topics,
                spoiler_level=chunk.spoiler_level,
                score=chunk.score,
            )
        )
        used += cost
    return selected


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"
