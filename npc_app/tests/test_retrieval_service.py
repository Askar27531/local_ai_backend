from __future__ import annotations

import pytest

from npc_app.services.milvus_retriever_service import (
    RetrievedChunk,
    _hit_to_chunk,
    apply_source_priority,
    rerank_game_chunks,
    retrieve_game_chunks,
    should_use_cross_encoder,
)


def _chunk(source: str, title: str) -> RetrievedChunk:
    return RetrievedChunk(
        source_file=source,
        section_title=title,
        content="content",
        npc_id="Karo",
        unlock_level=1,
        topics="test",
        spoiler_level="low",
        score=0.9,
    )


def test_cross_encoder_reranker_reorders_and_limits_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    class FakeCrossEncoder:
        def predict(self, pairs, show_progress_bar: bool):
            assert show_progress_bar is False
            return [0.1 if "Noise" in document else 0.9 for _question, document in pairs]

    monkeypatch.setattr(
        "npc_app.services.milvus_retriever_service.get_reranker_model",
        lambda: FakeCrossEncoder(),
    )
    reranked = rerank_game_chunks(
        "question", [_chunk("noise.md", "Noise"), _chunk("expected.md", "Expected section")], top_k=1
    )
    assert len(reranked) == 1
    assert reranked[0].source_file == "expected.md"
    assert 0.0 < reranked[0].score < 1.0


def test_source_priority_breaks_close_vector_score_tie() -> None:
    general = _chunk("19_dialogue.md", "General")
    general.score = 0.8
    canonical = _chunk("08_items.md", "Canonical")
    canonical.score = 0.799
    assert apply_source_priority([general, canonical], ("08_",), top_k=2)[0].source_file == "08_items.md"


def test_conditional_rerank_uses_intent_and_confidence() -> None:
    confident = _chunk("06_locations.md", "Location")
    confident.score = 0.8
    uncertain = _chunk("08_items.md", "Item")
    uncertain.score = 0.4
    assert should_use_cross_encoder("ask_location", [confident]) is False
    assert should_use_cross_encoder("ask_location", [uncertain]) is True
    assert should_use_cross_encoder("ask_item", [uncertain]) is True
    assert should_use_cross_encoder("ask_sensitive_truth", [confident]) is True


def test_world_retrieval_uses_bound_filter_parameters(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}

    class FakeVector(list):
        def tolist(self):
            return list(self)

    class FakeEmbedding:
        def encode(self, _texts, normalize_embeddings: bool):
            assert normalize_embeddings is True
            return [FakeVector([0.1, 0.2])]

    class FakeClient:
        def search(self, **kwargs):
            captured.update(kwargs)
            return [[]]

    monkeypatch.setattr("npc_app.services.milvus_retriever_service.get_embedding_model", lambda: FakeEmbedding())
    monkeypatch.setattr("npc_app.services.milvus_retriever_service.get_milvus_client", lambda: FakeClient())
    assert retrieve_game_chunks("question", "Orin", 2) == []
    assert captured["filter"] == "npc_id == {npc_id} and unlock_level <= {unlock_level}"
    assert captured["filter_params"] == {"npc_id": "Orin", "unlock_level": 2}


@pytest.mark.parametrize(("top_k", "candidate_k"), [(0, None), (5, 4)])
def test_world_retrieval_rejects_invalid_candidate_counts(top_k: int, candidate_k: int | None) -> None:
    with pytest.raises(ValueError, match="candidate_k >= top_k >= 1"):
        retrieve_game_chunks("question", "Orin", 2, top_k=top_k, candidate_k=candidate_k)


def test_world_hit_requires_all_schema_fields() -> None:
    with pytest.raises(KeyError, match="topics"):
        _hit_to_chunk(
            {
                "distance": 0.9,
                "entity": {
                    "source_file": "08_items.md",
                    "section_title": "Key",
                    "content": "content",
                    "npc_id": "Orin",
                    "unlock_level": 1,
                    "spoiler_level": "low",
                },
            }
        )
