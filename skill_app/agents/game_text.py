from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from skill_app.agents.base import AgentContext
from skill_app.config import get_settings
from skill_app.schemas.model import ModelMessage
from skill_app.schemas.project_workflows import (
    GameTextCandidate,
    GameTextCandidateSet,
    GameTextReviewResult,
    GameTextValidationResult,
)
from skill_app.schemas.review import ReviewFinding
from skill_app.schemas.skill import SkillLimits
from skill_app.services import model_gateway
from skill_app.services.skill_loader import load_skill
from skill_app.services.skill_registry import (
    register_builtin_skill_bindings,
    run_skill_validators,
    schema_registry,
)

SENSITIVE_TERMS = (
    "Subject 07",
    "浣庢俯浼戠湢",
    "淇濇姢浠?",
    "鐖舵瘝鏉冮檺",
    "涓诲叡鎸牳蹇?",
    "鏈€缁堢湡鐩?",
)


class GameTextWriterAgent:
    prompt_version = "game-text-writer-v1"

    def generate(
        self,
        context: AgentContext,
        *,
        request: str,
        repair_count: int = 0,
        previous: GameTextCandidateSet | GameTextCandidate | None = None,
        evidence: dict | None = None,
        instruction_text: str | None = None,
        profile_name: str | None = None,
        output_schema_name: str = "game_text_candidate_set",
        output_validators: list[str] | None = None,
        limits: SkillLimits | None = None,
    ) -> GameTextCandidateSet:
        previous_set = normalize_candidate_set(previous) if previous is not None else None
        baseline = previous_set or GameTextCandidateSet(
            candidates=[
                GameTextCandidate(
                    path=f"game_docs/vectorized/25_generated_story_{context.task.id[:8]}.md",
                    content=(
                        "---\n"
                        "source: generated_story.md\n"
                        "rag_ingest: true\n"
                        "default_unlock_level: 0\n"
                        "tags:\n"
                        "  - generated_lore\n"
                        "---\n\n"
                        "# Generated Game Text Candidate\n\n"
                        "## Village Rumor (unlock 0)\n\n"
                        f"{request}\n"
                    ),
                    summary="Create a retrieval-friendly game text candidate.",
                    source_paths=["game_docs/vectorized/01_world_lore.md"],
                    intended_unlock_level=0,
                )
            ],
            summary="Create retrieval-friendly game text candidates.",
        )
        selected_profile = profile_name or get_settings().model_profile
        profile = model_gateway.get_profile(selected_profile)
        user_content = baseline.model_dump_json()
        if profile.provider != "fake":
            user_content = json.dumps(
                {
                    "request": request,
                    "repair_count": repair_count,
                    "context": self._context_pack(request),
                    "previous_candidate_set": (
                        previous_set.model_dump(mode="json") if previous_set else None
                    ),
                    "repair_evidence": evidence or {},
                    "shape_example": baseline.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        register_builtin_skill_bindings()
        response_model = schema_registry.get(output_schema_name)
        candidate = model_gateway.invoke_structured(
            context.db,
            task_id=context.task.id,
            step_run_id=context.step_run.id,
            profile_name=selected_profile,
            messages=[
                ModelMessage(
                    role="system",
                    content=(
                        f"{_prompt('game_text_writer')}\n\n"
                        f"{instruction_text or load_skill('game-text-writer').body}"
                    ),
                ),
                ModelMessage(role="user", content=user_content),
            ],
            response_model=response_model,
            prompt_version=self.prompt_version,
        )
        candidate_set = normalize_candidate_set(candidate)
        self.validate_paths(candidate_set)
        run_skill_validators(
            output_validators or [],
            candidate_set,
            {
                "max_changed_lines": (
                    limits.max_changed_lines if limits is not None else 1000
                ),
                "max_files": limits.max_files if limits is not None else 1,
            },
        )
        if previous_set and candidate_set.model_dump() == previous_set.model_dump():
            raise ValueError("Game text repair must change the candidate set.")
        return candidate_set

    def validate_path(self, candidate: GameTextCandidate) -> None:
        self.validate_paths(
            GameTextCandidateSet(candidates=[candidate], summary=candidate.summary)
        )

    def validate_paths(self, candidate_set: GameTextCandidateSet) -> None:
        seen: set[str] = set()
        for candidate in candidate_set.candidates:
            path = Path(candidate.path)
            if (
                path.is_absolute()
                or ".." in path.parts
                or not candidate.path.startswith("game_docs/")
                or path.suffix.lower() != ".md"
            ):
                raise ValueError("Game text path must be a Markdown file under game_docs/.")
            normalized = candidate.path.replace("\\", "/")
            if normalized in seen:
                raise ValueError("Game text paths must be unique.")
            seen.add(normalized)

    def _context_pack(self, request: str) -> dict:
        root = get_settings().project_root.resolve()
        priority = [
            "game_docs/non_vectorized/B_npc_profiles_瑙掕壊妗ｆ.md",
            "game_docs/non_vectorized/E_npc_prompt_rules_NPC鍥炵瓟瑙勫垯.md",
            "game_docs/vectorized/01_world_lore_涓栫晫瑙?md",
        ]
        terms = [term.casefold() for term in request.split() if len(term) > 1]
        extras = sorted(
            (root / "game_docs").rglob("*.md"),
            key=lambda path: sum(term in path.as_posix().casefold() for term in terms),
            reverse=True,
        )
        paths = [root / item for item in priority] + extras[:1]
        seen = set()
        files = []
        for path in paths:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "content": path.read_text(encoding="utf-8")[:2000],
                }
            )
        return {"files": files}


