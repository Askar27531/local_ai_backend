from __future__ import annotations

import difflib
import shutil
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.config import get_settings
from skill_app.db.models import AgentTask, TaskWorkspace
from skill_app.domain.errors import EntityNotFound, InvalidStateTransition, UnsafePathError
from skill_app.services import artifact_service, event_service
from skill_app.tools.paths import IGNORED_PARTS, safe_path

ACTIVE = "active"
CLEANED = "cleaned"
SUPPORTED_STRATEGIES = frozenset({"copy_subset"})


@dataclass(frozen=True)
class WorkspaceDiff:
    content: str
    changed_paths: list[str]

    @property
    def changed(self) -> bool:
        return bool(self.changed_paths)


def create_workspace(
    db: Session,
    task: AgentTask,
    *,
    source_root: str = ".",
    strategy: str = "copy_subset",
    base_revision: str | None = None,
) -> TaskWorkspace:
    if strategy not in SUPPORTED_STRATEGIES:
        raise InvalidStateTransition(
            f"Unsupported workspace strategy: {strategy}",
            {"strategy": strategy, "supported": sorted(SUPPORTED_STRATEGIES)},
        )
    existing = _get_active_workspace(db, task.id)
    if existing is not None:
        raise InvalidStateTransition(
            "Task already has an active workspace.",
            {"task_id": task.id, "workspace_id": existing.id},
        )
    source = _resolve_source_root(source_root)
    workspace_id = str(uuid.uuid4())
    root = _expected_workspace_root(task.id, workspace_id)
    root.mkdir(parents=True, exist_ok=False)
    workspace = TaskWorkspace(
        id=workspace_id,
        task_id=task.id,
        source_root=str(source),
        workspace_root=str(root),
        strategy=strategy,
        base_revision=base_revision,
        status=ACTIVE,
        copied_paths=[],
    )
    try:
        db.add(workspace)
        event_service.record_event(
            db,
            task_id=task.id,
            event_type="workspace_created",
            actor_type="system",
            actor_name="workspace_service",
            payload={
                "workspace_id": workspace.id,
                "strategy": strategy,
                "source_root": str(source),
            },
        )
        db.commit()
        db.refresh(workspace)
        return workspace
    except Exception:
        db.rollback()
        shutil.rmtree(root, ignore_errors=True)
        raise


def get_workspace(db: Session, workspace_id: str) -> TaskWorkspace:
    workspace = db.get(TaskWorkspace, workspace_id)
    if workspace is None:
        raise EntityNotFound(
            f"Workspace not found: {workspace_id}",
            {"workspace_id": workspace_id},
        )
    return workspace


def list_task_workspaces(db: Session, task_id: str) -> list[TaskWorkspace]:
    stmt = (
        select(TaskWorkspace)
        .where(TaskWorkspace.task_id == task_id)
        .order_by(TaskWorkspace.created_at.asc(), TaskWorkspace.id.asc())
    )
    return list(db.execute(stmt).scalars().all())


def get_or_create_active_workspace(db: Session, task: AgentTask) -> TaskWorkspace:
    workspace = _get_active_workspace(db, task.id)
    if workspace is not None:
        _validate_workspace_root(workspace)
        return workspace
    return create_workspace(db, task)


