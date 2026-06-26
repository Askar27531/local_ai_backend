from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skill_app.domain.status import RunStatus


class WorkflowRunCreate(BaseModel):
    workflow_name: str = Field(min_length=1, max_length=120)
    graph_version: str = Field(default="1", min_length=1, max_length=40)


class WorkflowStartRequest(WorkflowRunCreate):
    pass


class WorkflowResumeRequest(BaseModel):
    resume_value: dict[str, Any] = Field(default_factory=dict)


class WorkflowCancelRequest(BaseModel):
    actor_name: str = Field(default="local-user", min_length=1, max_length=120)


class WorkflowInfo(BaseModel):
    name: str
    version: str
    description: str


class WorkflowRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    workflow_name: str
    graph_version: str
    status: RunStatus
    state_snapshot: dict[str, Any]
    started_at: datetime | None
    finished_at: datetime | None
