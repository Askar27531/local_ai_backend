import copy
import json
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from npc_app.contracts import LoginRequest, NpcId, RegisterRequest, UnityNpcTurnRequest
from npc_app.database import Base, NpcChatRecord, NpcChatThread, NpcMemoryItem, NpcThreadMemory, NpcUser
from npc_app.dialogue.characters import character_dialogue
from npc_app.dialogue.intent import Intent
from npc_app.dialogue.planner import DialogueStrategy
from npc_app.main import app
from npc_app.services import npc_prompt_service
from npc_app.services.chat_service import get_or_create_chat_thread
from npc_app.utils import dialogue_config


def test_dialogue_config_covers_runtime_characters_intents_and_strategies() -> None:
    config = dialogue_config()
    character_ids = {npc_id for npc_id in config["characters"] if not npc_id.startswith("_")}

    assert character_ids == {npc_id.value for npc_id in NpcId}
    policy_names = {name for name in config["intent_policies"] if not name.startswith("_")}
    assert policy_names == {intent.value for intent in Intent}
    assert set(config["context"]["strategy_budgets"]) == {strategy.value for strategy in DialogueStrategy}
    assert len(config["story_safety"]["sensitive_terms"]) == len(
        set(config["story_safety"]["sensitive_terms"])
    )
    assert config["_README"]["常见修改"]["增加敏感词"]


def test_dialogue_config_rejects_invalid_runtime_invariants(monkeypatch, tmp_path) -> None:
    config_path = tmp_path / "dialogue_config.json"
    monkeypatch.setattr("npc_app.utils._DIALOGUE_CONFIG_PATH", config_path)
    base = copy.deepcopy(dialogue_config())

    def missing_character(config):
        config["characters"].pop("Orin")

    def missing_refusal(config):
        config["characters"]["Orin"]["annoyed_responses"].pop("firm")

    def enabled_without_top_k(config):
        config["intent_policies"]["ask_clue"]["top_k"] = 0

    def disabled_with_top_k(config):
        config["intent_policies"]["greeting"]["top_k"] = 1

    def excessive_top_k(config):
        config["intent_policies"]["ask_clue"]["top_k"] = 7

    for mutate in (missing_character, missing_refusal, enabled_without_top_k, disabled_with_top_k, excessive_top_k):
        invalid = copy.deepcopy(base)
        mutate(invalid)
        config_path.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
        dialogue_config.cache_clear()
        with pytest.raises(RuntimeError):
            dialogue_config()
    dialogue_config.cache_clear()


def test_unknown_character_fails_instead_of_using_a_default() -> None:
    with pytest.raises(KeyError):
        character_dialogue("Unknown")


def test_required_prompt_file_and_profile_marker_fail_explicitly(monkeypatch, tmp_path) -> None:
    npc_prompt_service._read_text.cache_clear()
    with pytest.raises(FileNotFoundError):
        npc_prompt_service._read_text(tmp_path / "missing.md")

    npc_prompt_service._load_profile.cache_clear()
    monkeypatch.setattr(npc_prompt_service, "_read_text", lambda _path: "# no npc profiles")
    with pytest.raises(RuntimeError, match="profile marker"):
        npc_prompt_service._load_profile("Orin")
    npc_prompt_service._load_profile.cache_clear()


def test_unity_contract_preserves_nested_game_state() -> None:
    request = UnityNpcTurnRequest.model_validate(
        {
            "question": "这把钥匙能开什么？",
            "npc_id": "Orin",
            "player": {
                "location": "lighthouse",
                "current_quest": "open_old_door",
                "presented_items": ["old_key"],
            },
            "relationship": {"trust": 3, "favorability": 70, "annoyance": 12},
            "story": {"unlock_level": 3},
            "intent_hint": "ask_item",
        }
    )

    assert request.npc_id == "Orin"
    assert request.player.location == "lighthouse"
    assert request.player.presented_items == ["old_key"]
    assert request.relationship.trust == 3
    assert request.story.unlock_level == 3


