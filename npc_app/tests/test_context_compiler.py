from __future__ import annotations

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.dialogue.context import prepare_prompt_context
from npc_app.dialogue.planner import NpcTurnPlan
from npc_app.services.memory_service import RetrievedMemoryItem
from npc_app.services.milvus_retriever_service import RetrievedChunk


def _request(question: str = "你刚才说过钥匙的事吗？", story_level: int = 2) -> UnityNpcTurnRequest:
    return UnityNpcTurnRequest.model_validate(
        {
            "question": question,
            "npc_id": "Orin",
            "relationship": {"trust": 2, "favorability": 50, "annoyance": 0},
            "story": {"unlock_level": story_level},
        }
    )


def _plan(strategy: str, **updates) -> NpcTurnPlan:
    payload = {
        "arc_stage": "conflicted",
        "dialogue_strategy": strategy,
        "use_rag": True,
        "use_memory": True,
        "use_dialogue_history": True,
        "retrieval_top_k": 5,
        "disclosure_level": 1,
        "must_not_reveal": (),
    }
    payload.update(updates)
    return NpcTurnPlan(**payload)


def _memory(memory_type: str, content: str, *, importance: int = 4, score: float = 1.0) -> RetrievedMemoryItem:
    return RetrievedMemoryItem(memory_type, content, "钥匙", importance, score)


def _chunk(name: str, *, unlock_level: int = 1, content: str = "钥匙与灯塔下层有关。") -> RetrievedChunk:
    return RetrievedChunk(name, "钥匙", content, "Orin", unlock_level, "key", "low", 0.9)


def test_plan_keeps_enabled_context_categories() -> None:
    prepared = prepare_prompt_context(
        req=_request(),
        summary="玩家曾来过灯塔。",
        memory_items=[_memory("player_fact", "玩家展示过旧钥匙。")],
        dialogue_history=[("这是什么？", "一把旧钥匙。")],
        chunks=[_chunk("08_items.md")],
        turn_plan=_plan("answer_directly"),
    )

    assert prepared.summary
    assert prepared.memory_items
    assert prepared.dialogue_history
    assert prepared.chunks
    assert prepared.manifest.strategy == "answer_directly"


def test_refuse_plan_compiles_empty_context() -> None:
    prepared = prepare_prompt_context(
        req=_request(),
        summary="summary",
        memory_items=[_memory("player_fact", "玩家展示过旧钥匙。")],
        dialogue_history=[("问题", "回答")],
        chunks=[_chunk("08_items.md")],
        turn_plan=_plan(
            "refuse",
            use_rag=False,
            use_memory=False,
            use_dialogue_history=False,
            retrieval_top_k=0,
        ),
    )

    assert prepared.summary == ""
    assert prepared.memory_items == []
    assert prepared.dialogue_history == []
    assert prepared.chunks == []
    assert all(value == 0 for value in prepared.manifest.used_chars.values())
    assert {item["reason"] for item in prepared.manifest.excluded} == {"policy_disabled"}


def test_challenge_player_prioritizes_unverified_claim() -> None:
    prepared = prepare_prompt_context(
        req=_request("你说 Mara 已经告诉我钥匙的事？"),
        summary="",
        memory_items=[
            _memory("player_fact", "玩家展示过一把钥匙。", score=4.0),
            _memory("player_claim", "玩家声称 Mara 讲过钥匙。", score=1.0),
        ],
        dialogue_history=[],
        chunks=[],
        turn_plan=_plan("challenge_player"),
    )

    assert prepared.memory_items[0].memory_type.startswith("unverified_claim:player_claim")
    assert "never verified world truth" in prepared.strategy


def test_partial_truth_prioritizes_already_disclosed_memory() -> None:
    prepared = prepare_prompt_context(
        req=_request(),
        summary="",
        memory_items=[
            _memory("player_fact", "玩家展示过旧钥匙。", score=4.0),
            _memory("npc_disclosed", "Orin 已承认钥匙能开外门。", score=1.0),
        ],
        dialogue_history=[],
        chunks=[],
        turn_plan=_plan("tell_partial_truth"),
    )

    assert prepared.memory_items[0].memory_type.startswith("already_said:npc_disclosed")


def test_story_locked_chunk_is_excluded_and_explained() -> None:
    prepared = prepare_prompt_context(
        req=_request(story_level=1),
        summary="",
        memory_items=[],
        dialogue_history=[],
        chunks=[_chunk("10_truth.md", unlock_level=4)],
        turn_plan=_plan("give_hint"),
    )

    assert prepared.chunks == []
    assert any(item["reason"] == "story_locked" for item in prepared.manifest.excluded)


def test_manifest_reports_budget_exclusions_and_never_exceeds_budget() -> None:
    chunks = [_chunk(f"chunk_{index}.md", content="线索" * 240) for index in range(4)]
    prepared = prepare_prompt_context(
        req=_request(),
        summary="摘要" * 500,
        memory_items=[],
        dialogue_history=[("问题" * 100, "回答" * 150) for _ in range(4)],
        chunks=chunks,
        turn_plan=_plan("answer_directly"),
    )

    manifest = prepared.manifest
    assert manifest.used_chars["summary"] <= manifest.budget.summary_chars
    assert manifest.used_chars["dialogue"] <= manifest.budget.dialogue_chars
    assert manifest.used_chars["world"] <= manifest.budget.world_chars
    assert any(item["reason"] == "budget_exceeded" for item in manifest.excluded)
    assert len(manifest.selected_chunk_ids) == len(set(manifest.selected_chunk_ids))
