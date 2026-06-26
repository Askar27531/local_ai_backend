from __future__ import annotations

from skill_app.agents.review_agent import ReviewAgent
from skill_app.schemas.game_feature import CandidateSpec
from skill_app.schemas.test_report import TestExecutionReport


def run_case(case: dict) -> dict:
    candidate = CandidateSpec(
        kind="python_patch",
        relative_path=f"evaluation/{case['id']}.py",
        content=case["content"],
        summary="evaluation candidate",
    )
    report = ReviewAgent().review(
        candidate=candidate,
        diff="+ candidate",
        test_report=TestExecutionReport(
            passed=True,
            compile_passed=True,
            pytest_passed=True,
            candidate_path=candidate.relative_path,
            test_path=f"evaluation/test_{case['id']}.py",
        ),
        acceptance_criteria=["No known blocking defects."],
    )
    expected = set(case["expected_categories"])
    detected = {finding.category for finding in report.findings}
    true_positive = len(expected & detected)
    recall = true_positive / len(expected) if expected else 1.0
    false_positive = len(detected - expected) / len(detected) if detected else 0.0
    blocking_correct = (not report.approved) == case["expected_blocking"]
    passed = recall == 1 and false_positive == 0 and blocking_correct
    return {
        "case_id": case["id"],
        "passed": passed,
        "metrics": {
            "known_defect_recall": recall,
            "blocking_accuracy": float(blocking_correct),
            "false_positive_rate": false_positive,
        },
        "details": {
            "expected_categories": sorted(expected),
            "detected_categories": sorted(detected),
            "findings": [finding.model_dump(mode="json") for finding in report.findings],
        },
    }
