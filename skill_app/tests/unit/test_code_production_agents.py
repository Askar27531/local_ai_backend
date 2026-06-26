from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from skill_app.agents.code_generator import CodeGeneratorAgent
from skill_app.agents.review_agent import ReviewAgent
from skill_app.agents.test_agent import TestAgent
from skill_app.domain.errors import ToolExecutionError
from skill_app.schemas.code_generation import (
    CodeGenerationResult,
    CodePatch,
    PatchFile,
    RepairEvidencePack,
)
from skill_app.schemas.game_feature import CandidateSpec
from skill_app.schemas.review import CodeReviewReport, ReviewFinding
from skill_app.schemas.test_report import GeneratedTestPlan, GeneratedTestSuite
from skill_app.schemas.test_report import TestExecutionReport as ExecutionReport
from skill_app.schemas.tool import ToolContext
from skill_app.tools.inputs import ApplyCodePatchInput, PatchFileInput
from skill_app.tools.patch import apply_code_patch


def context(tmp_path: Path) -> ToolContext:
    return ToolContext(
        task_id="task",
        workflow_run_id="run",
        step_run_id="step",
        agent_name="code_generator",
        project_root=str(tmp_path),
        workspace_root=str(tmp_path),
        permissions=["apply_code_patch"],
        timeout_seconds=10,
    )


def test_code_generator_enforces_file_and_line_limits():
    generator = CodeGeneratorAgent()
    too_many = CodePatch.model_construct(
        files=[PatchFile(path=f"{index}.py", content="x = 1\n") for index in range(6)],
        explanation="too many",
    )
    with pytest.raises(ValueError, match="file count"):
        generator.validate_limits(too_many)

    too_large = CodePatch(
        files=[PatchFile(path="large.py", content="\n".join("x = 1" for _ in range(401)))],
        explanation="too large",
    )
    with pytest.raises(ValueError, match="changed lines"):
        generator.validate_limits(too_large)


def test_code_generator_requires_targeted_hash_locked_repair():
    previous = "VALUE = 1\n"
    evidence = RepairEvidencePack(
        previous_path="generated/demo.py",
        previous_content=previous,
        previous_sha256=hashlib.sha256(previous.encode("utf-8")).hexdigest(),
        review_findings=[{"category": "correctness", "blocking": True}],
    )
    generator = CodeGeneratorAgent()
    wrong_path = CodeGenerationResult(
        patch=CodePatch(
            files=[
                PatchFile(
                    path="generated/other.py",
                    content="VALUE = 2\n",
                    expected_sha256=evidence.previous_sha256,
                )
            ],
            explanation="repair",
        ),
        affected_files=["generated/other.py"],
        summary="repair",
    )
    with pytest.raises(ValueError, match="previous candidate path"):
        generator.validate_result(wrong_path, repair_evidence=evidence)

    unchanged = CodeGenerationResult(
        patch=CodePatch(
            files=[
                PatchFile(
                    path=evidence.previous_path,
                    content=previous,
                    expected_sha256=evidence.previous_sha256,
                )
            ],
            explanation="repair",
        ),
        affected_files=[evidence.previous_path],
        summary="repair",
    )
    with pytest.raises(ValueError, match="change the previous"):
        generator.validate_result(unchanged, repair_evidence=evidence)

    missing_hash = unchanged.model_copy(deep=True)
    missing_hash.patch.files[0].content = "VALUE = 2\n"
    missing_hash.patch.files[0].expected_sha256 = None
    normalized = generator.normalize_repair_result(missing_hash, evidence)
    generator.validate_result(normalized, repair_evidence=evidence)
    assert normalized.patch.files[0].expected_sha256 == evidence.previous_sha256


def test_hash_locked_patch_rejects_stale_file_and_never_writes_outside_workspace(tmp_path):
    target = tmp_path / "demo.py"
    target.write_text("old\n", encoding="utf-8")
    stale = hashlib.sha256(b"different").hexdigest()
    with pytest.raises(ToolExecutionError, match="hash changed"):
        apply_code_patch(
            context(tmp_path),
            ApplyCodePatchInput(
                files=[
                    PatchFileInput(
                        path="demo.py",
                        content="new\n",
                        expected_sha256=stale,
                    )
                ]
            ),
        )
    assert target.read_text(encoding="utf-8") == "old\n"


def test_code_patch_writes_bounded_files_inside_workspace(tmp_path):
    result = apply_code_patch(
        context(tmp_path),
        ApplyCodePatchInput(
            files=[
                PatchFileInput(path="src/demo.py", content="VALUE = 1\n"),
                PatchFileInput(path="tests/test_demo.py", content="def test_value():\n    assert True\n"),
            ]
        ),
    )
    assert result.ok
    assert result.output["affected_files"] == ["src/demo.py", "tests/test_demo.py"]
    assert (tmp_path / "src" / "demo.py").is_file()