def copy_files(db: Session, workspace: TaskWorkspace, paths: list[str]) -> TaskWorkspace:
    _require_active(workspace)
    source_root = Path(workspace.source_root).resolve()
    workspace_root = _validate_workspace_root(workspace)
    copied = set(workspace.copied_paths)
    sources: list[tuple[Path, str]] = []
    for raw_path in paths:
        source = safe_path(source_root, raw_path, must_exist=True)
        relative = source.relative_to(source_root).as_posix()
        if not source.is_file() or _is_workspace_ignored(relative):
            raise UnsafePathError(
                "Workspace source must be an allowed regular file.",
                {"path": raw_path},
            )
        sources.append((source, relative))
    normalized_paths: list[str] = []
    for source, relative in sources:
        target = safe_path(workspace_root, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        copied.add(relative)
        normalized_paths.append(relative)
    workspace.copied_paths = sorted(copied)
    event_service.record_event(
        db,
        task_id=workspace.task_id,
        event_type="workspace_files_copied",
        actor_type="system",
        actor_name="workspace_service",
        payload={"workspace_id": workspace.id, "paths": normalized_paths},
    )
    db.commit()
    db.refresh(workspace)
    return workspace


def resolve_workspace_path(
    workspace: TaskWorkspace,
    relative_path: str,
    *,
    must_exist: bool = False,
) -> Path:
    _require_active(workspace)
    return safe_path(_validate_workspace_root(workspace), relative_path, must_exist=must_exist)


def build_diff(
    db: Session,
    workspace: TaskWorkspace,
    *,
    create_artifact: bool = True,
) -> tuple[WorkspaceDiff, str | None]:
    _require_active(workspace)
    source_root = Path(workspace.source_root).resolve()
    workspace_root = _validate_workspace_root(workspace)
    candidate_paths = {
        path.relative_to(workspace_root).as_posix()
        for path in workspace_root.rglob("*")
        if path.is_file() and not _is_workspace_ignored(path.relative_to(workspace_root).as_posix())
    }
    all_paths = sorted(candidate_paths | set(workspace.copied_paths))
    chunks: list[str] = []
    changed_paths: list[str] = []
    for relative in all_paths:
        source = safe_path(source_root, relative)
        candidate = safe_path(workspace_root, relative)
        source_bytes = source.read_bytes() if source.is_file() else b""
        candidate_bytes = candidate.read_bytes() if candidate.is_file() else b""
        if source_bytes == candidate_bytes:
            continue
        changed_paths.append(relative)
        chunks.append(_file_diff(relative, source_bytes, candidate_bytes))
    result = WorkspaceDiff(content="".join(chunks), changed_paths=changed_paths)
    artifact_id: str | None = None
    if create_artifact:
        task = db.get(AgentTask, workspace.task_id)
        if task is None:
            raise EntityNotFound(f"Task not found: {workspace.task_id}", {"task_id": workspace.task_id})
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            artifact_type="workspace_diff",
            logical_name=f"workspace-{workspace.id}.diff",
            content=result.content,
            mime_type="text/x-diff; charset=utf-8",
            metadata={
                "workspace_id": workspace.id,
                "strategy": workspace.strategy,
                "changed_paths": changed_paths,
            },
            validation_status="valid",
        )
        artifact_id = artifact.id
    event_service.record_event(
        db,
        task_id=workspace.task_id,
        event_type="workspace_diff_built",
        actor_type="system",
        actor_name="workspace_service",
        payload={
            "workspace_id": workspace.id,
            "changed": result.changed,
            "changed_paths": changed_paths,
            "artifact_id": artifact_id,
        },
    )
    db.commit()
    return result, artifact_id


def cleanup_workspace(db: Session, workspace: TaskWorkspace) -> TaskWorkspace:
    if workspace.status == CLEANED:
        return workspace
    _require_active(workspace)
    root = _validate_workspace_root(workspace)
    if root.exists():
        shutil.rmtree(root)
    workspace.status = CLEANED
    workspace.cleaned_at = datetime.now()
    event_service.record_event(
        db,
        task_id=workspace.task_id,
        event_type="workspace_cleaned",
        actor_type="system",
        actor_name="workspace_service",
        payload={"workspace_id": workspace.id},
    )
    db.commit()
    db.refresh(workspace)
    return workspace


def _resolve_source_root(raw_path: str) -> Path:
    project_root = get_settings().project_root.resolve()
    source = safe_path(project_root, raw_path, must_exist=True)
    if not source.is_dir():
        raise UnsafePathError("Workspace source root must be a directory.", {"source_root": raw_path})
    return source


def _get_active_workspace(db: Session, task_id: str) -> TaskWorkspace | None:
    stmt = (
        select(TaskWorkspace)
        .where(TaskWorkspace.task_id == task_id, TaskWorkspace.status == ACTIVE)
        .order_by(TaskWorkspace.created_at.desc(), TaskWorkspace.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def _expected_workspace_root(task_id: str, workspace_id: str) -> Path:
    managed_root = get_settings().workspace_root.resolve()
    return (managed_root / task_id / workspace_id).resolve()


def _validate_workspace_root(workspace: TaskWorkspace) -> Path:
    managed_root = get_settings().workspace_root.resolve()
    expected = _expected_workspace_root(workspace.task_id, workspace.id)
    actual = Path(workspace.workspace_root).resolve()
    if actual != expected:
        raise UnsafePathError(
            "Workspace path does not match its managed location.",
            {"workspace_id": workspace.id},
        )
    try:
        actual.relative_to(managed_root)
    except ValueError as exc:
        raise UnsafePathError(
            "Workspace path escapes the managed workspace root.",
            {"workspace_id": workspace.id},
        ) from exc
    return actual


def _is_workspace_ignored(relative_path: str) -> bool:
    return any(part in IGNORED_PARTS for part in Path(relative_path).parts)


def _require_active(workspace: TaskWorkspace) -> None:
    if workspace.status != ACTIVE:
        raise InvalidStateTransition(
            "Workspace is not active.",
            {"workspace_id": workspace.id, "status": workspace.status},
        )


def _file_diff(relative: str, source: bytes, candidate: bytes) -> str:
    try:
        source_text = source.decode("utf-8")
        candidate_text = candidate.decode("utf-8")
    except UnicodeDecodeError:
        return f"Binary files a/{relative} and b/{relative} differ\n"
    return "".join(
        difflib.unified_diff(
            source_text.splitlines(keepends=True),
            candidate_text.splitlines(keepends=True),
            fromfile=f"a/{relative}",
            tofile=f"b/{relative}",
        )
    )
