"""单轮 NPC 对话的线性编排器。

调用顺序固定为硬拒答 → 意图/Policy → 低风险快速回答 → Turn Plan → RAG → Context →
Prompt → LLM 生成 → 答案守卫。函数只产出 NDJSON 字符串并填充状态，数据库写入由 API 层
在确认获得有效答案后执行。
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass, field
from time import perf_counter

from langchain_core.messages import HumanMessage, SystemMessage

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.dialogue.characters import basic_response, character_dialogue
from npc_app.dialogue.context import ContextManifest, prepare_prompt_context
from npc_app.dialogue.intent import TERMINAL_NPC_ID, NpcTurnPolicy, build_turn_policy, classify_npc_intent
from npc_app.dialogue.planner import NpcTurnPlan, build_turn_plan
from npc_app.services.llm_service import llm
from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import (
    RetrievedChunk,
    retrieve_conditional_game_chunks,
)
from npc_app.services.npc_prompt_service import (
    build_annoyed_refusal,
    build_npc_system_prompt,
    build_npc_user_prompt,
    build_unknown_answer,
)
from npc_app.trace import NpcTurnTrace
from npc_app.utils import contains_any, dialogue_config, message_text

_DIALOGUE_CONFIG = dialogue_config()
_RELATIONSHIP_CONFIG = _DIALOGUE_CONFIG["relationship"]
_LIRA_CONFIG = _DIALOGUE_CONFIG["npc_guards"]["Lira"]


@dataclass
class NpcRagStreamState:
    """跨生成器边界回传给 API 层的单轮结果。

    answer 决定 API 是否持久化，used_rag/sources 记录检索结果，turn_plan 和
    context_manifest 为测试、评估或诊断保留本轮决策依据。
    """

    answer: str = ""
    used_rag: bool = False
    sources: list[dict] = field(default_factory=list)
    turn_plan: NpcTurnPlan | None = None
    context_manifest: ContextManifest | None = None


@dataclass(frozen=True)
class RetrievalResult:
    """把检索成功、空命中和异常统一成无需抛出的结果对象。"""

    chunks: list[RetrievedChunk] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    error: str = ""


def run_npc_rag_chat_stream(
    req: UnityNpcTurnRequest,
    stream_state: NpcRagStreamState | None = None,
    dialogue_history: list[tuple[str, str]] | None = None,
    memory_summary: str = "",
    memory_items: list[RetrievedMemoryItem] | None = None,
    turn_trace: NpcTurnTrace | None = None,
) -> Iterator[str]:
    """按固定线性顺序执行单轮 NPC 对话，并持续产出 NDJSON 事件。"""
    state = stream_state or NpcRagStreamState()
    trace = turn_trace
    available_history = list(dialogue_history or ())
    available_memories = list(memory_items or ())

    # 满厌烦值在意图识别和检索前直接拒答，既降低成本也避免继续暴露剧情信息。
    if req.relationship.annoyance >= int(_RELATIONSHIP_CONFIG["refusal_annoyance"]):
        if trace:
            trace.dialogue_strategy = "refuse"
        answer = build_annoyed_refusal(req)
        yield from _stream_static_answer(
            state,
            answer,
            {
                "message": (
                    f"{req.npc_id} annoyance is {req.relationship.annoyance}%; "
                    "this turn is refused before retrieval."
                )
            },
            {"retrieved_count": 0, "answer_length": len(answer), "annoyance_percent": req.relationship.annoyance},
        )
        return

    # 从此处开始的 Policy、Plan、检索与守卫逐层收紧；后层不能扩大前层授予的权限。
    intent = classify_npc_intent(req)
    policy = build_turn_policy(intent, req)
    if trace:
        trace.intent = intent.intent
        trace.policy_source = intent.source
    basic_answer = basic_response(req.npc_id, intent.intent)
    # 问候、身份和职责等低风险意图使用角色固定回答，无需让模型接触世界知识。
    if basic_answer:
        if trace:
            trace.dialogue_strategy = "answer_directly"
        yield from _stream_static_answer(
            state,
            basic_answer,
            {
                "message": f"{req.npc_id} handled as {policy.intent}; skipped story retrieval.",
                "intent": intent.to_dict(),
                "policy": policy.to_dict(),
            },
            {"retrieved_count": 0, "answer_length": len(basic_answer)},
        )
        return

    # Planner 只决定本轮如何表达；可访问的剧情范围仍由 Policy 和 Unity 状态决定。
    planning_started = perf_counter()
    plan = build_turn_plan(intent, policy, req, available_history, available_memories)
    if trace:
        trace.planning_ms = round((perf_counter() - planning_started) * 1000, 3)
        trace.plan_source = plan.source
        trace.arc_stage = plan.arc_stage
        trace.dialogue_strategy = plan.dialogue_strategy
    state.turn_plan = plan
    # 尽早按 Plan 清空禁用来源，避免无用材料继续进入检索查询或 Context Compiler。
    history = available_history if plan.use_dialogue_history else []
    memories = available_memories if plan.use_memory else []
    summary = memory_summary if plan.use_memory else ""

    yield make_npc_stream_event(
        "status",
        {
            "message": f"Retrieving story context for {req.npc_id} ({policy.intent})...",
            "intent": intent.to_dict(),
            "policy": policy.to_dict(),
            "plan": plan.to_dict(),
        },
    )
    retrieval_started = perf_counter()
    retrieval = _retrieve_context(req, policy, plan, history)
    if trace:
        trace.retrieval_ms = round((perf_counter() - retrieval_started) * 1000, 3)
        trace.retrieved_count = len(retrieval.chunks)
        trace.selected_chunk_ids = [f"{chunk.source_file}#{chunk.section_title}" for chunk in retrieval.chunks]
    if retrieval.error:
        # 检索失败时不调用生成模型，避免在缺少受控依据的情况下编造世界设定。
        if trace:
            trace.error_stage = "retrieval"
        yield make_npc_stream_event("error", {"message": f"Milvus retrieval failed: {retrieval.error}"})
        yield make_npc_stream_event(
            "done",
            {"retrieved_count": 0, "answer_length": 0, "intent": intent.to_dict()},
        )
        return

    state.sources = retrieval.sources
    state.used_rag = bool(retrieval.chunks)
    yield make_npc_stream_event(
        "sources",
        {"retrieved_count": len(retrieval.chunks), "sources": retrieval.sources},
    )

    # Context Compiler 是 Prompt Builder 前的唯一材料入口，并返回不含正文的选择清单。
    system_prompt, user_prompt, context_manifest = _prepare_prompt(
        req,
        plan,
        history,
        summary,
        memories,
        retrieval.chunks,
    )
    state.context_manifest = context_manifest
    if trace:
        trace.selected_chunk_ids = list(context_manifest.selected_chunk_ids)
        trace.selected_memory_keys = list(context_manifest.selected_memory_keys)
        trace.prompt_chars = len(system_prompt) + len(user_prompt)
    try:
        generation_started = perf_counter()
        # provider 可能返回字符串或分块 content，统一转文本后拼接为完整成稿再执行守卫。
        answer = "".join(
            message_text(token)
            for token in llm.stream([SystemMessage(content=system_prompt), HumanMessage(content=user_prompt)])
        )
        if trace:
            trace.generation_ms = round((perf_counter() - generation_started) * 1000, 3)
    except Exception as exc:
        if trace:
            trace.generation_ms = round((perf_counter() - generation_started) * 1000, 3)
            trace.error_stage = "generation"
        yield make_npc_stream_event("error", {"message": f"Local model generation failed: {exc}"})
        yield make_npc_stream_event(
            "done",
            {
                "retrieved_count": len(retrieval.chunks),
                "answer_length": 0,
                "intent": intent.to_dict(),
                "plan": plan.to_dict(),
            },
        )
        return

    # 生成后的确定性守卫是最后一道边界，发现越权内容时直接替换整段回答。
    answer, guard_result = _guard_answer(req, policy, answer)
    if trace:
        trace.guard_result = guard_result
    state.answer = answer
    yield make_npc_stream_event("answer_delta", {"text": answer})
    yield make_npc_stream_event(
        "done",
        {
            "retrieved_count": len(retrieval.chunks),
            "answer_length": len(answer.strip()),
            "intent": intent.to_dict(),
        },
    )


def _stream_static_answer(state: NpcRagStreamState, answer: str, status: dict, done: dict) -> Iterator[str]:
    """按 status → answer_delta → done 输出无需 RAG/LLM 的确定性回答。"""
    state.answer = answer
    yield make_npc_stream_event("status", status)
    yield make_npc_stream_event("answer_delta", {"text": answer})
    yield make_npc_stream_event("done", done)


def _retrieve_context(
    req: UnityNpcTurnRequest,
    policy: NpcTurnPolicy,
    plan: NpcTurnPlan,
    dialogue_history: list[tuple[str, str]],
) -> RetrievalResult:
    """根据 Policy 与 Plan 构造查询并执行受限世界知识检索。

    Plan 关闭 RAG 时返回成功空结果；Milvus 或嵌入/重排异常被转换为 error 字符串，交由
    主流程发送 error/done，而不是在流式响应中抛出未编码异常。
    """
    if not plan.use_rag:
        return RetrievalResult()

    question = _build_retrieval_question(req, policy, dialogue_history)
    try:
        chunks = retrieve_conditional_game_chunks(
            question=question,
            npc_id=req.npc_id,
            unlocked_story_level=req.story.unlock_level,
            intent=policy.intent,
            top_k=plan.retrieval_top_k,
            allowed_source_prefixes=policy.allowed_source_prefixes,
            forbidden_context_terms=policy.forbidden_context_terms,
        )
    except Exception as exc:
        return RetrievalResult(error=str(exc))

    chunks = _filter_npc_context(req, chunks)
    return RetrievalResult(chunks=chunks, sources=[_chunk_to_source(chunk) for chunk in chunks])


_LIRA_CLINICAL_TERMS = tuple(_LIRA_CONFIG["clinical_terms"])


def _filter_npc_context(req: UnityNpcTurnRequest, chunks: list[RetrievedChunk]) -> list[RetrievedChunk]:
    """在早期剧情中把 Lira 的上下文限制在临床证据和观察职责内。"""
    if req.npc_id != "Lira" or req.story.unlock_level >= int(_LIRA_CONFIG["active_until_story_unlock"]):
        return chunks
    return [
        chunk
        for chunk in chunks
        if contains_any(f"{chunk.section_title}\n{chunk.topics}\n{chunk.content}", _LIRA_CLINICAL_TERMS)
    ]


def _prepare_prompt(
    req: UnityNpcTurnRequest,
    plan: NpcTurnPlan,
    dialogue_history: list[tuple[str, str]],
    summary: str,
    memories: list[RetrievedMemoryItem],
    chunks: list[RetrievedChunk],
) -> tuple[str, str, ContextManifest]:
    """先由 Context Compiler 收紧材料，再组装 System/User Prompt。"""
    prepared = prepare_prompt_context(
        req=req,
        summary=summary,
        memory_items=memories,
        dialogue_history=dialogue_history,
        chunks=chunks,
        max_chunks=plan.retrieval_top_k,
        turn_plan=plan,
    )
    return (
        build_npc_system_prompt(
            req,
            prepared.chunks,
            dialogue_history=prepared.dialogue_history,
            memory_summary=prepared.summary,
            memory_items=prepared.memory_items,
            context_strategy=prepared.strategy,
            turn_plan=plan,
        ),
        build_npc_user_prompt(req),
        prepared.manifest,
    )


def _guard_answer(req: UnityNpcTurnRequest, policy: NpcTurnPolicy, answer: str) -> tuple[str, str]:
    """对模型成稿执行角色专业边界和剧情敏感词复查。"""
    if _lira_unsupported_direction(req, answer):
        return str(_LIRA_CONFIG["replacement_answer"]), "replaced_lira_scope"
    if not policy.answer_guard_terms or not contains_any(answer, policy.answer_guard_terms):
        return answer, "passed"
    guarded = basic_response(req.npc_id, policy.intent) or build_unknown_answer(req.npc_id)
    return guarded, "replaced"


_LIRA_RESTRICTED_DIRECTIONS = tuple(_LIRA_CONFIG["restricted_directions"])


def _lira_unsupported_direction(req: UnityNpcTurnRequest, answer: str) -> bool:
    """判断 Lira 的方向性建议是否缺少玩家状态或问题中的临床证据。"""
    if req.npc_id != "Lira" or not contains_any(answer, _LIRA_RESTRICTED_DIRECTIONS):
        return False
    evidence = "\n".join(
        [
            req.question,
            req.player.location or "",
            req.player.current_quest or "",
            *req.player.presented_items,
            *req.player.known_clues,
            *req.player.mentioned_clues,
        ]
    )
    return not contains_any(evidence, tuple(_LIRA_CONFIG["direction_evidence_terms"]))


def _build_retrieval_question(
    req: UnityNpcTurnRequest,
    policy: NpcTurnPolicy,
    dialogue_history: list[tuple[str, str]],
) -> str:
    """把角色召回词、信任提示、近期对话和本轮显式状态合成为检索问题。

    只加入玩家本轮展示/提及的强相关状态，不把完整背包和全部已知线索无差别塞入查询；
    最近两轮用于解决“它、那里、刚才那个”等追问指代。
    """
    parts = [req.question]
    if dialogue_history:
        recent = " | ".join(f"player: {question} npc: {answer}" for question, answer in dialogue_history[-2:])
        parts.insert(0, f"recent_dialogue: {recent}")
    if req.player.presented_items:
        parts.append("presented_items: " + ", ".join(req.player.presented_items))
    if req.player.mentioned_clues:
        parts.append("mentioned_clues: " + ", ".join(req.player.mentioned_clues))
    if req.player.current_quest:
        parts.append(f"current_quest: {req.player.current_quest}")
    if req.npc_id != TERMINAL_NPC_ID:
        parts.insert(0, character_dialogue(req.npc_id).retrieval_terms)
        trust_hint = _retrieval_trust_hint(req.relationship.trust)
        if trust_hint:
            parts.insert(0, trust_hint)
    return "\n".join(parts)


def _retrieval_trust_hint(trust_level: int) -> str:
    """把离散信任等级转换为召回侧提示，影响相关性而不改变解锁过滤。"""
    hints = _RELATIONSHIP_CONFIG["retrieval_trust_hints"]
    if trust_level <= 1:
        return str(hints["low"])
    if trust_level == 2:
        return str(hints["early"])
    if trust_level >= 4:
        return str(hints["high"])
    return ""


def _chunk_to_source(chunk: RetrievedChunk) -> dict:
    """转换为 Unity 可见的来源摘要，不返回完整知识正文。"""
    return {
        "source_file": chunk.source_file,
        "section_title": chunk.section_title,
        "npc_id": chunk.npc_id,
        "unlock_level": chunk.unlock_level,
        "topics": chunk.topics,
        "spoiler_level": chunk.spoiler_level,
        "score": chunk.score,
        "snippet": chunk.snippet,
    }


def make_npc_stream_event(event_type: str, data: dict) -> str:
    """编码单条 UTF-8 友好的 NDJSON 事件，并追加协议要求的换行符。"""
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=False) + "\n"
