"""Dynamic Context Compiler：把候选上下文收敛为安全且有预算的 Prompt 材料。

输入可能同时包含线程摘要、长期记忆、近期对话和世界知识。编译器先服从 Turn Plan 的
材料开关，再执行剧情过滤、相关性排序、去重和字符预算，最后产生可审计 Manifest。
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import asdict, dataclass
from typing import Any

from npc_app.contracts import MAX_RETRIEVAL_TOP_K, UnityNpcTurnRequest
from npc_app.dialogue.intent import TERMINAL_NPC_ID, story_text_allowed
from npc_app.dialogue.planner import DialogueStrategy, NpcTurnPlan
from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import RetrievedChunk
from npc_app.utils import dialogue_config, normalized_text, search_terms, truncate_text

_DIALOGUE_CONFIG = dialogue_config()
_CONTEXT_CONFIG = _DIALOGUE_CONFIG["context"]
_RELATIONSHIP_CONFIG = _DIALOGUE_CONFIG["relationship"]

MAX_MEMORY_ITEMS = 8
MAX_CHUNKS = MAX_RETRIEVAL_TOP_K
SUMMARY_CHARS = int(os.getenv("NPC_PROMPT_SUMMARY_CHARS", "500"))
MEMORY_CHARS = int(os.getenv("NPC_PROMPT_MEMORY_ITEMS_CHARS", "900"))
DIALOGUE_CHARS = int(os.getenv("NPC_PROMPT_DIALOGUE_CHARS", "900"))
WORLD_CHARS = int(os.getenv("NPC_PROMPT_WORLD_RAG_CHARS", "1600"))
_STRATEGY_BUDGETS = {strategy: tuple(values) for strategy, values in _CONTEXT_CONFIG["strategy_budgets"].items()}


@dataclass(frozen=True)
class ContextBudget:
    """四类上下文各自独立的字符预算，防止某一来源挤占整个 Prompt。"""

    summary_chars: int
    memory_chars: int
    dialogue_chars: int
    world_chars: int

    def to_dict(self) -> dict[str, int]:
        """返回适合 Trace、测试或诊断输出的预算字典。"""
        return asdict(self)


@dataclass(frozen=True)
class ContextManifest:
    """一次编译的选择清单与排除原因。

    Manifest 保存预算、实际字符数、稳定材料标识和排除原因，不保存完整材料正文；这样
    既能解释“模型本轮看到了什么”，又不会把玩家对话和 Prompt 暴露到结构化 Trace。
    """

    strategy: str
    budget: ContextBudget
    used_chars: dict[str, int]
    selected_chunk_ids: tuple[str, ...] = ()
    selected_memory_keys: tuple[str, ...] = ()
    selected_history_count: int = 0
    excluded: tuple[dict[str, str], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """把不可变 tuple 转为 JSON 友好的 list。"""
        data = asdict(self)
        data["selected_chunk_ids"] = list(self.selected_chunk_ids)
        data["selected_memory_keys"] = list(self.selected_memory_keys)
        data["excluded"] = list(self.excluded)
        return data


@dataclass
class PreparedPromptContext:
    """已经通过安全过滤和预算裁剪、可直接交给 Prompt Builder 的上下文。"""

    summary: str
    memory_items: list[RetrievedMemoryItem]
    dialogue_history: list[tuple[str, str]]
    chunks: list[RetrievedChunk]
    strategy: str
    manifest: ContextManifest


@dataclass(frozen=True)
class _PlannedMemory:
    """记忆在本轮的用途分桶、原始内容与综合排序分。"""

    bucket: str
    item: RetrievedMemoryItem
    score: float


def prepare_prompt_context(
    req: UnityNpcTurnRequest,
    summary: str,
    memory_items: list[RetrievedMemoryItem],
    dialogue_history: list[tuple[str, str]],
    chunks: list[RetrievedChunk],
    turn_plan: NpcTurnPlan,
    max_chunks: int = MAX_CHUNKS,
) -> PreparedPromptContext:
    """筛选剧情安全上下文，并在全局上限与本轮策略预算内完成编译。"""
    baseline = ContextBudget(SUMMARY_CHARS, MEMORY_CHARS, DIALOGUE_CHARS, WORLD_CHARS)
    requested = ContextBudget(*_STRATEGY_BUDGETS[turn_plan.dialogue_strategy])
    # 策略预算只能小于全局基线，配置错误也不能意外放大 Prompt 上下文。
    budget = ContextBudget(
        *(min(value, limit) for value, limit in zip(asdict(requested).values(), asdict(baseline).values(), strict=True))
    )
    excluded: list[dict[str, str]] = []

    # Plan 关闭某类材料时不仅清空内容，还把每个排除项记入 Manifest，便于解释策略行为。
    if not turn_plan.use_memory:
        if summary:
            excluded.append(_excluded("summary", "summary", "policy_disabled"))
        _exclude_items(excluded, "memory", memory_items, "policy_disabled")
        effective_summary = ""
        planned_memories: list[_PlannedMemory] = []
    else:
        effective_summary = truncate_text(summary, budget.summary_chars) if budget.summary_chars > 0 else ""
        planned_memories, memory_excluded = _select_memories(req, memory_items, turn_plan)
        excluded.extend(memory_excluded)

    if not turn_plan.use_dialogue_history:
        _exclude_items(excluded, "history", dialogue_history, "policy_disabled")
        selected_history: list[tuple[str, str]] = []
    else:
        selected_history, history_excluded = _budget_items(
            dialogue_history,
            budget.dialogue_chars,
            "history",
        )
        excluded.extend(history_excluded)

    if not turn_plan.use_rag:
        _exclude_items(excluded, "chunk", chunks, "policy_disabled")
        selected_chunks: list[RetrievedChunk] = []
    else:
        selected_chunks, chunk_excluded = _select_chunks(req, chunks, max_chunks)
        excluded.extend(chunk_excluded)

    # 将记忆用途写入类型前缀，提示模型区分确认事实、未验证说法与纯语气信号。
    bucketed_memories = [
        RetrievedMemoryItem(
            memory_type=f"{memory.bucket}:{memory.item.memory_type}",
            content=memory.item.content,
            keywords=memory.item.keywords,
            importance=memory.item.importance,
            score=memory.score,
        )
        for memory in planned_memories
    ]
    # 分桶/相关性筛选解决“选什么”，统一预算函数再解决“最多放多少”。
    selected_memories, memory_budget_excluded = _budget_items(
        bucketed_memories,
        budget.memory_chars,
        "memory",
    )
    selected_chunks, chunk_budget_excluded = _budget_items(
        selected_chunks,
        budget.world_chars,
        "chunk",
    )
    excluded.extend(memory_budget_excluded)
    excluded.extend(chunk_budget_excluded)

    strategy = _build_strategy(
        req,
        planned_memories,
        selected_chunks,
        bool(effective_summary),
        bool(selected_history),
        turn_plan,
    )
    # Manifest 只记录选择结果和字符用量，供 Trace 审计，不包含完整对话正文。
    manifest = ContextManifest(
        strategy=turn_plan.dialogue_strategy,
        budget=budget,
        used_chars={
            "summary": len(effective_summary),
            "memory": _context_chars("memory", selected_memories),
            "dialogue": _context_chars("history", selected_history),
            "world": _context_chars("chunk", selected_chunks),
        },
        selected_chunk_ids=tuple(_chunk_id(chunk) for chunk in selected_chunks),
        selected_memory_keys=tuple(_memory_key(item) for item in selected_memories),
        selected_history_count=len(selected_history),
        excluded=tuple(excluded),
    )
    return PreparedPromptContext(
        summary=effective_summary,
        memory_items=selected_memories,
        dialogue_history=selected_history,
        chunks=selected_chunks,
        strategy=strategy,
        manifest=manifest,
    )


def _select_memories(
    req: UnityNpcTurnRequest,
    items: list[RetrievedMemoryItem],
    turn_plan: NpcTurnPlan,
) -> tuple[list[_PlannedMemory], list[dict[str, str]]]:
    """按剧情权限、相关性、可信类型和本轮策略为长期记忆分桶排序。

    返回值第一项最多包含 MAX_MEMORY_ITEMS 条候选，第二项记录重复、剧情锁定、低相关或
    数量超限的排除项。综合分由语义/关键词基础分、用途分桶、策略加成、词项重叠和
    importance 共同组成，只影响选择顺序，不改变记忆的事实等级。
    """
    query_terms = search_terms(req.question)
    selected: list[_PlannedMemory] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in sorted(items, key=lambda value: (value.score, value.importance), reverse=True):
        normalized = normalized_text(item.content)
        key = _memory_key(item)
        if not normalized or normalized in seen:
            excluded.append(_excluded("memory", key, "low_relevance"))
            continue
        seen.add(normalized)
        if not story_text_allowed(req.npc_id, req.story.unlock_level, item.content):
            excluded.append(_excluded("memory", key, "story_locked"))
            continue
        overlap = len(query_terms & search_terms(f"{item.content} {item.keywords}"))
        bucket = _memory_bucket(req, item, overlap)
        if bucket == "drop":
            excluded.append(_excluded("memory", key, "low_relevance"))
            continue
        # 策略加成让“质疑玩家”优先看到 claim，让“部分坦白”优先看到 NPC 已披露内容。
        strategy_boost = 0.0
        if (
            (
                turn_plan.dialogue_strategy == DialogueStrategy.CHALLENGE_PLAYER.value
                and item.memory_type.lower() == "player_claim"
            )
            or (
                turn_plan.dialogue_strategy == DialogueStrategy.TELL_PARTIAL_TRUTH.value
                and item.memory_type.lower() == "npc_disclosed"
            )
        ):
            strategy_boost = 8.0
        # 分桶分值表达使用优先级；overlap 和 importance 提供当前问题相关性与长期重要性修正。
        score = item.score + {
            "must_use": 6.0,
            "already_said": 5.0,
            "repeat_or_open_loop": 4.0,
            "unverified_claim": 3.0,
            "reference": 2.0,
            "tone_only": 1.0,
        }.get(bucket, 0.0) + strategy_boost + overlap * 2.0 + item.importance
        selected.append(_PlannedMemory(bucket, item, score))
    ranked = sorted(selected, key=lambda value: value.score, reverse=True)
    for memory in ranked[MAX_MEMORY_ITEMS:]:
        excluded.append(_excluded("memory", _memory_key(memory.item), "budget_exceeded"))
    return ranked[:MAX_MEMORY_ITEMS], excluded


def _select_chunks(
    req: UnityNpcTurnRequest,
    chunks: list[RetrievedChunk],
    max_chunks: int,
) -> tuple[list[RetrievedChunk], list[dict[str, str]]]:
    """再次执行去重和解锁等级检查，形成 Prompt 前的纵深防护。

    Milvus 已按 unlock_level 查询，但这里不信任外部检索层的唯一正确性；编译器仍独立
    拒绝高等级 Chunk，并把去重和数量上限的原因写入 Manifest。
    """
    selected: list[RetrievedChunk] = []
    excluded: list[dict[str, str]] = []
    seen: set[str] = set()
    for chunk in chunks:
        key = normalized_text(f"{chunk.source_file}:{chunk.section_title}:{chunk.content[:120]}")
        chunk_id = _chunk_id(chunk)
        if not key or key in seen:
            excluded.append(_excluded("chunk", chunk_id, "low_relevance"))
            continue
        if chunk.unlock_level > req.story.unlock_level:
            excluded.append(_excluded("chunk", chunk_id, "story_locked"))
            continue
        seen.add(key)
        selected.append(chunk)
    limit = max_chunks
    for chunk in selected[limit:]:
        excluded.append(_excluded("chunk", _chunk_id(chunk), "budget_exceeded"))
    return selected[:limit], excluded


def _memory_bucket(req: UnityNpcTurnRequest, item: RetrievedMemoryItem, overlap: int) -> str:
    """根据记忆类型、相关性、重要度和厌烦状态决定本轮使用语义。"""
    # player_fact 来自 Unity 结构化证据；player_claim 仅是对话提取结果，不能当作已确认事实。
    memory_type = item.memory_type.lower()
    importance = item.importance
    if memory_type == "relationship_signal":
        return "tone_only"
    if memory_type == "unresolved_question":
        repeat_threshold = int(_RELATIONSHIP_CONFIG["repeat_memory_annoyance"])
        repeated = overlap or importance >= 4 or req.relationship.annoyance >= repeat_threshold
        return "repeat_or_open_loop" if repeated else "drop"
    if memory_type == "npc_disclosed":
        return "already_said" if overlap or importance >= 4 else "reference"
    if memory_type == "player_fact":
        return "must_use" if overlap or importance >= 4 else "reference"
    if memory_type == "player_claim":
        return "unverified_claim" if overlap or importance >= 4 else "drop"
    return "reference" if overlap or importance >= 5 else "drop"


def _budget_items(items: list[Any], max_chars: int, kind: str) -> tuple[list[Any], list[dict[str, str]]]:
    """在字符预算内裁剪上下文，并保留所有排除原因用于审计。

    记忆按综合分降序，历史从最新向前尝试但返回时恢复时间顺序，Chunk 保持检索顺序。
    溢出时跳过过大项继续寻找后续可容纳项；单项内容还会先按类型上限截断，避免一个
    候选独占整个类别预算。
    """
    if max_chars <= 0:
        if kind == "memory":
            ids = [_memory_key(item) for item in items]
        elif kind == "chunk":
            ids = [_chunk_id(item) for item in items]
        else:
            ids = [str(index) for index in range(len(items))]
        return [], [_excluded(kind, item_id, "budget_exceeded") for item_id in ids]
    candidates = sorted(items, key=lambda value: value.score, reverse=True) if kind == "memory" else items
    if kind == "history":
        candidates = list(reversed(items))
    selected: list[Any] = []
    excluded: list[dict[str, str]] = []
    used = 0
    for index, item in enumerate(candidates):
        if kind == "memory":
            prepared = RetrievedMemoryItem(
                item.memory_type,
                truncate_text(item.content, 220),
                truncate_text(item.keywords, 120),
                item.importance,
                item.score,
            )
            item_id = _memory_key(item)
        elif kind == "history":
            prepared = (truncate_text(item[0], 180), truncate_text(item[1], 220))
            item_id = str(len(items) - index - 1)
        else:
            prepared = RetrievedChunk(
                source_file=item.source_file,
                section_title=item.section_title,
                content=truncate_text(item.content, 500),
                npc_id=item.npc_id,
                unlock_level=item.unlock_level,
                topics=item.topics,
                spoiler_level=item.spoiler_level,
                score=item.score,
            )
            item_id = _chunk_id(item)
        # 成本包含正文和格式化时必然出现的元数据，预算更接近最终 Prompt 的真实占用。
        cost = _context_chars(kind, [prepared])
        if used + cost > max_chars:
            excluded.append(_excluded(kind, item_id, "budget_exceeded"))
            continue
        selected.append(prepared)
        used += cost
    return (list(reversed(selected)) if kind == "history" else selected), excluded


def _build_strategy(
    req: UnityNpcTurnRequest,
    memories: list[_PlannedMemory],
    chunks: list[RetrievedChunk],
    has_summary: bool,
    has_dialogue: bool,
    turn_plan: NpcTurnPlan,
) -> str:
    """根据实际入选材料生成给模型阅读的上下文使用说明。"""
    buckets = {memory.bucket for memory in memories}
    lines = list(_CONTEXT_CONFIG["base_guidance"])
    hints = _CONTEXT_CONFIG["memory_bucket_guidance"]
    lines.extend(hints[bucket] for bucket in hints.keys() & buckets)
    lines.append(f"Compile context for the {turn_plan.dialogue_strategy} dialogue strategy.")
    if has_summary:
        lines.append("The thread summary is continuity, not new world lore.")
    if has_dialogue:
        lines.append("Recent dialogue resolves references but cannot unlock knowledge.")
    if chunks:
        lines.append("World chunks are reference material available to this NPC now.")
    if req.relationship.trust <= 1 and req.npc_id != TERMINAL_NPC_ID:
        lines.append("Low trust: remain short and guarded; do not connect a full theory.")
    return "\n".join(lines)


def _memory_key(item: RetrievedMemoryItem) -> str:
    """用类型与规范化内容摘要生成不暴露正文的稳定记忆标识。"""
    digest = hashlib.sha256(normalized_text(item.content).encode("utf-8")).hexdigest()[:12]
    return f"{item.memory_type}:{digest}"


def _chunk_id(chunk: RetrievedChunk) -> str:
    """用来源文件和章节标题构造可回溯的世界知识标识。"""
    return f"{chunk.source_file}#{chunk.section_title}"


def _context_chars(kind: str, items: list[Any]) -> int:
    """估算各类材料格式化后的字符成本，包括必要标签和元数据开销。"""
    if kind == "memory":
        return sum(len(item.content) + len(item.memory_type) + len(item.keywords) + 24 for item in items)
    if kind == "history":
        return sum(len(question) + len(answer) + 16 for question, answer in items)
    return sum(len(item.content) + len(item.source_file) + len(item.section_title) + 48 for item in items)


def _excluded(kind: str, item_id: str, reason: str) -> dict[str, str]:
    """构造统一的 Manifest 排除记录。"""
    return {"kind": kind, "id": item_id, "reason": reason}


def _exclude_items(excluded: list[dict[str, str]], kind: str, items: list[Any], reason: str) -> None:
    """批量记录某一来源因 Policy 等统一原因被排除。"""
    for index, item in enumerate(items):
        if kind == "memory":
            item_id = _memory_key(item)
        elif kind == "chunk":
            item_id = _chunk_id(item)
        else:
            item_id = str(index)
        excluded.append(_excluded(kind, item_id, reason))