def test_review_findings_include_evidence_location_and_block_critical_issue():
    candidate = CandidateSpec(
        kind="python_patch",
        relative_path="generated/demo.py",
        content="def build_demo():\n    return eval('1 + 1')\n",
        summary="unsafe",
    )
    report = ReviewAgent().review(
        candidate=candidate,
        diff="+ return eval('1 + 1')",
        test_report=ExecutionReport(
            passed=True,
            compile_passed=True,
            pytest_passed=True,
            candidate_path=candidate.relative_path,
            test_path="generated/test_demo.py",
        ),
        acceptance_criteria=["No dynamic execution."],
    )
    assert not report.approved
    finding = report.findings[0]
    assert finding.severity == "critical"
    assert finding.line == 2
    assert finding.evidence == "generated/demo.py:2"


def test_review_agent_rejects_invalid_model_evidence():
    candidate = CandidateSpec(
        kind="python_patch",
        relative_path="generated/demo.py",
        content="VALUE = 1\n",
        summary="demo",
    )
    report = CodeReviewReport(
        approved=False,
        summary="bad evidence",
        findings=[
            ReviewFinding(
                severity="high",
                category="correctness",
                file=candidate.relative_path,
                line=99,
                title="Invented line",
                evidence=f"{candidate.relative_path}:99",
                suggestion="Fix it.",
                blocking=True,
            )
        ],
        reviewed_files=[candidate.relative_path],
        acceptance_criteria_checked=["Return one."],
    )

    with pytest.raises(ValueError, match="outside the candidate"):
        ReviewAgent().normalize_model_findings(report.findings, candidate)


def test_review_agent_normalizes_evidence_and_blocking_from_severity():
    candidate = CandidateSpec(
        kind="python_patch",
        relative_path="generated/demo.py",
        content="VALUE = 1\n",
        summary="demo",
    )
    finding = ReviewFinding(
        severity="high",
        category="correctness",
        file=candidate.relative_path,
        line=1,
        title="Wrong value",
        evidence="The value violates the requirement.",
        suggestion="Return the expected value.",
        blocking=False,
    )

    normalized = ReviewAgent().normalize_model_findings([finding], candidate)[0]

    assert normalized.blocking is True
    assert normalized.evidence.startswith("generated/demo.py:1")


def test_review_agent_merge_cannot_remove_deterministic_blocker():
    candidate = CandidateSpec(
        kind="python_patch",
        relative_path="generated/demo.py",
        content="def build_demo():\n    return eval('1 + 1')\n",
        summary="unsafe",
    )
    agent = ReviewAgent()
    deterministic = agent.review(
        candidate=candidate,
        diff="+ return eval('1 + 1')",
        test_report=ExecutionReport(
            passed=True,
            compile_passed=True,
            pytest_passed=True,
            candidate_path=candidate.relative_path,
            test_path="generated/test_demo.py",
        ),
        acceptance_criteria=["No dynamic execution."],
    )
    merged = agent._merge_findings(deterministic.findings, [])

    assert any(finding.category == "security" and finding.blocking for finding in merged)


def test_test_agent_rejects_unsafe_generated_tests():
    candidate = CandidateSpec(
        kind="python_patch",
        relative_path="generated/demo.py",
        content="def build_demo():\n    return {'feature_id': 'demo', 'enabled': True}\n",
        summary="demo",
    )
    suite = GeneratedTestSuite(
        plan=GeneratedTestPlan(
            candidate_path=candidate.relative_path,
            test_path="generated/test_demo.py",
            cases=["run a shell command"],
        ),
        test_content=(
            "import subprocess\n\n"
            "def test_unsafe():\n"
            "    subprocess.run(['whoami'])\n"
        ),
    )

    with pytest.raises(ValueError, match="forbidden imports"):
        TestAgent().validate_suite(suite, candidate, Path("generated/test_demo.py"))


def test_test_agent_requires_managed_path_and_real_test_function():
    candidate = CandidateSpec(
        kind="python_patch",
        relative_path="generated/demo.py",
        content="VALUE = 1\n",
        summary="demo",
    )
    agent = TestAgent()
    wrong_path = GeneratedTestSuite(
        plan=GeneratedTestPlan(
            candidate_path=candidate.relative_path,
            test_path="../test_demo.py",
            cases=["check value"],
        ),
        test_content="def test_value():\n    assert True\n",
    )
    with pytest.raises(ValueError, match="managed test path"):
        agent.validate_suite(wrong_path, candidate, Path("generated/test_demo.py"))

    no_test = GeneratedTestSuite(
        plan=GeneratedTestPlan(
            candidate_path=candidate.relative_path,
            test_path="generated/test_demo.py",
            cases=["check value"],
        ),
        test_content="def helper():\n    return True\n",
    )
    with pytest.raises(ValueError, match="at least one test function"):
        agent.validate_suite(no_test, candidate, Path("generated/test_demo.py"))
