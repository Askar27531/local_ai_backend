from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvaluationSuite = Literal["planning", "config", "patch", "review", "all"]


class EvaluationRunRequest(BaseModel):
    suite: EvaluationSuite = "all"
    dataset_version: str = Field(default="v1", min_length=1, max_length=80)


class EvaluationResultResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    evaluation_run_id: str | None
    task_id: str
    step_run_id: str | None
    evaluator: str
    metric_name: str
    score: float | None
    verdict: str
    details: dict[str, Any]
    created_at: datetime


class EvaluationRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    suite: str
    dataset_version: str
    dataset_sha256: str
    app_version: str
    status: str
    total_cases: int
    passed_cases: int
    failed_cases: int
    summary: dict[str, Any]
    started_at: datetime
    finished_at: datetime | None


class EvaluationRunDetail(EvaluationRunResponse):
    results: list[EvaluationResultResponse] = Field(default_factory=list)


class EvaluationAggregate(BaseModel):
    group_by: str
    groups: dict[str, dict[str, float | int | None]]
