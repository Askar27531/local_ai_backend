from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from skill_app.domain.status import ApprovalStatus


class ApprovalCreate(BaseModel):
    artifact_id: str | None = None
    approval_type: str = Field(min_length=1, max_length=80)
    risk_summary: str = ""
    diff_summary: str = ""


class ApprovalDecision(BaseModel):
    approved: bool
    resolved_by: str = Field(min_length=1, max_length=120)
    comment: str | None = None


class ApprovalResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    artifact_id: str | None
    approval_type: str
    status: ApprovalStatus
    risk_summary: str
    diff_summary: str
    requested_at: datetime
    resolved_at: datetime | None
    resolved_by: str | None
    comment: str | None
