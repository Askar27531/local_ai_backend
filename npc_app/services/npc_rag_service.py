from __future__ import annotations

import json
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Annotated, Any, TypedDict

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langgraph.config import get_stream_writer
from langgraph.graph import END, START, StateGraph, add_messages

from npc_app.schemas.chat import SearchItem
from npc_app.services.llm_service import llm
from npc_app.schemas.npc_chat import NpcChatRequest
from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import RetrievedChunk, retrieve_game_chunks
from npc_app.services.npc_prompt_service import (
    build_annoyed_refusal,
    build_npc_system_prompt,
    build_npc_user_prompt,
    build_unknown_answer,
)
from npc_app.services.npc_context_planner_service import plan_prompt_context
from npc_app.services.prompt_budget_service import budget_prompt_context

class NpcGraphState(TypedDict, total=False):
    messages: Annotated[list[AnyMessage], add_messages]
    request: dict[str, Any]
    dialogue_history: list[tuple[str, str]]
    memory_items: list[dict[str, Any]]
    chunks: list[dict[str, Any]]
    sources: list[dict[str, Any]]
    system_prompt: str
    user_prompt: str
    answer: str
    used_rag: bool
    summary: str
    error: str


@dataclass
class NpcRagStreamState:
    answer: str = ""
    used_rag: bool = False
    sources: list[SearchItem] = field(default_factory=list)


def run_npc_rag_chat_stream(
    req: NpcChatRequest,
    stream_state: NpcRagStreamState | None = None,
    dialogue_history: list[tuple[str, str]] | None = None,
    memory_summary: str = "",
    memory_items: list[RetrievedMemoryItem] | None = None,
):
    if stream_state is None:
        stream_state = NpcRagStreamState()

    if req.annoyance_percent >= 100:
        answer = build_annoyed_refusal(req)
        stream_state.answer = answer
        yield make_npc_stream_event(
            "status",
            {
                "message": (
                    f"{req.npc_id} 的厌烦值已到 {req.annoyance_percent}%，"
                    "本轮拒绝继续解释。"
                )
            },
        )
        yield make_npc_stream_event("answer_delta", {"text": answer})
        yield make_npc_stream_event(
            "done",
            {
                "retrieved_count": 0,
                "answer_length": len(answer),
                "annoyance_percent": req.annoyance_percent,
            },
        )
        return

    graph = _build_npc_graph()
    config = {
        "configurable": {
            "npc_id": req.npc_id,
        }
    }
    initial_state: NpcGraphState = {
        "request": req.model_dump(),
        "dialogue_history": dialogue_history or [],
        "summary": memory_summary,
        "memory_items": [_memory_item_to_dict(item) for item in memory_items or []],
        "messages": [HumanMessage(content=req.question)],
    }

    retrieved_count = 0

    for chunk in graph.stream(
        initial_state,
        config=config,
        stream_mode=["custom", "updates"],
    ):
        mode, data = _normalize_graph_stream_part(chunk)

        if mode == "custom":
            if not isinstance(data, dict):
                yield make_npc_stream_event("status", {"message": str(data)})
                continue

            event_type = data.get("type", "custom")
            event_data = data.get("data", {})

            if event_type == "sources":
                sources = event_data.get("sources", [])
                retrieved_count = int(event_data.get("retrieved_count", len(sources)))
                stream_state.sources = [_source_dict_to_search_item(source) for source in sources]
                stream_state.used_rag = retrieved_count > 0
            elif event_type == "answer_delta":
                text = str(event_data.get("text", ""))
                stream_state.answer += text

            yield make_npc_stream_event(event_type, event_data)
            continue

        if mode == "updates" and isinstance(data, dict):
            generate_update = data.get("generate_answer")
            if isinstance(generate_update, dict):
                answer = str(generate_update.get("answer", ""))
                if answer and not stream_state.answer:
                    stream_state.answer = answer

    yield make_npc_stream_event(
        "done",
        {
            "retrieved_count": retrieved_count,
            "answer_length": len(stream_state.answer.strip()),
        },
    )


@lru_cache(maxsize=1)
def _build_npc_graph():
    graph = StateGraph(NpcGraphState)
    graph.add_node("retrieve_context", _retrieve_context_node)
    graph.add_node("build_prompt", _build_prompt_node)
    graph.add_node("generate_answer", _generate_answer_node)

    graph.add_edge(START, "retrieve_context")
    graph.add_edge("retrieve_context", "build_prompt")
    graph.add_edge("build_prompt", "generate_answer")
    graph.add_edge("generate_answer", END)

    return graph.compile()


