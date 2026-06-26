from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class ToolDefinition(BaseModel):
    name: str
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    output_schema: dict[str, Any] = Field(default_factory=dict)
    permission: str = "read"
    timeout_seconds: int = Field(default=60, ge=1)
    idempotent: bool = True
    side_effect_level: str = "none"
    approval_required: bool = False


class ToolContext(BaseModel):
    task_id: str
    workflow_run_id: str
    step_run_id: str
    agent_name: str
    project_root: str
    workspace_root: str
    permissions: list[str] = Field(default_factory=list)
    timeout_seconds: int = Field(default=60, ge=1)
    skill_run_id: str | None = None
    skill_name: str | None = None
    skill_version: str | None = None


class ToolResult(BaseModel):
    ok: bool
    output: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    artifacts: list[str] = Field(default_factory=list)
    metrics: dict[str, Any] = Field(default_factory=dict)
    error_type: str | None = None


class ToolExecuteRequest(BaseModel):
    workflow_run_id: str
    step_run_id: str
    agent_name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ToolExecutionResponse(BaseModel):
    tool_name: str
    result: ToolResult
