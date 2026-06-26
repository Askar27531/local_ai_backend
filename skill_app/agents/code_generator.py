from __future__ import annotations

import hashlib
import json
from pathlib import Path

from skill_app.agents.base import AgentContext
from skill_app.config import get_settings
from skill_app.schemas.code_generation import (
    CodeGenerationResult,
    CodePatch,
    PatchFile,
    RepairEvidencePack,
)
from skill_app.schemas.game_feature import CandidateSpec
from skill_app.schemas.model import ModelMessage
from skill_app.services import model_gateway
from skill_app.services.skill_loader import load_skill


class CodeGeneratorAgent:
    name = "code_generator"
    skill_name = "code-generator"
    prompt_version = "code-generator-v1"
    max_files = 5
    max_changed_lines = 400

    def generate(
        self,
        *,
        task_id: str,
        request: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
        repair_evidence: RepairEvidencePack | None = None,
        context: AgentContext | None = None,
        relevant_paths: list[str] | None = None,
    ) -> CodeGenerationResult:
        baseline = self._baseline_result(
            task_id=task_id,
            request=request,
            repair_count=repair_count,
            previous_content=previous_content,
            validation_errors=validation_errors,
            review_findings=review_findings,
        )
        if context is None:
            return baseline
        project_context = self._build_project_context(relevant_paths or [])
        skill = load_skill(self.skill_name)
        profile = model_gateway.get_profile(get_settings().model_profile)
        user_content = baseline.model_dump_json()
        if profile.provider != "fake":
            user_content = json.dumps(
                {
                    "request": request,
                    "repair_count": repair_count,
                    "previous_content": previous_content,
                    "repair_evidence": (
                        repair_evidence.model_dump(mode="json")
                        if repair_evidence is not None
                        else None
                    ),
                    "baseline_shape_example": baseline.model_dump(mode="json"),
                    "instruction": (
                        (
                            "Repair the previous candidate using every item in the evidence pack. "
                            "Keep the same path, preserve unrelated behavior, and make the smallest "
                            "change that resolves failed tests and blocking review findings. "
                        )
                        if repair_evidence is not None
                        else "Implement the request using the project context. "
                    )
                    + (
                        "The baseline is only a shape and compatibility example; "
                        "do not return it unchanged or produce a metadata-only placeholder."
                    ),
                },
                ensure_ascii=False,
            )
        messages = [
            ModelMessage(
                role="system",
                content=(
                    f"{_load_prompt()}\n\nCode generation skill:\n{skill.body}\n\n"
                    f"Project context:\n{json.dumps(project_context, ensure_ascii=False)}\n\n"
                    f"Validation errors:\n{json.dumps(validation_errors, ensure_ascii=False)}\n\n"
                    f"Review findings:\n{json.dumps(review_findings, ensure_ascii=False)}"
                ),
            ),
            ModelMessage(role="user", content=user_content),
        ]
        result = model_gateway.invoke_structured(
            context.db,
            task_id=context.task.id,
            step_run_id=context.step_run.id,
            profile_name=get_settings().model_profile,
            messages=messages,
            response_model=CodeGenerationResult,
            prompt_version=self.prompt_version,
        )
        if repair_evidence is not None:
            result = self.normalize_repair_result(result, repair_evidence)
        self.validate_result(result, repair_evidence=repair_evidence)
        if profile.provider != "fake" and result == baseline:
            raise ValueError("Model returned the unchanged deterministic baseline.")
        return result

    def normalize_repair_result(
        self,
        result: CodeGenerationResult,
        repair_evidence: RepairEvidencePack,
    ) -> CodeGenerationResult:
        if len(result.patch.files) == 1:
            result.patch.files[0].expected_sha256 = repair_evidence.previous_sha256
        return result

    def _baseline_result(
        self,
        *,
        task_id: str,
        request: str,
        repair_count: int,
        previous_content: str | None,
        validation_errors: list[str],
        review_findings: list[dict],
    ) -> CodeGenerationResult:
        safe_id = task_id.replace("-", "_")
        enabled = True
        content = (
            '"""Generated candidate; isolated in TaskWorkspace until approval."""\n\n'
            f'FEATURE_ID = "{task_id}"\n'
            f'REQUEST = {request!r}\n\n'
            f"def build_{safe_id}() -> dict[str, object]:\n"
            f'    return {{"feature_id": FEATURE_ID, "request": REQUEST, "enabled": {enabled!r}}}\n'
        )
        path = f"generated/{task_id}/game_feature.py"
        expected_hash = (
            hashlib.sha256(previous_content.encode("utf-8")).hexdigest()
            if previous_content is not None
            else None
        )
        patch = CodePatch(
            files=[PatchFile(path=path, content=content, expected_sha256=expected_hash)],
            explanation=(
                "Create an isolated Python feature builder."
                if repair_count == 0
                else (
                    f"Repair attempt {repair_count}; validation errors={validation_errors}; "
                    f"review findings={len(review_findings)}."
                )
            ),
        )
        self.validate_limits(patch)
        return CodeGenerationResult(
            patch=patch,
            affected_files=[path],
            summary=patch.explanation,
        )

    def to_candidate(self, result: CodeGenerationResult) -> CandidateSpec:
        file = result.patch.files[0]
        return CandidateSpec(
            kind="python_patch",
            relative_path=file.path,
            content=file.content,
            summary=result.summary,
        )

    def validate_result(
        self,
        result: CodeGenerationResult,
        *,
        repair_evidence: RepairEvidencePack | None = None,
    ) -> None:
        self.validate_limits(result.patch)
        if len(result.patch.files) != 1:
            raise ValueError("Workflow version 1 requires exactly one generated code file.")
        patch_paths = [file.path for file in result.patch.files]
        if result.affected_files != patch_paths:
            raise ValueError("affected_files must exactly match patch file paths.")
        for file in result.patch.files:
            path = Path(file.path)
            if path.is_absolute() or ".." in path.parts:
                raise ValueError("Generated code path must remain relative.")
            if path.suffix != ".py":
                raise ValueError("Workflow version 1 only accepts Python candidates.")
        if repair_evidence is not None:
            file = result.patch.files[0]
            if file.path != repair_evidence.previous_path:
                raise ValueError("Repair must keep the previous candidate path.")
            if file.content == repair_evidence.previous_content:
                raise ValueError("Repair must change the previous candidate content.")
            if file.expected_sha256 != repair_evidence.previous_sha256:
                raise ValueError("Repair must carry the previous candidate SHA-256.")

    def validate_limits(self, patch: CodePatch) -> None:
        if len(patch.files) > self.max_files:
            raise ValueError(f"Patch exceeds maximum file count: {self.max_files}.")
        changed_lines = sum(len(file.content.splitlines()) for file in patch.files)
        if changed_lines > self.max_changed_lines:
            raise ValueError(f"Patch exceeds maximum changed lines: {self.max_changed_lines}.")

    def _build_project_context(self, relevant_paths: list[str]) -> dict[str, object]:
        root = get_settings().project_root.resolve()
        files: list[dict[str, str]] = []
        total_chars = 0
        for relative in relevant_paths[:8]:
            path = (root / relative).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                continue
            try:
                content = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            remaining = 24000 - total_chars
            if remaining <= 0:
                break
            excerpt = content[: min(6000, remaining)]
            files.append({"path": relative, "content": excerpt})
            total_chars += len(excerpt)
        return {
            "project_root_name": root.name,
            "relevant_files": files,
            "constraints": {
                "max_files": 1,
                "max_changed_lines": self.max_changed_lines,
                "source_project_modified": False,
            },
        }


def _load_prompt() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "prompts"
        / "code_generator"
        / "v1.md"
    ).read_text(encoding="utf-8")