def test_unity_chat_is_the_only_npc_chat_route() -> None:
    paths = {route.path for route in app.routes}

    assert "/v1/npc/chat/stream" in paths
    assert "/npc/chat/stream" not in paths
    assert "/auth/me" not in paths
    assert "/threads" not in paths
    assert "/threads/{thread_id}/records" not in paths


@pytest.mark.parametrize("npc_id", ["Unknown", 'Orin" && user_id > 0'])
def test_unity_contract_rejects_unknown_or_expression_like_npc_id(npc_id: str) -> None:
    with pytest.raises(ValidationError):
        UnityNpcTurnRequest.model_validate({"question": "你好", "npc_id": npc_id})


def test_unity_contract_rejects_non_uuid_thread_id() -> None:
    with pytest.raises(ValidationError):
        UnityNpcTurnRequest.model_validate({"question": "你好", "npc_id": "Orin", "thread_id": "thread-1"})


def test_auth_contract_normalizes_username_consistently() -> None:
    assert RegisterRequest(username=" player ", password="secret1").username == "player"
    assert LoginRequest(username=" player ", password="secret1").username == "player"


@pytest.mark.parametrize("username", ["  ", "bad name", 'name"'])
def test_auth_contract_rejects_invalid_username(username: str) -> None:
    with pytest.raises(ValidationError):
        RegisterRequest(username=username, password="secret1")
    with pytest.raises(ValidationError):
        LoginRequest(username=username, password="secret1")


def test_demo_models_store_only_runtime_data() -> None:
    columns = {model: set(model.__table__.columns.keys()) for model in Base.__subclasses__()}
    assert columns[NpcUser] == {"id", "username", "password_hash"}
    assert columns[NpcChatThread] == {"id", "user_id", "npc_id", "updated_at"}
    assert columns[NpcChatRecord] == {"id", "thread_id", "question", "answer"}
    assert columns[NpcThreadMemory] == {"id", "thread_id", "summary", "last_record_id"}
    assert columns[NpcMemoryItem] == {"id", "thread_id", "memory_type", "content", "keywords", "importance"}
    assert "npc_search_sources" not in Base.metadata.tables


def test_explicit_thread_id_rejects_mismatched_npc() -> None:
    db = MagicMock()
    user = NpcUser(id=7, username="player", password_hash="hash")
    thread = NpcChatThread(id="karo-thread", user_id=user.id, npc_id="Karo")
    db.query.return_value.filter.return_value.first.return_value = thread
    with pytest.raises(HTTPException) as exc_info:
        get_or_create_chat_thread(db=db, user=user, thread_id=thread.id, npc_id="Lira")
    assert exc_info.value.status_code == 409
    assert exc_info.value.detail == "thread_id 与 npc_id 不匹配，请使用该 NPC 对应的线程 ID"
    db.add.assert_not_called()
    db.commit.assert_not_called()


def test_explicit_thread_id_returns_thread_when_npc_matches() -> None:
    db = MagicMock()
    user = NpcUser(id=7, username="player", password_hash="hash")
    thread = NpcChatThread(id="lira-thread", user_id=user.id, npc_id="Lira")
    db.query.return_value.filter.return_value.first.return_value = thread
    assert get_or_create_chat_thread(db=db, user=user, thread_id=thread.id, npc_id="Lira") is thread


def test_unity_contract_rejects_duplicate_or_blank_game_state_values() -> None:
    with pytest.raises(ValidationError):
        UnityNpcTurnRequest.model_validate(
            {"question": "看看", "npc_id": "Orin", "player": {"presented_items": ["old_key", "old_key"]}}
        )
    with pytest.raises(ValidationError):
        UnityNpcTurnRequest.model_validate(
            {"question": "看看", "npc_id": "Orin", "player": {"presented_items": ["   "]}}
        )
