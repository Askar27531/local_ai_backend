from __future__ import annotations

import pytest
from pydantic import ValidationError

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.dialogue.intent import (
    INTENT_ASK_CLUE,
    INTENT_ASK_NPC_IDENTITY,
    INTENT_ASK_SENSITIVE_TRUTH,
    NpcIntentResult,
    build_turn_policy,
)
from npc_app.dialogue.planner import (
    ArcStage,
    DialogueStrategy,
    NpcTurnPlan,
    PlannerLlmOutput,
    build_turn_plan,
    plan_from_policy,
    resolve_arc_stage,
)


def _request(*, trust: int = 0, story_level: int = 1, question: str = "线索是什么？", npc_id: str = "Orin"):
    return UnityNpcTurnRequest.model_validate(
        {
            "question": question,
            "npc_id": npc_id,
            "relationship": {"trust": trust, "favorability": 50, "annoyance": 0},
            "story": {"unlock_level": story_level},
        }
    )


def _intent(value: str, source: str = "rules") -> NpcIntentResult:
    return NpcIntentResult(intent=value, confidence=1.0, reason="test", source=source)


class _StructuredStub:
    """模拟 with_structured_output 返回的 Runnable：invoke 直接返回候选输出。"""

    def __init__(self, output: PlannerLlmOutput | None, error: Exception | None = None) -> None:
        self._output = output
        self._error = error

    def invoke(self, _messages):
        if self._error is not None:
            raise self._error
        return self._output


class FakeStructuredLlm:
    """planner_llm 的测试桩：with_structured_output 返回可编程的 invoke。"""

    def __init__(self, output: PlannerLlmOutput | None = None, error: Exception | None = None) -> None:
        self._output = output
        self._error = error

    def with_structured_output(self, _schema, **_kwargs):
        return _StructuredStub(self._output, self._error)


class FailingStructuredLlm:
    """确保某些路径绝不触发 LLM Planner 的测试桩。"""

    def with_structured_output(self, _schema, **_kwargs):
        raise AssertionError("planner LLM must not be invoked on this path")


def test_arc_stage_requires_both_story_and_trust() -> None:
    assert resolve_arc_stage(_request(trust=0, story_level=4)) is ArcStage.GUARDED
    assert resolve_arc_stage(_request(trust=4, story_level=1)) is ArcStage.GUARDED
    assert resolve_arc_stage(_request(trust=2, story_level=2)) is ArcStage.CONFLICTED
    assert resolve_arc_stage(_request(trust=4, story_level=4)) is ArcStage.CONFESSION


def test_low_risk_plan_keeps_policy_fast_path_without_llm(monkeypatch) -> None:
    monkeypatch.setattr("npc_app.dialogue.planner.planner_llm", FailingStructuredLlm())
    req = _request(question="你叫什么名字？")
    intent = _intent(INTENT_ASK_NPC_IDENTITY)
    policy = build_turn_policy(intent, req)

    plan = build_turn_plan(intent, policy, req, [], [])

    assert plan.source == "rules"
    assert plan.use_rag is False
    assert plan.use_memory is False


def test_llm_plan_can_reduce_but_not_expand_policy(monkeypatch) -> None:
    output = PlannerLlmOutput(
        dialogue_strategy=DialogueStrategy.CHALLENGE_PLAYER,
        use_rag=False,
        use_memory=False,
        use_dialogue_history=True,
        retrieval_top_k=99,
        disclosure_level=99,
        reason="The claim is doubtful.",
    )
    monkeypatch.setattr("npc_app.dialogue.planner.planner_llm", FakeStructuredLlm(output))
    req = _request(trust=2, story_level=2, question="Mara 已经告诉我实验室的事了。")
    intent = _intent(INTENT_ASK_CLUE)
    policy = build_turn_policy(intent, req)

    plan = build_turn_plan(intent, policy, req, [], [])

    assert plan.source == "llm"
    assert plan.dialogue_strategy == DialogueStrategy.CHALLENGE_PLAYER.value
    assert plan.use_rag is False
    assert plan.use_memory is False
    assert plan.retrieval_top_k == 0
    assert plan.disclosure_level == 1