def _retrieve_context_node(state: NpcGraphState) -> dict[str, Any]:
    writer = get_stream_writer()
    req = NpcChatRequest.model_validate(state["request"])

    writer(
        {
            "type": "status",
            "data": {"message": f"正在检索 {req.npc_id} 可访问的记忆..."},
        }
    )

    try:
        chunks = retrieve_game_chunks(
            question=_build_retrieval_question(req),
            npc_id=req.npc_id,
            unlocked_story_level=req.unlocked_story_level,
            top_k=req.top_k,
            candidate_k=_candidate_k_for_request(req),
        )
        chunks = _rerank_chunks_for_request(req, chunks)[: req.top_k]
    except Exception as exc:
        writer(
            {
                "type": "error",
                "data": {"message": f"Milvus 检索失败：{exc}"},
            }
        )
        return {
            "error": str(exc),
            "chunks": [],
            "sources": [],
            "used_rag": False,
        }

    chunk_dicts = [_chunk_to_dict(chunk) for chunk in chunks]
    source_dicts = [_chunk_to_source(chunk) for chunk in chunks]

    writer(
        {
            "type": "sources",
            "data": {
                "retrieved_count": len(chunks),
                "sources": source_dicts,
            },
        }
    )

    return {
        "chunks": chunk_dicts,
        "sources": source_dicts,
        "used_rag": bool(chunks),
    }


def _build_retrieval_question(req: NpcChatRequest) -> str:
    """
    Bias retrieval toward relationship-appropriate examples.
    Story level still controls access; trust level only changes tone/style anchors.
    """
    parts = [req.question]

    if req.npc_id != "Lab_Terminal":
        parts.insert(0, _npc_retrieval_voice_terms(req.npc_id))
        if req.trust_level <= 1:
            parts.insert(
                0,
                "低信任阶段 当前NPC个人口吻 陌生人问敏感问题 半答 不主动解释 不给攻略",
            )
        elif req.trust_level == 2:
            parts.insert(
                0,
                "初步信任 谨慎建议 只解释自己领域内的一个观察",
            )
        elif req.trust_level >= 4:
            parts.insert(
                0,
                "深度信任 个人立场 代价 犹豫 不替玩家选择",
            )

    return "\n".join(parts)


def _npc_retrieval_voice_terms(npc_id: str) -> str:
    terms = {
        "Karo": "Karo 老渔夫 码头 船 罗盘 海雾 旧规矩 短硬",
        "Lira": "Lira 草药师 症状 接触时间 样本 病历 诊断克制 外来词 不问海雾 不转给传闻",
        "Orin": "Orin 守塔人 门 锁 钥匙 规矩 下层 后果",
        "Nia": "Nia 孩子 井边 贝壳 风铃 蓝眼睛 不懂怪词",
        "Venn": "Venn 破碎研究者 纸页 地图 接口片 终端 不可靠记忆",
        "Elder_Mara": "Elder Mara 长者 祭歌 骨 禁忌 潮民 代价",
    }
    return terms.get(npc_id, npc_id)


def _candidate_k_for_request(req: NpcChatRequest) -> int:
    if req.npc_id != "Lab_Terminal" and req.trust_level <= 1:
        return max(req.top_k * 3, 12)
    return req.top_k


def _rerank_chunks_for_request(
    req: NpcChatRequest,
    chunks: list[RetrievedChunk],
) -> list[RetrievedChunk]:
    if req.npc_id == "Lab_Terminal" or req.trust_level > 1:
        return chunks

    def adjusted_score(chunk: RetrievedChunk) -> float:
        score = chunk.score
        text = f"{chunk.source_file}\n{chunk.section_title}\n{chunk.topics}\n{chunk.content}"

        if chunk.source_file == "19_dialogue_examples_NPC对话样例.md":
            score += 0.10
        if _npc_section_marker(req.npc_id) in text:
            score += 0.08
        if "低信任阶段" in chunk.section_title or "陌生人" in chunk.content:
            score += 0.06
        if chunk.source_file in {
            "16_daily_life_静潮村日常.md",
            "20_village_events_村庄事件.md",
            "24_player_choice_consequences_玩家行为后果.md",
        }:
            score += 0.04

        if chunk.source_file in {
            "01_world_lore_世界观.md",
            "02_crystal_vein_水晶矿脉.md",
            "04_shadow_organization_反政府科技组织.md",
            "05_player_background_主人公背景.md",
            "10_hidden_truth_隐藏真相.md",
        }:
            score -= 0.08

        if any(term in text for term in ["归潮场", "Project ECHO", "Subject 07", "主共振核心", "保护仓"]):
            score -= 0.06

        return score

    return sorted(chunks, key=adjusted_score, reverse=True)


def _npc_section_marker(npc_id: str) -> str:
    markers = {
        "Karo": "Karo",
        "Lira": "Lira",
        "Orin": "Orin",
        "Nia": "Nia",
        "Venn": "Venn",
        "Elder_Mara": "Mara",
        "Lab_Terminal": "Lab Terminal",
    }
    return markers.get(npc_id, npc_id)


