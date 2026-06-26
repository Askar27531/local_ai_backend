from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skill_app.schemas.code_generation import CodePatch
from skill_app.schemas.review import ReviewFinding
from skill_app.schemas.test_report import GeneratedTestSuite


class NpcAppPatchResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: CodePatch
    summary: str
    affected_files: list[str] = Field(min_length=1, max_length=5)


class NpcAppReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)


class NpcAppDeliveryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    workflow_run_id: str
    workspace_id: str
    candidate_artifact_ids: list[str]
    patch_manifest_artifact_ids: list[str]
    test_plan_artifact_ids: list[str]
    test_patch_artifact_ids: list[str]
    test_report_artifact_ids: list[str]
    review_report_artifact_ids: list[str]
    diff_artifact_id: str
    repair_count: int
    source_project_modified: bool = False
    applied_paths: list[str] = Field(default_factory=list)


class GameTextCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str = Field(min_length=1, max_length=12000)
    summary: str
    source_paths: list[str] = Field(default_factory=list, max_length=8)
    affected_npcs: list[str] = Field(default_factory=list)
    intended_unlock_level: int = Field(default=0, ge=0, le=9)


class GameTextCandidateSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidates: list[GameTextCandidate] = Field(min_length=1, max_length=10)
    summary: str


class GameTextValidationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    valid: bool
    checks: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class GameTextReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)


class GameTextDeliveryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    task_id: str
    workflow_run_id: str
    workspace_id: str
    candidate_artifact_ids: list[str]
    validation_report_artifact_ids: list[str]
    review_report_artifact_ids: list[str]
    diff_artifact_id: str
    final_candidate_artifact_id: str
    final_candidate_artifact_ids: list[str] = Field(default_factory=list)
    repair_count: int
    source_project_modified: bool = False


class NpcAppTestSuite(GeneratedTestSuite):
    pass
