from __future__ import annotations

import hashlib
import uuid

import pytest

from skill_app.agents.game_text import GameTextWriterAgent, validate_game_text
from skill_app.agents.npc_app_feature import NpcAppFeatureAgent, _normalize_findings
from skill_app.config import get_settings
from skill_app.db.models import TaskWorkspace
from skill_app.schemas.code_generation import CodePatch, PatchFile
from skill_app.schemas.project_workflows import (
    GameTextCandidate,
    GameTextCandidateSet,
    NpcAppPatchResult,
)
from skill_app.schemas.review import ReviewFinding
from skill_app.workflows.npc_app_feature import _build_repair_evidence


def test_npc_app_agent_accepts_bounded_multi_file_patch_and_rejects_escape():
    result = NpcAppPatchResult(
        patch=CodePatch(
            files=[
                PatchFile(path="npc_app/services/demo_service.py", content="VALUE = 1\n"),
                PatchFile(path="npc_app/schemas/demo.py", content="VALUE = 2\n"),
            ],
            explanation="Add service and schema.",
        ),
        affected_files=[
            "npc_app/services/demo_service.py",
            "npc_app/schemas/demo.py",
        ],
        summary="Add service and schema.",
    )
    normalized = NpcAppFeatureAgent().normalize_and_validate(result)
    assert len(normalized.patch.files) == 2
    assert all(item.expected_sha256 is None for item in normalized.patch.files)

    escaped = result.model_copy(deep=True)
    escaped.patch.files[0].path = "game_docs/demo.py"
    escaped.affected_files[0] = "game_docs/demo.py"
    with pytest.raises(ValueError, match="under npc_app"):
        NpcAppFeatureAgent().normalize_and_validate(escaped)


def test_npc_app_repair_hashes_previous_workspace_content():
    previous = NpcAppPatchResult(
        patch=CodePatch(
            files=[PatchFile(path="npc_app/services/demo.py", content="VALUE = 1\n")],
            explanation="Initial.",
        ),
        affected_files=["npc_app/services/demo.py"],
        summary="Initial.",
    )
    repaired = NpcAppPatchResult(
        patch=CodePatch(
            files=[PatchFile(path="npc_app/services/demo.py", content="VALUE = 2\n")],
            explanation="Repair.",
        ),
        affected_files=["npc_app/services/demo.py"],
        summary="Repair.",
    )
    normalized = NpcAppFeatureAgent().normalize_and_validate(repaired, previous)
    assert normalized.patch.files[0].expected_sha256 == hashlib.sha256(
        b"VALUE = 1\n"
    ).hexdigest()


def test_npc_app_repair_evidence_is_bounded_to_failure_and_current_files():
    task_id = str(uuid.uuid4())
    workspace_id = str(uuid.uuid4())
    workspace_root = get_settings().workspace_root / task_id / workspace_id
    source = workspace_root / "npc_app" / "services" / "demo.py"
    test_file = workspace_root / "npc_app" / "services" / "test_demo.py"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    test_file.write_text(
        "def test_value():\n"
        "    assert value() == 2\n",
        encoding="utf-8",
    )
    patch = NpcAppPatchResult(
        patch=CodePatch(
            files=[PatchFile(path="npc_app/services/demo.py", content="def value():\n    return 1\n")],
            explanation="Initial.",
        ),
        affected_files=["npc_app/services/demo.py"],
        summary="Initial.",
    )
    long_stdout = (
        "FAILED npc_app\\services\\test_demo.py::test_value\n"
        "npc_app\\services\\test_demo.py:2: AssertionError\n"
        "E       assert 1 == 2\n"
        + ("x" * 5000)
    )
    state = {
        "npc_app_patch": patch.model_dump(mode="json"),
        "npc_app_test_suite": {
            "plan": {
                "candidate_path": "npc_app/services/demo.py",
                "test_path": "npc_app/services/test_demo.py",
                "cases": ["value"],
                "uncovered_risks": [],
            },
            "test_content": test_file.read_text(encoding="utf-8"),
        },
        "npc_app_test_report": {
            "passed": False,
            "compile_passed": True,
            "pytest_passed": False,
            "candidate_path": "npc_app/services/demo.py",
            "test_path": "npc_app/services/test_demo.py",
            "stdout": long_stdout,
            "stderr": "",
            "errors": ["Generated npc_app pytest failed."],
            "uncovered_risks": [],
        },
        "repair_summaries": ["old" * 3000],
    }
    workspace = TaskWorkspace(
        id=workspace_id,
        task_id=task_id,
        source_root=str(get_settings().project_root),
        workspace_root=str(workspace_root),
        status="active",
        copied_paths=[],
    )
    evidence = _build_repair_evidence(state, workspace)

    assert evidence["failure"]["source"] == "pytest"
    assert len(evidence["failure"]["stdout"]) < len(long_stdout)
    assert evidence["current_files"][0]["path"] == "npc_app/services/demo.py"
    assert "def value" in evidence["current_files"][0]["content"]
    assert evidence["test_excerpt"]["start_line"] == 1
    assert evidence["constraints"]["allowed_paths"] == ["npc_app/services/demo.py"]


