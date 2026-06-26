from __future__ import annotations

import json
from pathlib import Path

from skill_app.agents.base import AgentContext
from skill_app.config import get_settings
from skill_app.schemas.game_feature import CandidateSpec
from skill_app.schemas.model import ModelMessage
from skill_app.schemas.review import CodeReviewReport, ReviewFinding, SemanticReviewResult
from skill_app.schemas.test_report import TestExecutionReport
from skill_app.services import model_gateway
from skill_app.services.skill_loader import load_skill


class ReviewAgent:
    name = "review_agent"
    skill_name = "code-reviewer"
    prompt_version = "code-reviewer-v1"

    def review(
        self,
        *,
        candidate: CandidateSpec,
        diff: str,
        test_report: TestExecutionReport,
        acceptance_criteria: list[str],
        config_consistent: bool = True,
        test_report_artifact_ids: list[str] | None = None,
        agent_context: AgentContext | None = None,
        request: str = "",
        generated_test_plan: dict | None = None,
        generated_test_content: str = "",
    ) -> CodeReviewReport:
        deterministic = self._deterministic_review(
            candidate=candidate,
            diff=diff,
            test_report=test_report,
            acceptance_criteria=acceptance_criteria,
            config_consistent=config_consistent,
            test_report_artifact_ids=test_report_artifact_ids,
        )
        if agent_context is None:
            return deterministic
        skill = load_skill(self.skill_name)
        profile = model_gateway.get_profile(get_settings().model_profile)
        user_content = SemanticReviewResult(
            summary=deterministic.summary,
            findings=deterministic.findings,
        ).model_dump_json()
        if profile.provider != "fake":
            user_content = json.dumps(
                {
                    "request": request,
                    "acceptance_criteria": acceptance_criteria,
                    "candidate": candidate.model_dump(mode="json"),
                    "workspace_diff": diff,
                    "test_plan": generated_test_plan or {},
                    "generated_test_content": generated_test_content,
                    "test_report": test_report.model_dump(mode="json"),
                    "config_consistent": config_consistent,
                    "deterministic_findings": [
                        finding.model_dump(mode="json")
                        for finding in deterministic.findings
                    ],
                    "instruction": (
                        "Perform an independent semantic review. "
                        "Do not remove or weaken deterministic findings."
                    ),
                },
                ensure_ascii=False,
            )
        model_report = model_gateway.invoke_structured(
            agent_context.db,
            task_id=agent_context.task.id,
            step_run_id=agent_context.step_run.id,
            profile_name=get_settings().model_profile,
            messages=[
                ModelMessage(
                    role="system",
                    content=f"{_load_prompt()}\n\nReview skill:\n{skill.body}",
                ),
                ModelMessage(
                    role="user",
                    content=user_content,
                ),
            ],
            response_model=SemanticReviewResult,
            prompt_version=self.prompt_version,
        )
        model_findings = self.normalize_model_findings(model_report.findings, candidate)
        findings = self._merge_findings(deterministic.findings, model_findings)
        return CodeReviewReport(
            approved=not any(finding.blocking for finding in findings),
            summary=(
                "Code review passed."
                if not findings
                else "Code review found blocking issues."
                if any(finding.blocking for finding in findings)
                else "Code review passed with non-blocking findings."
            ),
            findings=findings,
            reviewed_files=[candidate.relative_path],
            acceptance_criteria_checked=acceptance_criteria,
            test_report_artifact_ids=test_report_artifact_ids or [],
        )

    def _deterministic_review(
        self,
        *,
        candidate: CandidateSpec,
        diff: str,
        test_report: TestExecutionReport,
        acceptance_criteria: list[str],
        config_consistent: bool,
        test_report_artifact_ids: list[str] | None,
    ) -> CodeReviewReport:
        findings: list[ReviewFinding] = []
        if not test_report.compile_passed:
            findings.append(self._finding(candidate, 1, "critical", "compile", "Compilation failed"))
        if not test_report.pytest_passed:
            findings.append(self._finding(candidate, 1, "critical", "test", "Generated tests failed"))
        if not config_consistent:
            findings.append(
                self._finding(candidate, 1, "high", "config_consistency", "Code conflicts with config")
            )
        for line_number, line in enumerate(candidate.content.splitlines(), start=1):
            if "TODO" in line:
                findings.append(
                    self._finding(candidate, line_number, "medium", "completeness", "Unresolved TODO")
                )
            if "eval(" in line or "exec(" in line:
                findings.append(
                    self._finding(candidate, line_number, "critical", "security", "Dynamic execution")
                )
        if not diff.strip():
            findings.append(self._finding(candidate, 1, "high", "diff", "No code change detected"))
        return CodeReviewReport(
            approved=not any(finding.blocking for finding in findings),
            summary="Code review passed." if not findings else "Code review found issues.",
            findings=findings,
            reviewed_files=[candidate.relative_path],
            acceptance_criteria_checked=acceptance_criteria,
            test_report_artifact_ids=test_report_artifact_ids or [],
        )

    def normalize_model_findings(
        self,
        findings: list[ReviewFinding],
        candidate: CandidateSpec,
    ) -> list[ReviewFinding]:
        line_count = max(1, len(candidate.content.splitlines()))
        normalized: list[ReviewFinding] = []
        for finding in findings:
            if finding.file != candidate.relative_path:
                raise ValueError("Review finding references a non-candidate file.")
            expected_blocking = finding.severity in {"critical", "high", "medium"}
            if expected_blocking and finding.line is None:
                raise ValueError("Blocking review finding must include a line number.")
            if finding.line is not None and finding.line > line_count:
                raise ValueError("Review finding line is outside the candidate.")
            evidence = finding.evidence
            if finding.line is not None and f"{finding.file}:{finding.line}" not in evidence:
                evidence = f"{finding.file}:{finding.line} — {evidence}"
            normalized.append(
                finding.model_copy(
                    update={
                        "blocking": expected_blocking,
                        "evidence": evidence,
                    }
                )
            )
        return normalized

    def _merge_findings(
        self,
        deterministic: list[ReviewFinding],
        model_findings: list[ReviewFinding],
    ) -> list[ReviewFinding]:
        merged: list[ReviewFinding] = []
        seen: set[tuple[str, int | None, str, str]] = set()
        for finding in [*deterministic, *model_findings]:
            key = (finding.file, finding.line, finding.category, finding.title.casefold())
            if key not in seen:
                seen.add(key)
                merged.append(finding)
        return merged

    def _finding(
        self,
        candidate: CandidateSpec,
        line: int,
        severity: str,
        category: str,
        title: str,
    ) -> ReviewFinding:
        return ReviewFinding(
            severity=severity,
            category=category,
            file=candidate.relative_path,
            line=line,
            title=title,
            evidence=f"{candidate.relative_path}:{line}",
            suggestion="Repair the candidate and rerun tests and review.",
            blocking=severity in {"critical", "high", "medium"},
        )


def _load_prompt() -> str:
    return (
        Path(__file__).resolve().parents[1]
        / "prompts"
        / "code_reviewer"
        / "v1.md"
    ).read_text(encoding="utf-8")