class LoreReviewAgent:
    prompt_version = "lore-reviewer-v1"

    def review(
        self,
        context: AgentContext,
        *,
        request: str,
        candidate: GameTextCandidateSet | GameTextCandidate,
        validation: GameTextValidationResult,
        instruction_text: str | None = None,
        profile_name: str | None = None,
        output_schema_name: str = "game_text_review_result",
        output_validators: list[str] | None = None,
    ) -> GameTextReviewResult:
        candidate_set = normalize_candidate_set(candidate)
        deterministic = [
            ReviewFinding(
                severity="high",
                category="validation",
                file=str(item.get("path") or candidate_set.candidates[0].path),
                line=1,
                title=str(item.get("error") or item),
                evidence=(
                    f"{item.get('path') or candidate_set.candidates[0].path}:1 "
                    f"- {item.get('error') or item}"
                ),
                suggestion="Repair the candidate metadata or content.",
                blocking=True,
            )
            for item in validation.metadata.get(
                "errors_by_path",
                [
                    {"path": candidate_set.candidates[0].path, "error": error}
                    for error in validation.errors
                ],
            )
        ]
        baseline = GameTextReviewResult(
            approved=not deterministic,
            summary="Lore review passed." if not deterministic else "Lore review requires repair.",
            findings=deterministic,
        )
        selected_profile = profile_name or get_settings().model_profile
        profile = model_gateway.get_profile(selected_profile)
        user_content = baseline.model_dump_json()
        if profile.provider != "fake":
            writer = GameTextWriterAgent()
            user_content = json.dumps(
                {
                    "request": request,
                    "candidate_set": candidate_set.model_dump(mode="json"),
                    "validation": validation.model_dump(mode="json"),
                    "lore_context": writer._context_pack(request),
                    "deterministic_findings": [
                        item.model_dump(mode="json") for item in deterministic
                    ],
                },
                ensure_ascii=False,
            )
        register_builtin_skill_bindings()
        response_model = schema_registry.get(output_schema_name)
        result = model_gateway.invoke_structured(
            context.db,
            task_id=context.task.id,
            step_run_id=context.step_run.id,
            profile_name=selected_profile,
            messages=[
                ModelMessage(
                    role="system",
                    content=(
                        f"{_prompt('lore_reviewer')}\n\n"
                        f"{instruction_text or load_skill('lore-reviewer').body}"
                    ),
                ),
                ModelMessage(role="user", content=user_content),
            ],
            response_model=response_model,
            prompt_version=self.prompt_version,
        )
        if not isinstance(result, GameTextReviewResult):
            raise ValueError("Lore reviewer returned an incompatible output schema.")
        findings = _normalize_text_findings(
            [*deterministic, *result.findings],
            candidate_set,
        )
        normalized = GameTextReviewResult(
            approved=not any(item.blocking for item in findings),
            summary="Lore review passed." if not findings else "Lore review requires repair.",
            findings=findings,
        )
        run_skill_validators(output_validators or [], normalized)
        return normalized