def _build_prompt_node(state: NpcGraphState) -> dict[str, Any]:
    if state.get("error"):
        return {}

    req = NpcChatRequest.model_validate(state["request"])
    chunks = [_dict_to_chunk(chunk) for chunk in state.get("chunks", [])]
    memory_items = [_dict_to_memory_item(item) for item in state.get("memory_items", [])]
    graph_history = _messages_to_dialogue_history(state.get("messages", []))
    dialogue_history = graph_history or state.get("dialogue_history", [])
    planned = plan_prompt_context(
        req=req,
        summary=str(state.get("summary", "")),
        memory_items=memory_items,
        dialogue_history=dialogue_history,
        chunks=chunks,
    )
    budgeted = budget_prompt_context(
        summary=planned.summary,
        memory_items=planned.memory_items,
        dialogue_history=planned.dialogue_history,
        chunks=planned.chunks,
    )

    return {
        "system_prompt": build_npc_system_prompt(
            req,
            budgeted.chunks,
            dialogue_history=budgeted.dialogue_history,
            memory_summary=budgeted.summary,
            memory_items=budgeted.memory_items,
            context_strategy=planned.strategy,
        ),
        "user_prompt": build_npc_user_prompt(req),
    }


def _generate_answer_node(state: NpcGraphState) -> dict[str, Any]:
    if state.get("error"):
        return {"answer": ""}

    writer = get_stream_writer()
    req = NpcChatRequest.model_validate(state["request"])
    chunks = state.get("chunks", [])

    has_any_context = bool(
        chunks
        or state.get("memory_items")
        or state.get("summary")
        or state.get("dialogue_history")
    )
    if not has_any_context:
        answer = build_unknown_answer(req.npc_id)
        writer({"type": "answer_delta", "data": {"text": answer}})
        return {
            "answer": answer,
            "messages": [AIMessage(content=answer)],
        }

    answer = ""
    try:
        for token in llm.stream(
            [
                SystemMessage(content=str(state.get("system_prompt", ""))),
                HumanMessage(content=str(state.get("user_prompt", ""))),
            ]
        ):
            text = _message_to_text(token)
            if not text:
                continue
            answer += text
            writer({"type": "answer_delta", "data": {"text": text}})
    except Exception as exc:
        writer(
            {
                "type": "error",
                "data": {"message": f"本地模型生成失败：{exc}"},
            }
        )
        return {"error": str(exc), "answer": answer}

    return {
        "answer": answer,
        "messages": [AIMessage(content=answer)],
    }


def make_npc_stream_event(event_type: str, data: dict) -> str:
    return json.dumps({"type": event_type, "data": data}, ensure_ascii=False) + "\n"


def _chunk_to_dict(chunk: RetrievedChunk) -> dict[str, Any]:
    return {
        "source_file": chunk.source_file,
        "section_title": chunk.section_title,
        "content": chunk.content,
        "npc_id": chunk.npc_id,
        "unlock_level": chunk.unlock_level,
        "topics": chunk.topics,
        "spoiler_level": chunk.spoiler_level,
        "score": chunk.score,
    }


def _dict_to_chunk(data: dict[str, Any]) -> RetrievedChunk:
    return RetrievedChunk(
        source_file=str(data.get("source_file", "")),
        section_title=str(data.get("section_title", "")),
        content=str(data.get("content", "")),
        npc_id=str(data.get("npc_id", "")),
        unlock_level=int(data.get("unlock_level", 0)),
        topics=str(data.get("topics", "")),
        spoiler_level=str(data.get("spoiler_level", "")),
        score=float(data.get("score", 0.0)),
    )


def _chunk_to_source(chunk: RetrievedChunk) -> dict:
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


def _source_dict_to_search_item(source: dict[str, Any]) -> SearchItem:
    return SearchItem(
        title=str(source.get("section_title", "")),
        url=str(source.get("source_file", "")),
        snippet=str(source.get("snippet", "")),
    )


def _memory_item_to_dict(item: RetrievedMemoryItem) -> dict[str, Any]:
    return {
        "memory_type": item.memory_type,
        "content": item.content,
        "keywords": item.keywords,
        "importance": item.importance,
        "score": item.score,
    }


def _dict_to_memory_item(data: dict[str, Any]) -> RetrievedMemoryItem:
    return RetrievedMemoryItem(
        memory_type=str(data.get("memory_type", "")),
        content=str(data.get("content", "")),
        keywords=str(data.get("keywords", "")),
        importance=int(data.get("importance", 3)),
        score=float(data.get("score", 0.0)),
    )


def _messages_to_dialogue_history(messages: list[AnyMessage]) -> list[tuple[str, str]]:
    history: list[tuple[str, str]] = []
    pending_question: str | None = None

    for message in messages:
        message_type = getattr(message, "type", "")
        content = _message_to_text(message)
        if not content:
            continue

        if message_type == "human":
            pending_question = content
        elif message_type == "ai" and pending_question:
            history.append((pending_question, content))
            pending_question = None

    return history[-6:]


def _message_to_text(message: Any) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content

    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                parts.append(str(item.get("text") or item.get("content") or ""))
            else:
                parts.append(str(item))
        return "".join(parts)

    return str(content) if content else ""


def _normalize_graph_stream_part(chunk: Any) -> tuple[Any, Any]:
    if isinstance(chunk, tuple) and len(chunk) == 2:
        return chunk[0], chunk[1]
    return None, chunk
