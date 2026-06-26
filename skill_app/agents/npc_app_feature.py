from __future__ import annotations

import hashlib
import json
from pathlib import Path

from skill_app.agents.base import AgentContext
from skill_app.agents.test_agent import TestAgent
from skill_app.config import get_settings
from skill_app.schemas.code_generation import CodePatch, PatchFile
from skill_app.schemas.game_feature import CandidateSpec
from skill_app.schemas.model import ModelMessage
from skill_app.schemas.project_workflows import (
    NpcAppPatchResult,
    NpcAppReviewResult,
    NpcAppTestSuite,
)
from skill_app.schemas.review import ReviewFinding
from skill_app.schemas.skill import SkillLimits
from skill_app.schemas.test_report import GeneratedTestPlan, TestExecutionReport
from skill_app.services import model_gateway
from skill_app.services.skill_loader import load_skill
from skill_app.services.skill_registry import (
    register_builtin_skill_bindings,
    run_skill_validators,
    schema_registry,
)


class NpcAppFeatureAgent:
    prompt_version = "npc-app-feature-v1"
    max_files = 5
    max_lines = 1200

    def generate(
        self,
        context: AgentContext,
        *,
        request: str,
        repair_count: int = 0,
        previous_patch: NpcAppPatchResult | None = None,
        evidence: dict | None = None,
        limits: SkillLimits | None = None,
        output_schema_name: str = "npc_app_patch_result",
        output_validators: list[str] | None = None,
        profile_name: str | None = None,
    ) -> NpcAppPatchResult:
        baseline = self._baseline(context.task.id, request, previous_patch, repair_count)
        selected_profile = profile_name or get_settings().model_profile
        profile = model_gateway.get_profile(selected_profile)
        is_repair = previous_patch is not None
        user_content = baseline.model_dump_json()
        if profile.provider != "fake":
            shape_example = (
                _compact_patch_shape(previous_patch)
                if is_repair and previous_patch is not None
                else baseline.model_dump(mode="json")
            )
            payload = {
                "request": request,
                "repair_count": repair_count,
                "shape_example": shape_example,
            }
            if not is_repair:
                payload["project_context"] = self._context_pack(request)
            else:
                payload["repair_evidence"] = evidence or {}
            user_content = json.dumps(payload, ensure_ascii=False)
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
                        _repair_system_prompt()
                        if is_repair
                        else f"{_prompt('npc_app_feature')}\n\n{load_skill('npc-app-feature').body}"
                    ),
                ),
                ModelMessage(role="user", content=user_content),
            ],
            response_model=response_model,
            prompt_version=self.prompt_version,
        )
        if not isinstance(result, NpcAppPatchResult):
            raise ValueError("npc_app feature skill returned an incompatible output schema.")
        normalized = self.normalize_and_validate(result, previous_patch, limits=limits)
        run_skill_validators(
            output_validators or [],
            normalized,
            {"previous_patch": previous_patch},
        )
        return normalized

    def normalize_and_validate(
        self,
        result: NpcAppPatchResult,
        previous_patch: NpcAppPatchResult | None = None,
        *,
        limits: SkillLimits | None = None,
    ) -> NpcAppPatchResult:
        max_files = min(self.max_files, limits.max_files) if limits else self.max_files
        max_lines = (
            min(self.max_lines, limits.max_changed_lines)
            if limits
            else self.max_lines
        )
        if result.affected_files != [item.path for item in result.patch.files]:
            raise ValueError("affected_files must exactly match patch file paths.")
        if len(result.patch.files) > max_files:
            raise ValueError("npc_app patch exceeds maximum file count.")
        total_lines = sum(len(item.content.splitlines()) for item in result.patch.files)
        if total_lines > max_lines:
            raise ValueError("npc_app patch exceeds maximum line count.")
        root = get_settings().project_root.resolve()
        previous = {item.path: item for item in previous_patch.patch.files} if previous_patch else {}
        for item in result.patch.files:
            path = Path(item.path)
            if path.is_absolute() or ".." in path.parts or not item.path.startswith("npc_app/"):
                raise ValueError("npc_app patch path must remain under npc_app/.")
            if path.suffix != ".py":
                raise ValueError("npc_app workflow version 1 only accepts Python files.")
            source = (root / item.path).resolve()
            if item.path in previous:
                item.expected_sha256 = hashlib.sha256(
                    previous[item.path].content.encode("utf-8")
                ).hexdigest()
            elif source.is_file():
                item.expected_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
            else:
                item.expected_sha256 = None
        if previous_patch is not None:
            previous_contents = {item.path: item.content for item in previous_patch.patch.files}
            if all(previous_contents.get(item.path) == item.content for item in result.patch.files):
                raise ValueError("npc_app repair must change at least one file.")
        return result

    def _baseline(
        self,
        task_id: str,
        request: str,
        previous_patch: NpcAppPatchResult | None,
        repair_count: int,
    ) -> NpcAppPatchResult:
        if previous_patch is not None:
            files = [
                item.model_copy(
                    update={
                        "expected_sha256": hashlib.sha256(
                            item.content.encode("utf-8")
                        ).hexdigest()
                    }
                )
                for item in previous_patch.patch.files
            ]
            return NpcAppPatchResult(
                patch=CodePatch(
                    files=files,
                    explanation=f"Repair npc_app candidate attempt {repair_count}.",
                ),
                affected_files=[item.path for item in files],
                summary=f"Repair npc_app candidate attempt {repair_count}.",
            )
        path = f"npc_app/generated_feature_{task_id.replace('-', '_')}.py"
        content = (
            '"""Isolated npc_app feature candidate."""\n\n'
            f"REQUEST = {request!r}\n\n"
            "def build_npc_app_feature() -> dict[str, object]:\n"
            '    return {"feature_id": "npc_app_feature", "enabled": True, "request": REQUEST}\n'
        )
        return NpcAppPatchResult(
            patch=CodePatch(
                files=[PatchFile(path=path, content=content)],
                explanation="Create a bounded npc_app feature candidate.",
            ),
            affected_files=[path],
            summary="Create a bounded npc_app feature candidate.",
        )

    def _context_pack(self, request: str) -> dict:
        root = get_settings().project_root.resolve()
        terms = [term.casefold() for term in request.replace("/", " ").split() if len(term) > 2]
        paths = sorted((root / "npc_app").rglob("*.py"))
        scored = sorted(
            paths,
            key=lambda path: sum(term in path.as_posix().casefold() for term in terms),
            reverse=True,
        )
        files = []
        for path in scored[:6]:
            content = path.read_text(encoding="utf-8")
            files.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                    "content": content[:2500],
                }
            )
        return {"files": files, "constraints": {"root": "npc_app/", "max_files": 5}}


