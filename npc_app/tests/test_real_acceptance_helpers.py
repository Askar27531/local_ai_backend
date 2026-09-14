from __future__ import annotations

import json

from scripts.run_real_npc_acceptance import CheckResult, _contains_sensitive, _request, _write_report


def test_real_acceptance_request_uses_nested_unity_contract() -> None:
    payload = _request(
        "问题",
        "Orin",
        thread_id="thread-1",
        trust=2,
        story_level=3,
        player={"presented_items": ["old_key"]},
    )

    assert payload["thread_id"] == "thread-1"
    assert payload["relationship"]["trust"] == 2
    assert payload["story"]["unlock_level"] == 3
    assert payload["player"]["presented_items"] == ["old_key"]


def test_real_acceptance_sensitive_check_is_whitespace_insensitive() -> None:
    assert _contains_sensitive("Subject  07") is True
    assert _contains_sensitive("我不知道你说的是什么。") is False


def test_real_acceptance_report_contains_failures(tmp_path) -> None:
    results = [
        CheckResult("ok", True, 10.0, {"answer": "通过"}),
        CheckResult("bad", False, 20.0, error="AssertionError: failed"),
    ]

    json_path, markdown_path = _write_report(results, tmp_path, "http://127.0.0.1:8001")

    payload = json.loads(json_path.read_text(encoding="utf-8"))
    assert payload["passed"] is False
    assert payload["passed_checks"] == 1
    assert "Real NPC Backend Acceptance — FAIL" in markdown_path.read_text(encoding="utf-8")
