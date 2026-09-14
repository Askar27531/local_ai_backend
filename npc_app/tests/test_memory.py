from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from npc_app.contracts import UnityNpcTurnRequest
from npc_app.database import NpcChatThread
from npc_app.services import memory_milvus_service
from npc_app.services.memory_service import RetrievedMemoryItem, _normalize_memory_type, record_confirmed_turn_memories


class _SearchClient:
    def __init__(self) -> None:
        self.search_calls = 0
        self.search_kwargs = {}

    def search(self, **kwargs):
        self.search_calls += 1
        self.search_kwargs = kwargs
        return [[]]


def _request(items: list[str]) -> UnityNpcTurnRequest:
    return UnityNpcTurnRequest.model_validate(
        {
            "question": "看看这个。",
            "npc_id": "Orin",
            "player": {"presented_items": items},
        }
    )


def test_dialogue_extraction_cannot_promote_claim_to_fact() -> None:
    assert _normalize_memory_type("player_fact", allow_player_fact=False) == "player_claim"
    assert _normalize_memory_type("invented_type", allow_player_fact=False) == "player_claim"
    assert _normalize_memory_type("npc_disclosed", allow_player_fact=False) == "npc_disclosed"


def test_confirmed_unity_path_can_write_player_fact() -> None:
    assert _normalize_memory_type("player_fact", allow_player_fact=True) == "player_fact"


def test_presented_items_are_written_once_as_confirmed_facts(monkeypatch) -> None:
    db = MagicMock()
    thread = NpcChatThread(id="thread-1", user_id=7, npc_id="Orin")
    captured: dict = {}

    def fake_upsert(_db, _thread, items, *, allow_player_fact=False):
        captured["items"] = items
        captured["allow_player_fact"] = allow_player_fact

    monkeypatch.setattr("npc_app.services.memory_service._upsert_memory_items", fake_upsert)

    record_confirmed_turn_memories(db, thread, _request(["old_key", "photo_fragment"]))

    assert [item["content"] for item in captured["items"]] == [
        "玩家曾向当前 NPC 展示物品：old_key",
        "玩家曾向当前 NPC 展示物品：photo_fragment",
    ]
    assert captured["allow_player_fact"] is True
    db.commit.assert_called_once_with()


def test_no_presented_item_does_not_open_a_transaction(monkeypatch) -> None:
    db = MagicMock()
    thread = NpcChatThread(id="thread-1", user_id=7, npc_id="Orin")
    upsert = MagicMock()
    monkeypatch.setattr("npc_app.services.memory_service._upsert_memory_items", upsert)

    record_confirmed_turn_memories(db, thread, _request([]))

    upsert.assert_not_called()
    db.commit.assert_not_called()


def test_runtime_memory_retrieval_does_not_manage_collection(monkeypatch) -> None:
    client = _SearchClient()
    monkeypatch.setattr(memory_milvus_service, "get_memory_milvus_client", lambda: client)
    monkeypatch.setattr(memory_milvus_service, "_embed_text", lambda _text: [0.1, 0.2])
    assert memory_milvus_service.retrieve_memory_items_from_milvus(1, "thread", "Orin", "question") == []
    assert client.search_calls == 1
    assert client.search_kwargs["filter"] == (
        "user_id == {user_id} and thread_id == {thread_id} and npc_id == {npc_id}"
    )
    assert client.search_kwargs["filter_params"] == {"user_id": 1, "thread_id": "thread", "npc_id": "Orin"}


def test_memory_storage_rejects_oversized_or_invalid_values() -> None:
    with pytest.raises(ValueError, match="content"):
        memory_milvus_service._validate_memory_record("player_fact", "x" * 1025, "key", 4)
    with pytest.raises(ValueError, match="keywords"):
        memory_milvus_service._validate_memory_record("player_fact", "content", "x" * 513, 4)
    with pytest.raises(ValueError, match="importance"):
        memory_milvus_service._validate_memory_record("player_fact", "content", "key", 6)


@pytest.mark.parametrize("importance", [0, 6])
def test_retrieved_memory_rejects_invalid_importance(importance: int) -> None:
    with pytest.raises(ValueError, match="importance"):
        RetrievedMemoryItem("player_fact", "content", importance=importance)


def test_memory_hit_requires_all_schema_fields() -> None:
    with pytest.raises(KeyError, match="keywords"):
        memory_milvus_service._hit_to_memory_item(
            {
                "distance": 0.8,
                "entity": {"memory_type": "player_fact", "content": "content", "importance": 3},
            }
        )


@pytest.mark.parametrize("dimension", [None, 0, -1])
def test_embedding_dimension_must_be_positive(monkeypatch, dimension) -> None:
    model = MagicMock()
    model.get_sentence_embedding_dimension.return_value = dimension
    monkeypatch.setattr(memory_milvus_service, "get_embedding_model", lambda: model)

    with pytest.raises(RuntimeError, match="invalid dimension"):
        memory_milvus_service._embedding_dim()


def test_memory_upsert_uses_bound_delete_parameter(monkeypatch) -> None:
    captured: dict = {}

    class FakeClient:
        def delete(self, **kwargs):
            captured.update(kwargs)

        def insert(self, **_kwargs):
            return None

        def flush(self, **_kwargs):
            return None

    monkeypatch.setattr(memory_milvus_service, "get_memory_milvus_client", lambda: FakeClient())
    monkeypatch.setattr(memory_milvus_service, "_embed_text", lambda _text: [0.1, 0.2])
    assert memory_milvus_service.upsert_memory_item_to_milvus(
        9, 1, "00000000-0000-0000-0000-000000000000", "Orin", "player_fact", "content", "key", 4
    )
    assert captured["filter"] == "memory_item_id == {memory_item_id}"
    assert captured["filter_params"] == {"memory_item_id": 9}


def test_memory_collection_lifecycle_is_explicit(monkeypatch) -> None:
    class MissingClient:
        def has_collection(self, _name):
            return False

    monkeypatch.setattr(memory_milvus_service, "get_memory_milvus_client", lambda: MissingClient())
    with pytest.raises(RuntimeError, match=r"python -m scripts\.init_memory_milvus"):
        memory_milvus_service.validate_memory_collection()

    class ExistingClient:
        def has_collection(self, _name):
            return True

    monkeypatch.setattr(memory_milvus_service, "get_memory_milvus_client", lambda: ExistingClient())
    with pytest.raises(RuntimeError, match="already exists"):
        memory_milvus_service.create_memory_collection()
