from __future__ import annotations

import json
from types import SimpleNamespace

from langchain_core.messages import AIMessage

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.dialogue import orchestrator
from npc_app.dialogue.characters import basic_response
from npc_app.dialogue.intent import (
    INTENT_ASK_NPC_IDENTITY,
    INTENT_ASK_SENSITIVE_TRUTH,
    build_turn_policy,
    classify_npc_intent,
    classify_npc_intent_with_rules,
)
from npc_app.dialogue.orchestrator import NpcRagStreamState, RetrievalResult, run_npc_rag_chat_stream


def _request(question: str, npc_id: str = "Karo", **updates) -> UnityNpcTurnRequest:
    payload = {
        "question": question,
        "npc_id": npc_id,
        "player": {},
        "relationship": {"trust": 0, "favorability": 50, "annoyance": 0},
        "story": {"unlock_level": 1},
    }
    payload.update(updates)
    return UnityNpcTurnRequest(**payload)


def test_rules_distinguish_npc_name_from_sensitive_player_identity() -> None:
    name_question = "\u4f60\u597d\u8bf7\u95ee\u4f60\u53eb\u4ec0\u4e48\u540d\u5b57"
    name_intent = classify_npc_intent_with_rules(_request(name_question))
    sensitive_intent = classify_npc_intent_with_rules(_request("Subject 07 \u662f\u6211\u7684\u540d\u5b57\u5417"))

    assert name_intent.intent == INTENT_ASK_NPC_IDENTITY
    assert sensitive_intent.intent == INTENT_ASK_SENSITIVE_TRUTH


def test_broad_story_words_need_sensitive_phrases() -> None:
    npc_identity = classify_npc_intent_with_rules(_request("你的身份是什么？"))
    ordinary_basement = classify_npc_intent_with_rules(_request("地下室怎么走？"))
    story_facility = classify_npc_intent_with_rules(_request("地下设施里有什么？"))

    assert npc_identity.intent == INTENT_ASK_NPC_IDENTITY
    assert ordinary_basement.intent != INTENT_ASK_SENSITIVE_TRUTH
    assert story_facility.intent == INTENT_ASK_SENSITIVE_TRUTH


def test_intent_hint_builds_low_risk_policy_without_llm() -> None:
    req = _request("anything", intent_hint=INTENT_ASK_NPC_IDENTITY)
    intent = classify_npc_intent(req)
    policy = build_turn_policy(intent, req)

    assert intent.intent == INTENT_ASK_NPC_IDENTITY
    assert intent.source == "hint"
    assert policy.context_enabled is False


def test_llm_intent_classifier_is_used_only_for_unknown_rule_result(monkeypatch) -> None:
    class FakeLlm:
        def invoke(self, _messages):
            return AIMessage(
                content=json.dumps(
                    {
                        "intent": "ask_item",
                        "risk_level": "medium",
                        "confidence": 0.91,
                        "reason": "player asks about an object",
                    }
                )
            )

    monkeypatch.setattr("npc_app.dialogue.intent.planner_llm", FakeLlm())

    intent = classify_npc_intent(_request("今天心情如何？"))

    assert intent.intent == "ask_item"
    assert intent.source == "llm"
    assert intent.confidence == 0.91


def test_sensitive_terms_override_low_risk_llm_result(monkeypatch) -> None:
    class FakeLlm:
        def invoke(self, _messages):
            return AIMessage(content=json.dumps({"intent": INTENT_ASK_NPC_IDENTITY, "confidence": 0.99}))

    monkeypatch.setattr("npc_app.dialogue.intent.planner_llm", FakeLlm())

    intent = classify_npc_intent(_request("Subject 07 \u662f\u6211\u7684\u540d\u5b57\u5417"))

    assert intent.intent == INTENT_ASK_SENSITIVE_TRUTH
    assert intent.source == "rules_override"


def test_basic_response_uses_intent_not_question_guessing() -> None:
    answer = basic_response("Karo", INTENT_ASK_NPC_IDENTITY)

    assert answer is not None
    assert "Karo" in answer
    assert "\u94c1\u724c" not in answer
    assert "\u7f16\u53f7" not in answer


def test_low_risk_stream_skips_retrieval_with_hint() -> None:
    stream_state = NpcRagStreamState()
    events = list(
        run_npc_rag_chat_stream(
            _request(
                "\u4f60\u597d\u8bf7\u95ee\u4f60\u53eb\u4ec0\u4e48\u540d\u5b57",
                intent_hint=INTENT_ASK_NPC_IDENTITY,
            ),
            stream_state=stream_state,
        )
    )

    assert stream_state.answer
    assert stream_state.used_rag is False
    assert any('"retrieved_count": 0' in event for event in events)
    assert "\u94c1\u724c" not in stream_state.answer
    assert "\u7f16\u53f7" not in stream_state.answer


def test_complex_stream_records_context_manifest(monkeypatch) -> None:
    monkeypatch.setattr(
        orchestrator, "llm", SimpleNamespace(stream=lambda *_args: [AIMessage(content="这把钥匙我见过。")])
    )
    monkeypatch.setattr(
        "npc_app.dialogue.orchestrator._retrieve_context",
        lambda *_args: RetrievalResult(),
    )
    stream_state = NpcRagStreamState()

    list(
        run_npc_rag_chat_stream(
            _request("这把钥匙是什么？", intent_hint="ask_item"),
            stream_state=stream_state,
            dialogue_history=[("你见过吗？", "也许。")],
        )
    )

    assert stream_state.turn_plan is not None
    assert stream_state.context_manifest is not None
    assert stream_state.context_manifest.strategy == stream_state.turn_plan.dialogue_strategy