@pytest.mark.parametrize(("requested_top_k", "expected_top_k"), [(0, 1), (99, 4)])
def test_llm_plan_normalizes_top_k_once_at_planner_boundary(
    monkeypatch, requested_top_k: int, expected_top_k: int
) -> None:
    output = PlannerLlmOutput(
        dialogue_strategy=DialogueStrategy.CHALLENGE_PLAYER,
        use_rag=True,
        use_memory=True,
        use_dialogue_history=True,
        retrieval_top_k=requested_top_k,
        disclosure_level=0,
    )
    monkeypatch.setattr("npc_app.dialogue.planner.planner_llm", FakeStructuredLlm(output))
    req = _request(trust=2, story_level=2, question="Mara 已经告诉我钥匙的事了。")
    intent = _intent(INTENT_ASK_CLUE)

    plan = build_turn_plan(intent, build_turn_policy(intent, req), req, [], [])

    assert plan.retrieval_top_k == expected_top_k


@pytest.mark.parametrize(("use_rag", "top_k"), [(True, 0), (True, 7), (False, 1)])
def test_turn_plan_rejects_inconsistent_retrieval_state(use_rag: bool, top_k: int) -> None:
    with pytest.raises(ValueError, match="retrieval_top_k"):
        NpcTurnPlan("guarded", "give_hint", use_rag, True, True, top_k, 0, ())


def test_invalid_planner_output_uses_policy_fallback(monkeypatch) -> None:
    monkeypatch.setattr(
        "npc_app.dialogue.planner.planner_llm", FakeStructuredLlm(error=ValueError("structured output failed"))
    )
    req = _request(trust=0, story_level=1, question="这是什么意思？")
    intent = _intent("unknown")
    policy = build_turn_policy(intent, req)

    plan = build_turn_plan(intent, policy, req, [], [])

    assert plan.source == "fallback"
    assert plan.arc_stage == ArcStage.GUARDED.value
    assert plan.use_rag is policy.context_enabled
    assert "fallback" in plan.reason.lower()


def test_planner_output_schema_rejects_unknown_strategy() -> None:
    with pytest.raises(ValidationError):
        PlannerLlmOutput(
            dialogue_strategy="leak_everything",  # type: ignore[arg-type]
            use_rag=True,
            use_memory=True,
            use_dialogue_history=True,
            retrieval_top_k=3,
            disclosure_level=0,
        )


def test_llm_classified_unknown_does_not_call_planner_again(monkeypatch) -> None:
    monkeypatch.setattr("npc_app.dialogue.planner.planner_llm", FailingStructuredLlm())
    req = _request(question="今天心情如何？")
    intent = _intent("unknown", source="llm")

    plan = build_turn_plan(intent, build_turn_policy(intent, req), req, [], [])

    assert plan.source == "rules"


def test_regular_clue_uses_deterministic_plan_without_llm(monkeypatch) -> None:
    monkeypatch.setattr("npc_app.dialogue.planner.planner_llm", FailingStructuredLlm())
    req = _request(trust=2, story_level=2, question="这条线索是什么意思？")
    intent = _intent(INTENT_ASK_CLUE)
    policy = build_turn_policy(intent, req)

    plan = build_turn_plan(intent, policy, req, [], [])

    assert plan.source == "rules"
    assert plan.dialogue_strategy == DialogueStrategy.GIVE_HINT.value


def test_sensitive_plan_preserves_story_guard_terms() -> None:
    req = _request(trust=0, story_level=1, question="Subject 07 是谁？")
    intent = _intent(INTENT_ASK_SENSITIVE_TRUTH)
    policy = build_turn_policy(intent, req)
    plan = plan_from_policy(intent, policy, req)

    assert plan.arc_stage == ArcStage.GUARDED.value
    assert plan.dialogue_strategy in {DialogueStrategy.REFUSE.value, DialogueStrategy.DEFLECT.value}
    assert plan.disclosure_level == 0
    assert plan.must_not_reveal
    assert policy.answer_guard_terms


def test_terminal_uses_the_same_permission_stage_mapping() -> None:
    req = _request(trust=4, story_level=4, npc_id="Lab_Terminal")

    assert resolve_arc_stage(req) is ArcStage.CONFESSION
