from __future__ import annotations

import ast
from pathlib import Path
from typing import Protocol

from skill_app.agents.base import AgentContext
from skill_app.agents.code_generator import CodeGeneratorAgent
from skill_app.agents.config_generator import NPCBehaviorConfigGenerator
from skill_app.schemas.code_generation import RepairEvidencePack
from skill_app.schemas.game_feature import (
    CandidateReview,
    CandidateSpec,
    CandidateValidation,
    ReviewFinding,
)
from skill_app.tools.config_validation import validate_npc_behavior_yaml


class CandidateGenerator(Protocol):
    def generate(
        self,
        *,
        task_id: str,
        request: str,
        task_type: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
        repair_evidence: RepairEvidencePack | None = None,
        context: AgentContext | None = None,
        relevant_paths: list[str] | None = None,
    ) -> CandidateSpec: ...


class DeterministicCandidateGenerator:
    def generate(
        self,
        *,
        task_id: str,
        request: str,
        task_type: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
        repair_evidence: RepairEvidencePack | None = None,
        context: AgentContext | None = None,
        relevant_paths: list[str] | None = None,
    ) -> CandidateSpec:
        if _is_config_request(request, task_type):
            return NPCBehaviorConfigGenerator().generate(
                task_id=task_id,
                request=request,
                repair_count=repair_count,
                previous_content=previous_content,
                validation_errors=validation_errors,
            )
        generator = CodeGeneratorAgent()
        result = generator.generate(
            task_id=task_id,
            request=request,
            repair_count=repair_count,
            previous_content=previous_content,
            validation_errors=validation_errors,
            review_findings=review_findings,
            repair_evidence=repair_evidence,
            context=context,
            relevant_paths=relevant_paths,
        )
        return generator.to_candidate(result)


def validate_candidate(candidate: CandidateSpec) -> CandidateValidation:
    errors: list[str] = []
    checks = ["path_is_relative", "content_is_non_empty"]
    path = Path(candidate.relative_path)
    if path.is_absolute() or ".." in path.parts:
        errors.append("Candidate path must remain relative.")
    if not candidate.content.strip():
        errors.append("Candidate content is empty.")
    if candidate.kind == "game_config":
        report = validate_npc_behavior_yaml(candidate.content)
        checks.extend(report.checks)
        errors.extend(report.errors)
        details = {
            "schema_version": report.schema_version,
            "normalized_config": report.normalized_config,
        }
    else:
        details = {}
        checks.append("python_syntax")
        try:
            ast.parse(candidate.content)
        except SyntaxError as exc:
            errors.append(f"Invalid Python syntax at line {exc.lineno}: {exc.msg}.")
    return CandidateValidation(valid=not errors, errors=errors, checks=checks, details=details)


def review_candidate(candidate: CandidateSpec, validation: CandidateValidation) -> CandidateReview:
    findings: list[ReviewFinding] = []
    for error in validation.errors:
        findings.append(
            ReviewFinding(
                severity="high",
                category="validation",
                file=candidate.relative_path,
                title="Candidate validation failed",
                evidence=error,
                suggestion="Repair the candidate and rerun deterministic validation.",
                blocking=True,
            )
        )
    if "TODO" in candidate.content:
        findings.append(
            ReviewFinding(
                severity="medium",
                category="completeness",
                file=candidate.relative_path,
                title="Unresolved TODO marker",
                evidence="Candidate contains TODO.",
                suggestion="Resolve TODO before delivery.",
                blocking=True,
            )
        )
    return CandidateReview(
        approved=not any(finding.blocking for finding in findings),
        summary="Candidate approved by deterministic review." if not findings else "Candidate requires repair.",
        findings=findings,
    )


def _is_config_request(request: str, task_type: str) -> bool:
    text = f"{request} {task_type}".casefold()
    return any(keyword in text for keyword in ("配置", "config", "json", "yaml", "数值"))


candidate_generator: CandidateGenerator = DeterministicCandidateGenerator()


def set_candidate_generator(generator: CandidateGenerator) -> None:
    global candidate_generator
    candidate_generator = generator


def reset_candidate_generator() -> None:
    global candidate_generator
    candidate_generator = DeterministicCandidateGenerator()
