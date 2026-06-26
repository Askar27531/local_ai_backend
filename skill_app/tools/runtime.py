from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.config import get_settings
from skill_app.db.models import ApprovalRequest
from skill_app.domain.errors import (
    ApprovalRequired,
    DomainError,
    InvalidStateTransition,
    ToolExecutionError,
    ToolInputValidationError,
    ToolPermissionDenied,
)
from skill_app.domain.status import ApprovalStatus, RunStatus, TaskStatus
from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.services import (
    artifact_service,
    event_service,
    policy_service,
    skill_service,
    step_service,
    task_service,
    workflow_service,
    workspace_service,
)
from skill_app.tools.builtin import register_builtin_tools
from skill_app.tools.registry import tool_registry


def execute_tool(
    db: Session,
    *,
    task_id: str,
    workflow_run_id: str,
    step_run_id: str,
    agent_name: str,
    tool_name: str,
    arguments: dict[str, Any],
) -> ToolResult:
    register_builtin_tools()
    settings = get_settings()
    task = task_service.get_task(db, task_id)
    run = workflow_service.get_workflow_run(db, workflow_run_id)
    step = step_service.get_step(db, step_run_id)
    _validate_context(task.id, task.status, run, step, agent_name)
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        event_type="tool_requested",
        actor_type="agent",
        actor_name=agent_name,
        payload={"tool_name": tool_name},
    )
    db.commit()
    try:
        registered = tool_registry.get(tool_name)
        permissions = policy_service.require_tool_permission(agent_name, tool_name)
        skill_binding = skill_service.manifest_for_workflow(db, workflow_run_id)
        if skill_binding is not None:
            skill_run, skill_manifest = skill_binding
            if tool_name not in skill_manifest.tools.allow:
                raise ToolPermissionDenied(
                    f"Skill {skill_manifest.name} is not allowed to use tool {tool_name}.",
                    {
                        "skill_name": skill_manifest.name,
                        "skill_version": skill_manifest.version,
                        "tool_name": tool_name,
                    },
                )
            _validate_skill_paths(
                arguments,
                permission=registered.definition.permission,
                read_patterns=skill_manifest.data_policy.read,
                write_patterns=skill_manifest.data_policy.write,
                deny_patterns=skill_manifest.data_policy.deny,
            )
            permissions = sorted(set(permissions) & set(skill_manifest.tools.allow))
        else:
            skill_run = None
            skill_manifest = None
        if registered.definition.approval_required and not _has_tool_approval(db, task_id, tool_name):
            raise ApprovalRequired(
                f"Tool requires approval: {tool_name}",
                {"task_id": task_id, "tool_name": tool_name},
            )
    except DomainError as exc:
        _record_pre_execution_failure(db, task_id, workflow_run_id, step_run_id, agent_name, tool_name, exc)
        raise

    try:
        validated = registered.input_model.model_validate(arguments)
    except ValidationError as exc:
        error = ToolInputValidationError(
            f"Invalid input for tool {tool_name}.",
            {"tool_name": tool_name, "errors": exc.errors(include_url=False)},
        )
        _record_pre_execution_failure(db, task_id, workflow_run_id, step_run_id, agent_name, tool_name, error)
        raise error from exc

    workspace = workspace_service.get_or_create_active_workspace(db, task)
    context = ToolContext(
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        agent_name=agent_name,
        project_root=str(settings.project_root.resolve()),
        workspace_root=workspace.workspace_root,
        permissions=permissions,
        timeout_seconds=min(
            registered.definition.timeout_seconds,
            settings.tool_timeout_seconds,
            (
                skill_manifest.limits.tool_timeout_seconds
                if skill_manifest is not None
                else settings.tool_timeout_seconds
            ),
        ),
        skill_run_id=skill_run.id if skill_run is not None else None,
        skill_name=skill_manifest.name if skill_manifest is not None else None,
        skill_version=skill_manifest.version if skill_manifest is not None else None,
    )
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        event_type="tool_called",
        actor_type="agent",
        actor_name=agent_name,
        payload={
            "tool_name": tool_name,
            "side_effect_level": registered.definition.side_effect_level,
            "skill_name": context.skill_name,
            "skill_version": context.skill_version,
        },
    )
    db.commit()

    started = time.perf_counter()
    try:
        result = _execute_with_timeout(
            registered.handler,
            context,
            validated,
            context.timeout_seconds,
        )
        artifact_ids = _register_result_artifacts(db, task, step_run_id, context, tool_name, result.artifacts)
        result.artifacts = artifact_ids
        result = _truncate_result(result, settings.tool_output_chars)
        result.metrics.setdefault("duration_ms", round((time.perf_counter() - started) * 1000, 2))
        _record_tool_result(db, context, tool_name, result)
        return result
    except FutureTimeoutError:
        result = ToolResult(
            ok=False,
            output={},
            error_type="tool_timeout",
            stderr=f"Tool timed out after {context.timeout_seconds} seconds.",
            metrics={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
        _record_tool_result(db, context, tool_name, result)
        return result
    except DomainError as exc:
        _record_tool_failure(db, context, tool_name, exc)
        raise
    except Exception as exc:
        _record_tool_failure(db, context, tool_name, exc)
        raise ToolExecutionError(
            f"Tool execution failed: {tool_name}",
            {"tool_name": tool_name, "error_type": type(exc).__name__, "message": str(exc)},
        ) from exc


def _execute_with_timeout(handler, context: ToolContext, validated, timeout_seconds: int) -> ToolResult:
    executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="skill-tool")
    future = executor.submit(handler, context, validated)
    try:
        return future.result(timeout=timeout_seconds)
    finally:
        executor.shutdown(wait=False, cancel_futures=True)


def _validate_skill_paths(
    arguments: dict[str, Any],
    *,
    permission: str,
    read_patterns: list[str],
    write_patterns: list[str],
    deny_patterns: list[str],
) -> None:
    paths = _extract_paths(arguments)
    if not paths:
        return
    allowed_patterns = write_patterns if permission == "write_workspace" else read_patterns
    for raw_path in paths:
        normalized = raw_path.replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        path = PurePosixPath(normalized)
        if path.is_absolute() or ".." in path.parts or ":" in path.parts[0]:
            raise ToolPermissionDenied(
                "Skill tool path must remain relative.",
                {"path": raw_path},
            )
        if any(fnmatchcase(normalized, pattern) for pattern in deny_patterns):
            raise ToolPermissionDenied(
                "Skill data policy denies this path.",
                {"path": normalized},
            )
        if not any(fnmatchcase(normalized, pattern) for pattern in allowed_patterns):
            raise ToolPermissionDenied(
                "Skill data policy does not allow this path.",
                {
                    "path": normalized,
                    "permission": permission,
                    "allowed_patterns": allowed_patterns,
                },
            )


def _extract_paths(value: Any, key: str | None = None) -> list[str]:
    paths: list[str] = []
    if isinstance(value, dict):
        for child_key, child in value.items():
            paths.extend(_extract_paths(child, child_key))
    elif isinstance(value, list):
        for child in value:
            paths.extend(_extract_paths(child, key))
    elif isinstance(value, str) and key in {"path", "paths", "pattern"}:
        paths.append(value)
    return paths


def _validate_context(task_id: str, task_status: str, run, step, agent_name: str) -> None:
    if task_status != TaskStatus.RUNNING:
        raise InvalidStateTransition(
            "Tools can only execute while a task is running.",
            {"task_id": task_id, "current": str(task_status)},
        )
    if run.task_id != task_id:
        raise InvalidStateTransition("Workflow run does not belong to task.")
    if run.status != RunStatus.RUNNING:
        raise InvalidStateTransition(
            "Tools can only execute while a workflow run is running.",
            {"workflow_run_id": run.id, "current": str(run.status)},
        )
    if step.workflow_run_id != run.id:
        raise InvalidStateTransition("Step run does not belong to workflow run.")
    if step.status != RunStatus.RUNNING:
        raise InvalidStateTransition(
            "Tools can only execute for a running step.",
            {"step_run_id": step.id, "current": str(step.status)},
        )
    if step.agent_name != agent_name:
        raise InvalidStateTransition(
            "Tool agent does not match the step owner.",
            {"step_run_id": step.id, "expected": step.agent_name, "actual": agent_name},
        )


def _has_tool_approval(db: Session, task_id: str, tool_name: str) -> bool:
    stmt = select(ApprovalRequest.id).where(
        ApprovalRequest.task_id == task_id,
        ApprovalRequest.approval_type == f"tool:{tool_name}",
        ApprovalRequest.status == ApprovalStatus.APPROVED,
    )
    return db.execute(stmt).scalar_one_or_none() is not None


def _register_result_artifacts(
    db: Session,
    task,
    step_run_id: str,
    context: ToolContext,
    tool_name: str,
    paths: list[str],
) -> list[str]:
    artifact_ids: list[str] = []
    workspace_root = workspace_service.resolve_workspace_path(
        workspace_service.get_or_create_active_workspace(db, task),
        ".",
    )
    for relative_path in paths:
        path = (workspace_root / relative_path).resolve()
        try:
            path.relative_to(workspace_root)
        except ValueError as exc:
            raise ToolExecutionError("Tool artifact escaped workspace.", {"path": relative_path}) from exc
        if not path.is_file():
            raise ToolExecutionError("Tool artifact file does not exist.", {"path": relative_path})
        artifact = artifact_service.create_bytes_artifact(
            db,
            task,
            step_run_id=step_run_id,
            artifact_type="tool_output",
            logical_name=path.name,
            content=path.read_bytes(),
            metadata={"tool_name": tool_name, "workspace_path": relative_path},
        )
        artifact_ids.append(artifact.id)
    return artifact_ids


def _truncate_result(result: ToolResult, max_chars: int) -> ToolResult:
    result.stdout = _truncate_text(result.stdout, max_chars)
    result.stderr = _truncate_text(result.stderr, max_chars)
    serialized = json.dumps(result.output, ensure_ascii=False, default=str)
    if len(serialized) > max_chars:
        preserved: dict[str, Any] = {}
        per_value_limit = max(500, max_chars // max(1, len(result.output)))
        for key, value in result.output.items():
            if isinstance(value, str) and len(value) > per_value_limit:
                preserved[key] = _truncate_text(value, per_value_limit)
            else:
                preserved[key] = value
        preserved["truncated"] = True
        preserved["total_chars"] = len(serialized)
        result.output = preserved
    return result


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n[truncated {len(text) - max_chars} chars]"


def _record_tool_result(db: Session, context: ToolContext, tool_name: str, result: ToolResult) -> None:
    event_service.record_event(
        db,
        task_id=context.task_id,
        workflow_run_id=context.workflow_run_id,
        step_run_id=context.step_run_id,
        event_type="tool_completed" if result.ok else "tool_failed",
        actor_type="tool",
        actor_name=tool_name,
        payload={
            "ok": result.ok,
            "exit_code": result.exit_code,
            "error_type": result.error_type,
            "artifact_ids": result.artifacts,
            "metrics": result.metrics,
        },
    )
    db.commit()


def _record_tool_failure(
    db: Session,
    context: ToolContext,
    tool_name: str,
    error: Exception,
) -> None:
    event_service.record_event(
        db,
        task_id=context.task_id,
        workflow_run_id=context.workflow_run_id,
        step_run_id=context.step_run_id,
        event_type="tool_failed",
        actor_type="tool",
        actor_name=tool_name,
        payload={"error_type": type(error).__name__, "message": str(error)},
    )
    db.commit()


def _record_pre_execution_failure(
    db: Session,
    task_id: str,
    workflow_run_id: str,
    step_run_id: str,
    agent_name: str,
    tool_name: str,
    error: Exception,
) -> None:
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        event_type="tool_denied",
        actor_type="agent",
        actor_name=agent_name,
        payload={
            "tool_name": tool_name,
            "error_type": type(error).__name__,
            "message": str(error),
        },
    )
    db.commit()