class NpcAppTestAgent:
    prompt_version = "npc-app-test-v1"

    def generate(
        self,
        context: AgentContext,
        *,
        request: str,
        patch: NpcAppPatchResult,
        acceptance_criteria: list[str],
    ) -> NpcAppTestSuite:
        primary = patch.patch.files[0]
        test_path = Path(primary.path).with_name(f"test_{Path(primary.path).stem}.py").as_posix()
        baseline_candidate = CandidateSpec(
            kind="python_patch",
            relative_path=primary.path,
            content=primary.content,
            summary=patch.summary,
        )
        baseline = NpcAppTestSuite(
            plan=GeneratedTestPlan(
                candidate_path=primary.path,
                test_path=test_path,
                cases=["candidate imports", "feature payload remains enabled"],
                uncovered_risks=["External Ollama, Milvus, and persistent database integration."],
            ),
            test_content=TestAgent()._test_content(baseline_candidate),
        )
        profile = model_gateway.get_profile(get_settings().model_profile)
        user_content = baseline.model_dump_json()
        if profile.provider != "fake":
            user_content = json.dumps(
                {
                    "request": request,
                    "acceptance_criteria": acceptance_criteria,
                    "files": [
                        {"path": item.path, "content": item.content}
                        for item in patch.patch.files
                    ],
                    "required_test_path": test_path,
                    "shape_example": baseline.model_dump(mode="json"),
                },
                ensure_ascii=False,
            )
        suite = model_gateway.invoke_structured(
            context.db,
            task_id=context.task.id,
            step_run_id=context.step_run.id,
            profile_name=get_settings().model_profile,
            messages=[
                ModelMessage(
                    role="system",
                    content=(
                        f"{_prompt('npc_app_test')}\n\n"
                        f"General testing skill:\n{load_skill('test-generator').body}\n\n"
                        f"npc_app implementation skill:\n{load_skill('npc-app-feature').body}"
                    ),
                ),
                ModelMessage(role="user", content=user_content),
            ],
            response_model=NpcAppTestSuite,
            prompt_version=self.prompt_version,
        )
        suite = _normalize_test_suite_paths(
            suite,
            candidate_path=primary.path,
            test_path=test_path,
        )
        TestAgent().validate_suite(suite, baseline_candidate, Path(test_path))
        return suite


