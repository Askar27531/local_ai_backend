from __future__ import annotations

from typing import Any, TypedDict


class ProductionWorkflowState(TypedDict, total=False):
    task_id: str
    workflow_run_id: str
    request: str
    project_id: str
    current_step: str
    artifact_ids: list[str]
    plan: dict[str, Any] | None
    plan_json_artifact_id: str | None
    plan_markdown_artifact_id: str | None
    planned_step_ids: list[str]
    workspace_id: str | None
    candidate: dict[str, Any] | None
    candidate_artifact_ids: list[str]
    code_patch_artifact_ids: list[str]
    code_explanation_artifact_ids: list[str]
    test_plan_artifact_ids: list[str]
    test_patch_artifact_ids: list[str]
    current_candidate_artifact_id: str | None
    validation: dict[str, Any] | None
    validation_artifact_ids: list[str]
    config_artifact_ids: list[str]
    config_explanation_artifact_id: str | None
    normalized_config_artifact_id: str | None
    config_diff_artifact_id: str | None
    review: dict[str, Any] | None
    review_artifact_ids: list[str]
    repair_count: int
    repair_summaries: list[str]
    delivery_manifest_artifact_id: str | None
    approval_id: str | None
    approval_decision: dict[str, Any] | None
    final_summary: str
    error: dict[str, Any] | None
    npc_app_patch: dict[str, Any] | None
    npc_app_test_suite: dict[str, Any] | None
    npc_app_test_report: dict[str, Any] | None
    npc_app_review: dict[str, Any] | None
    game_text_candidate: dict[str, Any] | None
    game_text_validation: dict[str, Any] | None
    game_text_review: dict[str, Any] | None
    current_candidate_artifact_ids: list[str]
    visual_brief: dict[str, Any] | None
    visual_brief_artifact_id: str | None
    validation_report_artifact_id: str | None
    contact_sheet_artifact_id: str | None
    selected_artifact_id: str | None
    skill_run_id: str | None
    skill_runtime: dict[str, Any] | None
