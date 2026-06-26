from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from skill_app.db.database import Base


def now() -> datetime:
    return datetime.now()


class AgentTask(Base):
    __tablename__ = "agent_tasks"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_id: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    title: Mapped[str] = mapped_column(String(240), nullable=False)
    request: Mapped[str] = mapped_column(Text, nullable=False)
    task_type: Mapped[str] = mapped_column(String(80), default="general", nullable=False)
    workflow_name: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="draft", index=True, nullable=False)
    risk_level: Mapped[str] = mapped_column(String(20), default="low", nullable=False)
    created_by: Mapped[str] = mapped_column(String(120), default="local-user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=now, onupdate=now, nullable=False)


class WorkflowRun(Base):
    __tablename__ = "workflow_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    workflow_name: Mapped[str] = mapped_column(String(120), nullable=False)
    graph_version: Mapped[str] = mapped_column(String(40), default="1", nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)
    state_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class SkillRun(Base):
    __tablename__ = "skill_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    workflow_run_id: Mapped[str] = mapped_column(
        ForeignKey("workflow_runs.id"),
        index=True,
        unique=True,
        nullable=False,
    )
    skill_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    skill_version: Mapped[str] = mapped_column(String(40), nullable=False)
    package_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    instruction_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    manifest_snapshot: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False)
    instruction_snapshots: Mapped[dict[str, str]] = mapped_column(JSON, default=dict, nullable=False)
    component_instruction_sha256s: Mapped[dict[str, str]] = mapped_column(
        JSON,
        default=dict,
        nullable=False,
    )
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskWorkspace(Base):
    __tablename__ = "task_workspaces"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    source_root: Mapped[str] = mapped_column(Text, nullable=False)
    workspace_root: Mapped[str] = mapped_column(Text, nullable=False, unique=True)
    strategy: Mapped[str] = mapped_column(String(40), default="copy_subset", nullable=False)
    base_revision: Mapped[str | None] = mapped_column(String(120), nullable=True)
    status: Mapped[str] = mapped_column(String(40), default="active", index=True, nullable=False)
    copied_paths: Mapped[list[str]] = mapped_column(JSON, default=list, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    cleaned_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class ModelCall(Base):
    __tablename__ = "model_calls"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    step_run_id: Mapped[str | None] = mapped_column(ForeignKey("step_runs.id"), index=True, nullable=True)
    profile_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    provider: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(160), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(80), nullable=False)
    input_chars: Mapped[int] = mapped_column(Integer, nullable=False)
    output_chars: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[float] = mapped_column(Float, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    raw_output_artifact_id: Mapped[str | None] = mapped_column(
        ForeignKey("artifacts.id"),
        index=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class StepRun(Base):
    __tablename__ = "step_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    workflow_run_id: Mapped[str] = mapped_column(ForeignKey("workflow_runs.id"), index=True, nullable=False)
    step_key: Mapped[str] = mapped_column(String(120), nullable=False)
    agent_name: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)
    input_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    output_data: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_type: Mapped[str | None] = mapped_column(String(120), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class Artifact(Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        UniqueConstraint("task_id", "logical_name", "version", name="uq_artifact_task_name_version"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    step_run_id: Mapped[str | None] = mapped_column(ForeignKey("step_runs.id"), index=True, nullable=True)
    artifact_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    logical_name: Mapped[str] = mapped_column(String(240), nullable=False)
    storage_uri: Mapped[str] = mapped_column(Text, nullable=False)
    mime_type: Mapped[str] = mapped_column(String(120), default="application/octet-stream", nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    artifact_metadata: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    validation_status: Mapped[str] = mapped_column(String(40), default="pending", nullable=False)
    approval_status: Mapped[str] = mapped_column(String(40), default="not_required", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class ApprovalRequest(Base):
    __tablename__ = "approval_requests"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    artifact_id: Mapped[str | None] = mapped_column(ForeignKey("artifacts.id"), index=True, nullable=True)
    approval_type: Mapped[str] = mapped_column(String(80), nullable=False)
    status: Mapped[str] = mapped_column(String(40), default="pending", index=True, nullable=False)
    risk_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    diff_summary: Mapped[str] = mapped_column(Text, default="", nullable=False)
    requested_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(120), nullable=True)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)


class EvaluationResult(Base):
    __tablename__ = "evaluation_results"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    evaluation_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("evaluation_runs.id"),
        index=True,
        nullable=True,
    )
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    step_run_id: Mapped[str | None] = mapped_column(ForeignKey("step_runs.id"), index=True, nullable=True)
    evaluator: Mapped[str] = mapped_column(String(120), nullable=False)
    metric_name: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    verdict: Mapped[str] = mapped_column(String(40), nullable=False)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)


class EvaluationRun(Base):
    __tablename__ = "evaluation_runs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    suite: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    dataset_version: Mapped[str] = mapped_column(String(80), nullable=False)
    dataset_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    app_version: Mapped[str] = mapped_column(String(40), nullable=False)
    status: Mapped[str] = mapped_column(String(40), index=True, nullable=False)
    total_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    passed_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    failed_cases: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaskEvent(Base):
    __tablename__ = "task_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[str] = mapped_column(ForeignKey("agent_tasks.id"), index=True, nullable=False)
    workflow_run_id: Mapped[str | None] = mapped_column(ForeignKey("workflow_runs.id"), index=True, nullable=True)
    step_run_id: Mapped[str | None] = mapped_column(ForeignKey("step_runs.id"), index=True, nullable=True)
    event_type: Mapped[str] = mapped_column(String(80), index=True, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(40), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now, index=True, nullable=False)
