from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ArtifactTextCreate(BaseModel):
    step_run_id: str | None = None
    artifact_type: str = Field(min_length=1, max_length=80)
    logical_name: str = Field(min_length=1, max_length=240)
    content: str
    mime_type: str = Field(default="text/plain; charset=utf-8", max_length=120)
    metadata: dict[str, Any] = Field(default_factory=dict)
    validation_status: str = Field(default="pending", max_length=40)
    approval_status: str = Field(default="not_required", max_length=40)


class ArtifactResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    step_run_id: str | None
    artifact_type: str
    logical_name: str
    storage_uri: str
    mime_type: str
    sha256: str
    size_bytes: int
    version: int
    artifact_metadata: dict[str, Any]
    validation_status: str
    approval_status: str
    created_at: datetime
