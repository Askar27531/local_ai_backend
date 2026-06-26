from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class WorkspaceCreate(BaseModel):
    source_root: str = Field(default=".", min_length=1, max_length=1000)
    strategy: Literal["copy_subset"] = "copy_subset"
    base_revision: str | None = Field(default=None, max_length=120)


class WorkspaceCopyRequest(BaseModel):
    paths: list[str] = Field(min_length=1, max_length=500)


class WorkspaceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    source_root: str
    workspace_root: str
    strategy: str
    base_revision: str | None
    status: str
    copied_paths: list[str]
    created_at: datetime
    cleaned_at: datetime | None


class WorkspaceDiffResponse(BaseModel):
    workspace_id: str
    changed: bool
    changed_paths: list[str]
    diff: str
    artifact_id: str | None = None


class WorkspaceCleanupResponse(BaseModel):
    workspace_id: str
    status: str
    cleaned_at: datetime | None