def test_npc_app_review_normalizes_model_finding_locations():
    findings = [
        ReviewFinding(
            severity="medium",
            category="style",
            file="npc_app/services/demo.py",
            line=99,
            title="Out of range",
            evidence="model evidence",
            suggestion="Fix it.",
            blocking=True,
        ),
        ReviewFinding(
            severity="high",
            category="hallucination",
            file="npc_app/services/missing.py",
            line=1,
            title="Unknown file",
            evidence="model evidence",
            suggestion="Fix it.",
            blocking=True,
        ),
    ]
    normalized = _normalize_findings(findings, {"npc_app/services/demo.py": "a = 1\nb = 2\n"})

    assert len(normalized) == 1
    assert normalized[0].line == 2
    assert normalized[0].blocking is True
    assert "npc_app/services/demo.py:2" in normalized[0].evidence


def test_game_text_validation_requires_rag_metadata_and_unlock_headings():
    invalid = GameTextCandidate(
        path="game_docs/vectorized/25_demo.md",
        content="# Demo\n\n## Missing unlock\n\nText.",
        summary="demo",
        intended_unlock_level=0,
    )
    report = validate_game_text(invalid)
    assert not report.valid
    assert any("frontmatter" in error for error in report.errors)
    assert any("unlock level" in error for error in report.errors)


def test_game_text_validation_blocks_low_unlock_spoilers_and_safe_path_escape():
    candidate = GameTextCandidate(
        path="game_docs/vectorized/25_demo.md",
        content=(
            "---\nsource: demo.md\nrag_ingest: true\ndefault_unlock_level: 0\n---\n\n"
            "# Demo\n\n## 传闻（解锁0）\n\n这里直接写出 Subject 07。\n"
        ),
        summary="demo",
        intended_unlock_level=0,
    )
    report = validate_game_text(candidate)
    assert not report.valid
    assert any("sensitive terms" in error for error in report.errors)

    escaped = candidate.model_copy(update={"path": "../game_docs/demo.md"})
    with pytest.raises(ValueError, match="under game_docs"):
        GameTextWriterAgent().validate_path(escaped)


def test_game_text_validation_accepts_multiple_markdown_candidates():
    candidate_set = GameTextCandidateSet(
        summary="Add market and shrine rumors.",
        candidates=[
            GameTextCandidate(
                path="game_docs/vectorized/25_market.md",
                content=(
                    "---\nsource: demo.md\nrag_ingest: true\ndefault_unlock_level: 0\n---\n\n"
                    "# Demo\n\n## Market rumor unlock 0\n\nA safe rumor.\n"
                ),
                summary="market",
                intended_unlock_level=0,
            ),
            GameTextCandidate(
                path="game_docs/vectorized/25_shrine.md",
                content=(
                    "---\nsource: demo.md\nrag_ingest: true\ndefault_unlock_level: 1\n---\n\n"
                    "# Demo\n\n## Shrine rumor unlock 1\n\nAnother safe rumor.\n"
                ),
                summary="shrine",
                intended_unlock_level=1,
            ),
        ],
    )

    report = validate_game_text(candidate_set)

    assert report.valid
    assert report.metadata["paths"] == [
        "game_docs/vectorized/25_market.md",
        "game_docs/vectorized/25_shrine.md",
    ]