class NpcAppReviewAgent:
    prompt_version = "npc-app-review-v1"

    def review(
        self,
        context: AgentContext,
        *,
        request: str,
        patch: NpcAppPatchResult,
        diff: str,
        test_suite: NpcAppTestSuite,
        test_report: TestExecutionReport,
        acceptance_criteria: list[str],
    ) -> NpcAppReviewResult:
        deterministic: list[ReviewFinding] = []
        if not test_report.compile_passed:
            deterministic.append(_finding(patch.patch.files[0].path, 1, "critical", "compile"))
        if not test_report.pytest_passed:
            deterministic.append(_finding(patch.patch.files[0].path, 1, "critical", "test"))
        for item in patch.patch.files:
            for line, text in enumerate(item.content.splitlines(), start=1):
                if "TODO" in text:
                    deterministic.append(_finding(item.path, line, "medium", "completeness"))
                if "eval(" in text or "exec(" in text:
                    deterministic.append(_finding(item.path, line, "critical", "security"))
        baseline = NpcAppReviewResult(
            summary="npc_app patch passed review." if not deterministic else "npc_app patch requires repair.",
            findings=deterministic,
        )
        profile = model_gateway.get_profile(get_settings().model_profile)
        user_content = baseline.model_dump_json()
        if profile.provider != "fake":
            user_content = json.dumps(
                {
                    "request": request,
                    "acceptance_criteria": acceptance_criteria,
                    "files": [
                        {"path": item.path, "content": item.content}
                        for item in patch.patch.files
                    ],
                    "diff": diff,
                    "test_suite": test_suite.model_dump(mode="json"),
                    "test_report": test_report.model_dump(mode="json"),
                    "deterministic_findings": [
                        item.model_dump(mode="json") for item in deterministic
                    ],
                },
                ensure_ascii=False,
            )
        model_result = model_gateway.invoke_structured(
            context.db,
            task_id=context.task.id,
            step_run_id=context.step_run.id,
            profile_name=get_settings().model_profile,
            messages=[
                ModelMessage(
                    role="system",
                    content=(
                        f"{_prompt('npc_app_review')}\n\n"
                        f"General review skill:\n{load_skill('code-reviewer').body}\n\n"
                        f"npc_app implementation skill:\n{load_skill('npc-app-feature').body}"
                    ),
                ),
                ModelMessage(role="user", content=user_content),
            ],
            response_model=NpcAppReviewResult,
            prompt_version=self.prompt_version,
        )
        findings = _normalize_findings(
            [*deterministic, *model_result.findings],
            {item.path: item.content for item in patch.patch.files},
        )
        return NpcAppReviewResult(
            summary="npc_app patch passed review." if not findings else "npc_app patch requires repair.",
            findings=findings,
        )


def _finding(path: str, line: int, severity: str, category: str) -> ReviewFinding:
    return ReviewFinding(
        severity=severity,
        category=category,
        file=path,
        line=line,
        title=f"{category.title()} issue",
        evidence=f"{path}:{line}",
        suggestion="Repair the patch and rerun validation.",
        blocking=severity in {"critical", "high", "medium"},
    )


def _normalize_test_suite_paths(
    suite: NpcAppTestSuite,
    *,
    candidate_path: str,
    test_path: str,
) -> NpcAppTestSuite:
    return suite.model_copy(
        update={
            "plan": suite.plan.model_copy(
                update={
                    "candidate_path": candidate_path,
                    "test_path": test_path,
                }
            )
        }
    )


def _compact_patch_shape(previous_patch: NpcAppPatchResult) -> dict:
    return {
        "patch": {
            "files": [
                {
                    "path": item.path,
                    "content": "# complete repaired file content here\n",
                    "expected_sha256": item.expected_sha256,
                }
                for item in previous_patch.patch.files
            ],
            "explanation": "Concise repair explanation.",
        },
        "affected_files": [item.path for item in previous_patch.patch.files],
        "summary": "Concise repair summary.",
    }


def _repair_system_prompt() -> str:
    return (
        "You are repairing a bounded npc_app Python patch. "
        "Return only a JSON object matching NpcAppPatchResult. "
        "Use only the allowed paths in repair_evidence.constraints.allowed_paths. "
        "Return complete Python file contents for every file you modify. "
        "Fix the failure described in repair_evidence.failure using the current_files content. "
        "Do not add unrelated files, TODOs, dynamic execution, network calls, databases, or source-tree writes."
    )


def _normalize_findings(
    findings: list[ReviewFinding],
    files: dict[str, str],
) -> list[ReviewFinding]:
    normalized = []
    seen = set()
    for finding in findings:
        if finding.file not in files:
            continue
        line_count = max(1, len(files[finding.file].splitlines()))
        line = min(finding.line or 1, line_count)
        blocking = finding.severity in {"critical", "high", "medium"}
        evidence = finding.evidence
        if f"{finding.file}:{line}" not in evidence:
            evidence = f"{finding.file}:{finding.line} — {evidence}"
        if f"{finding.file}:{line}" not in evidence:
            evidence = f"{finding.file}:{line} - {finding.evidence}"
        key = (finding.file, line, finding.category, finding.title.casefold())
        if key not in seen:
            seen.add(key)
            normalized.append(
                finding.model_copy(update={"blocking": blocking, "evidence": evidence, "line": line})
            )
    return normalized


def _prompt(name: str) -> str:
    return (
        Path(__file__).resolve().parents[1] / "prompts" / name / "v1.md"
    ).read_text(encoding="utf-8")
