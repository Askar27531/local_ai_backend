from __future__ import annotations

import hashlib
import mimetypes
import uuid
from functools import lru_cache
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from skill_app.config import get_settings
from skill_app.db.models import AgentTask, Artifact, StepRun, WorkflowRun
from skill_app.domain.errors import (
    ArtifactStorageError,
    ArtifactValidationError,
    EntityNotFound,
    InvalidStateTransition,
)
from skill_app.domain.status import TaskStatus
from skill_app.domain.transitions import task_status
from skill_app.services import event_service
from skill_app.storage.local import LocalArtifactStorage, safe_logical_name


@lru_cache(maxsize=1)
def get_artifact_storage() -> LocalArtifactStorage:
    return LocalArtifactStorage(get_settings().artifact_root)


def get_artifact(db: Session, artifact_id: str) -> Artifact:
    artifact = db.get(Artifact, artifact_id)
    if artifact is None:
        raise EntityNotFound(f"Artifact not found: {artifact_id}", {"artifact_id": artifact_id})
    return artifact


def list_artifacts(db: Session, task_id: str) -> list[Artifact]:
    stmt = select(Artifact).where(Artifact.task_id == task_id).order_by(Artifact.created_at.asc())
    return list(db.execute(stmt).scalars().all())


def create_text_artifact(
    db: Session,
    task: AgentTask,
    *,
    artifact_type: str,
    logical_name: str,
    content: str,
    step_run_id: str | None = None,
    mime_type: str = "text/plain; charset=utf-8",
    metadata: dict[str, Any] | None = None,
    validation_status: str = "pending",
    approval_status: str = "not_required",
) -> Artifact:
    return create_bytes_artifact(
        db,
        task,
        artifact_type=artifact_type,
        logical_name=logical_name,
        content=content.encode("utf-8"),
        step_run_id=step_run_id,
        mime_type=mime_type,
        metadata=metadata,
        validation_status=validation_status,
        approval_status=approval_status,
    )


def create_bytes_artifact(
    db: Session,
    task: AgentTask,
    *,
    artifact_type: str,
    logical_name: str,
    content: bytes,
    step_run_id: str | None = None,
    mime_type: str | None = None,
    metadata: dict[str, Any] | None = None,
    validation_status: str = "pending",
    approval_status: str = "not_required",
) -> Artifact:
    _validate_task_and_step(db, task, step_run_id, artifact_type)
    settings = get_settings()
    if len(content) > settings.max_artifact_bytes:
        raise ArtifactValidationError(
            "Artifact exceeds maximum allowed size.",
            {"size_bytes": len(content), "max_artifact_bytes": settings.max_artifact_bytes},
        )

    safe_name = safe_logical_name(logical_name)
    artifact_id = str(uuid.uuid4())
    version = _next_version(db, task.id, safe_name)
    detected_mime = mime_type or mimetypes.guess_type(safe_name)[0] or "application/octet-stream"
    storage = get_artifact_storage()
    stored = storage.put_bytes(
        task_id=task.id,
        artifact_id=artifact_id,
        logical_name=safe_name,
        content=content,
    )
    artifact = Artifact(
        id=artifact_id,
        task_id=task.id,
        step_run_id=step_run_id,
        artifact_type=artifact_type,
        logical_name=safe_name,
        storage_uri=stored.storage_uri,
        mime_type=detected_mime,
        sha256=stored.sha256,
        size_bytes=stored.size_bytes,
        version=version,
        artifact_metadata=metadata or {},
        validation_status=validation_status,
        approval_status=approval_status,
    )
    try:
        db.add(artifact)
        event_service.record_event(
            db,
            task_id=task.id,
            step_run_id=step_run_id,
            event_type="artifact_created",
            actor_type="system",
            actor_name="artifact_service",
            payload={
                "artifact_id": artifact.id,
                "artifact_type": artifact.artifact_type,
                "logical_name": artifact.logical_name,
                "version": artifact.version,
                "size_bytes": artifact.size_bytes,
                "sha256": artifact.sha256,
            },
        )
        db.commit()
        db.refresh(artifact)
        return artifact
    except Exception as exc:
        db.rollback()
        storage.delete(stored.storage_uri)
        raise ArtifactStorageError(
            "Failed to register artifact.",
            {"artifact_id": artifact_id, "logical_name": safe_name},
        ) from exc


def read_artifact_bytes(artifact: Artifact) -> bytes:
    content = get_artifact_storage().read_bytes(artifact.storage_uri)
    if len(content) != artifact.size_bytes:
        raise ArtifactStorageError(
            "Artifact size does not match its database record.",
            {"artifact_id": artifact.id},
        )
    digest = hashlib.sha256(content).hexdigest()
    if digest != artifact.sha256:
        raise ArtifactStorageError(
            "Artifact hash does not match its database record.",
            {"artifact_id": artifact.id, "expected": artifact.sha256, "actual": digest},
        )
    return content


def resolve_artifact_path(artifact: Artifact):
    return get_artifact_storage().resolve_path(artifact.storage_uri)


def _next_version(db: Session, task_id: str, logical_name: str) -> int:
    stmt = select(func.max(Artifact.version)).where(
        Artifact.task_id == task_id,
        Artifact.logical_name == logical_name,
    )
    current = db.execute(stmt).scalar_one_or_none()
    return int(current or 0) + 1


def _validate_task_and_step(
    db: Session,
    task: AgentTask,
    step_run_id: str | None,
    artifact_type: str,
) -> None:
    current = task_status(task)
    planning_artifacts = {"model_raw_output", "plan_json", "plan_markdown"}
    if current != TaskStatus.RUNNING and not (
        current == TaskStatus.PLANNING and artifact_type in planning_artifacts
    ):
        raise InvalidStateTransition(
            "Artifact cannot be created in the current task state.",
            {"task_id": task.id, "current": current.value, "artifact_type": artifact_type},
        )
    if step_run_id is None:
        return
    step = db.get(StepRun, step_run_id)
    if step is None:
        raise EntityNotFound(f"Step run not found: {step_run_id}", {"step_run_id": step_run_id})
    workflow_run = db.get(WorkflowRun, step.workflow_run_id)
    if workflow_run is None or workflow_run.task_id != task.id:
        raise InvalidStateTransition(
            "Step run does not belong to the artifact task.",
            {"task_id": task.id, "step_run_id": step_run_id},
        )