def validate_game_text(
    candidate: GameTextCandidateSet | GameTextCandidate,
) -> GameTextValidationResult:
    candidate_set = normalize_candidate_set(candidate)
    errors: list[str] = []
    warnings: list[str] = []
    errors_by_path: list[dict[str, str]] = []
    warnings_by_path: list[dict[str, str]] = []
    paths = [item.path for item in candidate_set.candidates]
    for item in candidate_set.candidates:
        result = _validate_single_game_text(item)
        errors.extend(f"{item.path}: {error}" for error in result.errors)
        warnings.extend(f"{item.path}: {warning}" for warning in result.warnings)
        errors_by_path.extend(
            {"path": item.path, "error": error} for error in result.errors
        )
        warnings_by_path.extend(
            {"path": item.path, "warning": warning} for warning in result.warnings
        )
    if len(paths) != len(set(paths)):
        error = "Candidate paths must be unique."
        errors.append(error)
        errors_by_path.append({"path": paths[0] if paths else "game_docs/", "error": error})
    return GameTextValidationResult(
        valid=not errors,
        checks=[
            "candidate_set",
            "path",
            "non_empty",
            "frontmatter",
            "unlock_headings",
            "spoiler_terms",
        ],
        errors=errors,
        warnings=warnings,
        metadata={
            "paths": paths,
            "unlock_levels": {
                item.path: item.intended_unlock_level for item in candidate_set.candidates
            },
            "source_paths": sorted(
                {path for item in candidate_set.candidates for path in item.source_paths}
            ),
            "errors_by_path": errors_by_path,
            "warnings_by_path": warnings_by_path,
        },
    )


def _validate_single_game_text(candidate: GameTextCandidate) -> GameTextValidationResult:
    checks = ["path", "non_empty", "frontmatter", "unlock_headings", "spoiler_terms"]
    errors: list[str] = []
    warnings: list[str] = []
    text = candidate.content
    if not text.strip():
        errors.append("Candidate text is empty.")
    vectorized = candidate.path.startswith("game_docs/vectorized/")
    if vectorized:
        if not text.startswith("---") or "rag_ingest: true" not in text[:1000]:
            errors.append("Vectorized text requires frontmatter with rag_ingest: true.")
        headings = re.findall(r"^##\s+(.+)$", text, flags=re.MULTILINE)
        if not headings:
            errors.append("Vectorized text requires at least one H2 section.")
        for heading in headings:
            if not re.search(r"(瑙ｉ攣|unlock)\s*\d+", heading, flags=re.IGNORECASE):
                errors.append(f"Section heading lacks unlock level: {heading}")
    if candidate.intended_unlock_level <= 2:
        leaked = [term for term in SENSITIVE_TERMS if term in text]
        if leaked:
            errors.append(f"Low-unlock text contains sensitive terms: {leaked}")
    if any(term in text for term in ("鏍规嵁鏂囨。", "鐭ヨ瘑搴?", "RAG", "prompt", "Prompt")):
        warnings.append("Candidate contains developer-facing language.")
    return GameTextValidationResult(
        valid=not errors,
        checks=checks,
        errors=errors,
        warnings=warnings,
        metadata={
            "path": candidate.path,
            "intended_unlock_level": candidate.intended_unlock_level,
            "source_paths": candidate.source_paths,
        },
    )


def _normalize_text_findings(
    findings: list[ReviewFinding],
    candidate: GameTextCandidateSet | GameTextCandidate,
) -> list[ReviewFinding]:
    candidate_set = normalize_candidate_set(candidate)
    line_counts = {
        item.path: max(1, len(item.content.splitlines()))
        for item in candidate_set.candidates
    }
    normalized = []
    seen = set()
    for finding in findings:
        if finding.file not in line_counts:
            raise ValueError("Lore finding references a non-candidate file.")
        line = finding.line or 1
        if line > line_counts[finding.file]:
            raise ValueError("Lore finding line is outside the candidate.")
        blocking = finding.severity in {"critical", "high", "medium"}
        evidence = finding.evidence
        if f"{finding.file}:{line}" not in evidence:
            evidence = f"{finding.file}:{line} - {evidence}"
        key = (finding.file, line, finding.category, finding.title.casefold())
        if key not in seen:
            seen.add(key)
            normalized.append(
                finding.model_copy(
                    update={"line": line, "blocking": blocking, "evidence": evidence}
                )
            )
    return normalized


def normalize_candidate_set(value: Any) -> GameTextCandidateSet:
    if isinstance(value, GameTextCandidateSet):
        return value
    if isinstance(value, GameTextCandidate):
        return GameTextCandidateSet(candidates=[value], summary=value.summary)
    raise ValueError("Game-text writer returned an incompatible output schema.")


def _prompt(name: str) -> str:
    return (
        Path(__file__).resolve().parents[1] / "prompts" / name / "v1.md"
    ).read_text(encoding="utf-8")
