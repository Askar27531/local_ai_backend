from __future__ import annotations

import hashlib
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from skill_app.domain.errors import ConfigurationError
from skill_app.domain.status import TaskStatus
from skill_app.schemas.art import ArtManifest, VisualBrief
from skill_app.schemas.code_generation import CodeGenerationResult
from skill_app.schemas.game_feature import CandidateSpec
from skill_app.schemas.plan import TaskPlan
from skill_app.schemas.project_workflows import (
    GameTextCandidate,
    GameTextCandidateSet,
    GameTextReviewResult,
    GameTextValidationResult,
    NpcAppPatchResult,
    NpcAppReviewResult,
    NpcAppTestSuite,
)
from skill_app.schemas.review import SemanticReviewResult
from skill_app.schemas.test_report import GeneratedTestSuite

SkillValidator = Callable[[Any, dict[str, Any]], None]


class NamedRegistry:
    def __init__(self, kind: str) -> None:
        self.kind = kind
        self._items: dict[str, Any] = {}

    def register(self, name: str, value: Any) -> None:
        if name in self._items and self._items[name] != value:
            raise ConfigurationError(
                f"Duplicate {self.kind} registration: {name}",
                {"kind": self.kind, "name": name},
            )
        self._items[name] = value

    def get(self, name: str) -> Any:
        if name not in self._items:
            raise ConfigurationError(
                f"Unknown {self.kind}: {name}",
                {"kind": self.kind, "name": name},
            )
        return self._items[name]

    def names(self) -> set[str]:
        return set(self._items)


schema_registry = NamedRegistry("skill output schema")
validator_registry = NamedRegistry("skill validator")
agent_registry = NamedRegistry("skill agent")


def run_skill_validators(
    names: list[str],
    value: Any,
    context: dict[str, Any] | None = None,
) -> None:
    register_builtin_skill_bindings()
    for name in names:
        validator_registry.get(name)(value, context or {})


def _structured_output(value: Any, _: dict[str, Any]) -> None:
    if not isinstance(value, BaseModel):
        raise ValueError("Skill output must be a validated Pydantic model.")


def _npc_app_patch_paths(value: Any, _: dict[str, Any]) -> None:
    if not isinstance(value, NpcAppPatchResult):
        raise ValueError("npc_app path validation requires NpcAppPatchResult.")
    for item in value.patch.files:
        if not item.path.startswith("npc_app/") or ".." in item.path.replace("\\", "/").split("/"):
            raise ValueError("npc_app patch path escaped npc_app/.")


def _affected_files_match(value: Any, _: dict[str, Any]) -> None:
    if not isinstance(value, NpcAppPatchResult):
        raise ValueError("Affected-file validation requires NpcAppPatchResult.")
    if value.affected_files != [item.path for item in value.patch.files]:
        raise ValueError("affected_files must exactly match patch files.")


def _expected_hashes_match(value: Any, context: dict[str, Any]) -> None:
    if not isinstance(value, NpcAppPatchResult):
        raise ValueError("Hash validation requires NpcAppPatchResult.")
    previous = context.get("previous_patch")
    if previous is None:
        return
    previous_hashes = {
        item.path: hashlib.sha256(item.content.encode("utf-8")).hexdigest()
        for item in previous.patch.files
    }
    for item in value.patch.files:
        if item.path in previous_hashes and item.expected_sha256 != previous_hashes[item.path]:
            raise ValueError("Repair candidate does not carry the previous candidate hash.")


def _task_is_running(_: Any, context: dict[str, Any]) -> None:
    if context.get("task_status") != TaskStatus.RUNNING:
        raise ValueError("Skill requires a running task.")


def _workspace_is_active(_: Any, context: dict[str, Any]) -> None:
    if context.get("workspace_status") != "active":
        raise ValueError("Skill requires an active workspace.")


def _python_compile(value: Any, _: dict[str, Any]) -> None:
    if not getattr(value, "compile_passed", False):
        raise ValueError("Skill post-validation requires successful Python compilation.")


def _focused_pytest(value: Any, _: dict[str, Any]) -> None:
    if not getattr(value, "pytest_passed", False):
        raise ValueError("Skill post-validation requires focused pytest success.")


def _semantic_review(value: Any, _: dict[str, Any]) -> None:
    if any(getattr(item, "blocking", False) for item in getattr(value, "findings", [])):
        raise ValueError("Skill post-validation found blocking semantic review findings.")


