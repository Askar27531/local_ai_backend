from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from skill_app.domain.status import RiskLevel, TaskStatus


class TaskCreate(BaseModel):
    project_id: str = Field(min_length=1, max_length=120)
    title: str = Field(min_length=1, max_length=240)
    request: str = Field(min_length=1)
    task_type: str = Field(default="general", min_length=1, max_length=80)
    workflow_name: str | None = Field(default=None, max_length=120)
    risk_level: RiskLevel = RiskLevel.LOW
    created_by: str = Field(default="local-user", min_length=1, max_length=120)


class TaskCancelRequest(BaseModel):
    actor_name: str = Field(default="local-user", min_length=1, max_length=120)


class TaskResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    project_id: str
    title: str
    request: str
    task_type: str
    workflow_name: str | None
    status: TaskStatus
    risk_level: RiskLevel
    created_by: str
    created_at: datetime
    updated_at: datetime
