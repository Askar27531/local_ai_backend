from __future__ import annotations

from skill_app.evaluations.runners import config, patch, planning, review
from skill_app.evaluations.runners.common import load_jsonl


def test_every_golden_dataset_has_ten_curated_cases():
    assert len(load_jsonl("planning_cases.jsonl")) == 10
    assert len(load_jsonl("config_cases.jsonl")) == 10
    assert len(load_jsonl("patch_cases.jsonl")) == 10
    assert len(load_jsonl("code_review_cases.jsonl")) == 10


def test_planning_runner_reports_required_metrics_and_failure_details():
    case = load_jsonl("planning_cases.jsonl")[0]
    outcome = planning.run_case(case)
    assert outcome["passed"]
    assert set(outcome["metrics"]) == {
        "plan_structure_valid",
        "required_step_recall",
        "irrelevant_step_rate",
        "dependency_graph_correct",
    }

    invalid = {
        **case,
        "id": "forced_failure",
        "required_agents": ["missing_agent"],
    }
    failed = planning.run_case(invalid)
    assert not failed["passed"]
    assert failed["case_id"] == "forced_failure"
    assert "agents" in failed["details"]


def test_config_patch_and_review_runners_cover_quality_metrics():
    config_outcome = config.run_case(load_jsonl("config_cases.jsonl")[2])
    patch_outcome = patch.run_case(load_jsonl("patch_cases.jsonl")[0])
    review_outcome = review.run_case(load_jsonl("code_review_cases.jsonl")[0])

    assert config_outcome["passed"]
    assert config_outcome["details"]["repaired"]
    assert patch_outcome["passed"]
    assert patch_outcome["metrics"]["patch_apply_rate"] == 1
    assert review_outcome["passed"]
    assert review_outcome["metrics"]["known_defect_recall"] == 1
