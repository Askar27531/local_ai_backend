from __future__ import annotations

import json
import logging
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.dialogue import orchestrator
from npc_app.dialogue.intent import build_turn_policy, classify_npc_intent
from npc_app.dialogue.orchestrator import (
    NpcRagStreamState,
    RetrievalResult,
    _filter_npc_context,
    _guard_answer,
    run_npc_rag_chat_stream,
)
from npc_app.services.milvus_retriever_service import RetrievedChunk
from npc_app.trace import TRACE_LOGGER_NAME, NpcTurnTrace, emit_trace


def _request(question: str = "这把钥匙是什么？") -> UnityNpcTurnRequest:
    return UnityNpcTurnRequest.model_validate(
        {
            "question": question,
            "npc_id": "Orin",
            "relationship": {"trust": 1, "favorability": 50, "annoyance": 0},
            "story": {"unlock_level": 1},
            "intent_hint": "ask_item",
        }
    )


def test_trace_disabled_emits_nothing(monkeypatch, caplog) -> None:
    monkeypatch.setenv("NPC_TRACE_ENABLED", "false")
    with caplog.at_level(logging.INFO, logger=TRACE_LOGGER_NAME):
        emit_trace(NpcTurnTrace.create("thread-1", "Orin"))

    assert caplog.records == []


def test_trace_json_contains_no_credentials_or_content(monkeypatch, caplog) -> None:
    monkeypatch.setenv("NPC_TRACE_ENABLED", "true")
    trace = NpcTurnTrace.create("thread-1", "Orin")
    trace.intent = "ask_item"
    trace.prompt_chars = 1234

    with caplog.at_level(logging.INFO, logger=TRACE_LOGGER_NAME):
        emit_trace(trace)

    payload = json.loads(caplog.records[-1].message)
    serialized = json.dumps(payload)
    assert payload["intent"] == "ask_item"
    assert payload["prompt_chars"] == 1234
    assert "authorization" not in serialized.lower()
    assert "bearer" not in serialized.lower()
    assert "prompt_content" not in payload
    assert "answer" not in payload


def test_successful_turn_records_plan_context_and_timings(monkeypatch) -> None:
    monkeypatch.setattr("npc_app.dialogue.orchestrator._retrieve_context", lambda *_args: RetrievalResult())
    monkeypatch.setattr(
        orchestrator, "llm", SimpleNamespace(stream=lambda *_args: [AIMessage(content="我见过这把钥匙。")])
    )
    trace = NpcTurnTrace.create("thread-1", "Orin")
    state = NpcRagStreamState()

    events = list(run_npc_rag_chat_stream(_request(), stream_state=state, turn_trace=trace))

    assert trace.intent == "ask_item"
    assert trace.plan_source == "rules"
    assert trace.arc_stage == "guarded"
    assert trace.dialogue_strategy == "answer_directly"
    assert trace.prompt_chars > 0
    assert trace.planning_ms >= 0
    assert trace.retrieval_ms >= 0
    assert trace.generation_ms >= 0
    assert trace.guard_result == "passed"
    assert all("context_manifest" not in event for event in events)
    assert all("selected_memory_keys" not in event for event in events)


def test_retrieval_failure_marks_error_stage(monkeypatch) -> None:
    monkeypatch.setattr(
        "npc_app.dialogue.orchestrator._retrieve_context",
        lambda *_args: RetrievalResult(error="milvus unavailable"),
    )
    trace = NpcTurnTrace.create("thread-1", "Orin")

    list(run_npc_rag_chat_stream(_request(), turn_trace=trace))

    assert trace.error_stage == "retrieval"
    assert trace.guard_result == "not_run"


def test_generation_failure_marks_error_stage(monkeypatch) -> None:
    monkeypatch.setattr("npc_app.dialogue.orchestrator._retrieve_context", lambda *_args: RetrievalResult())

    def fail_generation(*_args):
        raise RuntimeError("model unavailable")

    monkeypatch.setattr(orchestrator, "llm", SimpleNamespace(stream=fail_generation))
    trace = NpcTurnTrace.create("thread-1", "Orin")

    list(run_npc_rag_chat_stream(_request(), turn_trace=trace))

    assert trace.error_stage == "generation"
    assert trace.generation_ms >= 0


def test_answer_guard_replacement_is_traced(monkeypatch) -> None:
    monkeypatch.setattr("npc_app.dialogue.orchestrator._retrieve_context", lambda *_args: RetrievalResult())
    monkeypatch.setattr(
        orchestrator, "llm", SimpleNamespace(stream=lambda *_args: [AIMessage(content="Subject 07 是实验体。")])
    )
    trace = NpcTurnTrace.create("thread-1", "Orin")
    state = NpcRagStreamState()

    list(run_npc_rag_chat_stream(_request(), stream_state=state, turn_trace=trace))

    assert trace.guard_result == "replaced"
    assert "Subject 07" not in state.answer


def test_early_lira_context_keeps_clinical_evidence_only() -> None:
    req = UnityNpcTurnRequest.model_validate(
        {"question": "我头痛。", "npc_id": "Lira", "story": {"unlock_level": 1}}
    )
    chunks = [
        RetrievedChunk("medical.md", "头痛记录", "记录症状和接触时间。", "Lira", 1, "病历", "low", 0.9),
        RetrievedChunk("quest.md", "灯塔入口", "前往灯塔打开旧门。", "Lira", 1, "任务", "low", 0.8),
    ]

    selected = _filter_npc_context(req, chunks)

    assert [chunk.source_file for chunk in selected] == ["medical.md"]


def test_lira_guard_replaces_unsupported_location_advice() -> None:
    req = UnityNpcTurnRequest.model_validate(
        {"question": "我从海边回来后头痛。", "npc_id": "Lira", "story": {"unlock_level": 1}}
    )
    policy = build_turn_policy(classify_npc_intent(req), req)

    answer, result = _guard_answer(req, policy, "先去井边看看蓝光，再到灯塔找原因。")

    assert result == "replaced_lira_scope"
    assert "井边" not in answer
    assert "灯塔" not in answer
    assert "发作时间" in answer


def test_lira_guard_keeps_location_advice_when_player_supplies_location_evidence() -> None:
    req = UnityNpcTurnRequest.model_validate(
        {
            "question": "我从井边回来后头痛。",
            "npc_id": "Lira",
            "player": {"mentioned_clues": ["well_echo"]},
            "story": {"unlock_level": 1},
        }
    )
    policy = build_turn_policy(classify_npc_intent(req), req)

    answer, result = _guard_answer(req, policy, "先别去井边，记录头痛是否减轻。")

    assert result == "passed"
    assert "井边" in answer