def _game_text_candidate_path(value: Any, _: dict[str, Any]) -> None:
    candidates = _game_text_candidates(value)
    seen: set[str] = set()
    for candidate in candidates:
        normalized = candidate.path.replace("\\", "/")
        if (
            not normalized.startswith("game_docs/")
            or not normalized.endswith(".md")
            or ".." in normalized.split("/")
        ):
            raise ValueError("Game-text candidate must be Markdown under game_docs/.")
        if normalized in seen:
            raise ValueError("Game-text candidate paths must be unique.")
        seen.add(normalized)


def _game_text_source_paths(value: Any, _: dict[str, Any]) -> None:
    for candidate in _game_text_candidates(value):
        for path in candidate.source_paths:
            normalized = path.replace("\\", "/")
            if (
                not normalized.startswith("game_docs/")
                or not normalized.endswith(".md")
                or ".." in normalized.split("/")
            ):
                raise ValueError("Game-text source paths must remain under game_docs/.")


def _game_text_content_limit(value: Any, context: dict[str, Any]) -> None:
    candidates = _game_text_candidates(value)
    max_files = int(context.get("max_files", 1))
    if len(candidates) > max_files:
        raise ValueError("Game-text candidate set exceeds the Skill file limit.")
    max_lines = int(context.get("max_changed_lines", 1000))
    changed_lines = sum(len(candidate.content.splitlines()) for candidate in candidates)
    if changed_lines > max_lines:
        raise ValueError("Game-text candidate set exceeds the Skill line limit.")


def _game_text_candidates(value: Any) -> list[GameTextCandidate]:
    if isinstance(value, GameTextCandidate):
        return [value]
    if isinstance(value, GameTextCandidateSet):
        return value.candidates
    raise ValueError("Game-text validation requires GameTextCandidate or GameTextCandidateSet.")


def _game_text_validation(value: Any, _: dict[str, Any]) -> None:
    if not isinstance(value, GameTextValidationResult) or not value.valid:
        raise ValueError("Game-text deterministic validation did not pass.")


def _lore_review(value: Any, _: dict[str, Any]) -> None:
    if not isinstance(value, GameTextReviewResult) or not value.approved:
        raise ValueError("Lore review did not approve the game-text candidate.")


def register_builtin_skill_bindings() -> None:
    schemas: dict[str, type[BaseModel]] = {
        "code_generation_result": CodeGenerationResult,
        "generated_test_suite": GeneratedTestSuite,
        "semantic_review_result": SemanticReviewResult,
        "task_plan": TaskPlan,
        "npc_app_patch_result": NpcAppPatchResult,
        "npc_app_test_suite": NpcAppTestSuite,
        "npc_app_review_result": NpcAppReviewResult,
        "game_text_candidate": GameTextCandidate,
        "game_text_candidate_set": GameTextCandidateSet,
        "game_text_review_result": GameTextReviewResult,
        "candidate_spec": CandidateSpec,
        "visual_brief": VisualBrief,
        "art_manifest": ArtManifest,
    }
    for name, schema in schemas.items():
        schema_registry.register(name, schema)

    validators: dict[str, SkillValidator] = {
        "task_is_running": _task_is_running,
        "workspace_is_active": _workspace_is_active,
        "structured_output": _structured_output,
        "npc_app_patch_paths": _npc_app_patch_paths,
        "affected_files_match": _affected_files_match,
        "expected_hashes_match": _expected_hashes_match,
        "python_compile": _python_compile,
        "focused_pytest": _focused_pytest,
        "semantic_review": _semantic_review,
        "game_text_candidate_path": _game_text_candidate_path,
        "game_text_source_paths": _game_text_source_paths,
        "game_text_content_limit": _game_text_content_limit,
        "game_text_validation": _game_text_validation,
        "lore_review": _lore_review,
    }
    for name, validator in validators.items():
        validator_registry.register(name, validator)

    for name in {
        "npc_app_feature",
        "npc_app_test",
        "npc_app_review",
        "code_generator",
        "test_agent",
        "review_agent",
        "planner",
        "game_text_writer",
        "lore_reviewer",
        "art_brief_agent",
        "art_asset_agent",
    }:
        agent_registry.register(name, name)
