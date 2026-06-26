from __future__ import annotations

import logging
from threading import Thread
from typing import Any

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from sqlalchemy.orm import Session

from skill_app.config import get_settings
from skill_app.db.database import SessionLocal
from skill_app.db.models import ApprovalRequest, WorkflowRun
from skill_app.domain.errors import InvalidStateTransition, WorkflowExecutionError
from skill_app.domain.status import ApprovalStatus, RunStatus, TaskStatus
from skill_app.schemas.skill import LoadedSkillPackage
from skill_app.schemas.workflow import WorkflowRunCreate
from skill_app.services import skill_service, step_service, task_service, workflow_service
from skill_app.workflows.builtin import register_builtin_workflows
from skill_app.workflows.registry import workflow_registry

logger = logging.getLogger(__name__)


def _config(workflow_run_id: str) -> dict[str, dict[str, str]]:
    return {"configurable": {"thread_id": workflow_run_id}}


def _checkpoint_path() -> str:
    settings = get_settings()
    settings.ensure_local_directories()
    return str(settings.checkpoint_path)


def _state_values(graph: Any, workflow_run_id: str) -> dict[str, Any]:
    snapshot = graph.get_state(_config(workflow_run_id))
    return dict(snapshot.values)


def _invoke_graph(run: WorkflowRun, command: Command | dict[str, Any]) -> tuple[dict[str, Any], bool]:
    register_builtin_workflows()
    definition = workflow_registry.get(run.workflow_name, run.graph_version)
    with SqliteSaver.from_conn_string(_checkpoint_path()) as checkpointer:
        checkpointer.setup()
        graph = definition.build_graph(checkpointer)
        graph.invoke(command, _config(run.id))
        snapshot = graph.get_state(_config(run.id))
        return dict(snapshot.values), bool(snapshot.next)


def start_workflow(
    db: Session,
    task_id: str,
    workflow_name: str,
    graph_version: str = "1",
    *,
    skill_package: LoadedSkillPackage | None = None,
) -> WorkflowRun:
    run = prepare_workflow_start(
        db,
        task_id,
        workflow_name,
        graph_version,
        skill_package=skill_package,
    )
    return _execute(db, run, run.state_snapshot)


def prepare_workflow_start(
    db: Session,
    task_id: str,
    workflow_name: str,
    graph_version: str = "1",
    *,
    skill_package: LoadedSkillPackage | None = None,
) -> WorkflowRun:
    register_builtin_workflows()
    workflow_registry.get(workflow_name, graph_version)
    task = task_service.get_task(db, task_id)
    payload = WorkflowRunCreate(workflow_name=workflow_name, graph_version=graph_version)
    if task.status == TaskStatus.DRAFT:
        run = task_service.create_workflow_run(db, task, payload)
    elif workflow_name == "game_feature":
        run = task_service.create_execution_workflow_run(db, task, payload)
    else:
        raise InvalidStateTransition(
            "Only game_feature can start from a planned running task.",
            {"task_id": task.id, "current": str(task.status), "workflow_name": workflow_name},
        )
    initial_state = {
        "task_id": task.id,
        "workflow_run_id": run.id,
        "request": task.request,
        "project_id": task.project_id,
        "artifact_ids": [],
    }
    if skill_package is not None:
        skill_run = skill_service.create_skill_run(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            package=skill_package,
        )
        initial_state["skill_run_id"] = skill_run.id
        initial_state["skill_runtime"] = {
            "name": skill_package.manifest.name,
            "version": skill_package.manifest.version,
            "package_sha256": skill_package.package_sha256,
            "manifest": skill_package.manifest.model_dump(mode="json"),
            "instructions": {
                skill_package.manifest.name: skill_package.instructions.body,
                **{
                    name: skill.body
                    for name, skill in skill_package.component_instructions.items()
                },
            },
            "component_instruction_sha256s": (
                skill_package.component_instruction_sha256s
            ),
        }
    run.state_snapshot = initial_state
    db.commit()
    db.refresh(run)
    return run


def resume_workflow(db: Session, workflow_run_id: str, resume_value: dict[str, Any]) -> WorkflowRun:
    run = prepare_workflow_resume(db, workflow_run_id)
    return _execute(db, run, Command(resume=resume_value))


def prepare_workflow_resume(db: Session, workflow_run_id: str) -> WorkflowRun:
    run = workflow_service.get_workflow_run(db, workflow_run_id)
    task = task_service.get_task(db, run.task_id)
    if run.status != RunStatus.PAUSED:
        raise InvalidStateTransition(
            f"Workflow run is not paused: {workflow_run_id}",
            {"workflow_run_id": workflow_run_id, "current": str(run.status)},
        )
    if task.status != TaskStatus.RUNNING:
        raise InvalidStateTransition(
            "Task must be running before workflow resume.",
            {"task_id": task.id, "current": str(task.status)},
        )
    workflow_service.resume_run(db, task, run)
    skill_service.update_skill_run_status(db, run.id, RunStatus.RUNNING)
    db.expire_all()
    return workflow_service.get_workflow_run(db, workflow_run_id)


def execute_workflow_run(workflow_run_id: str) -> None:
    db = SessionLocal()
    try:
        run = workflow_service.get_workflow_run(db, workflow_run_id)
        _execute(db, run, run.state_snapshot)
    except Exception:
        logger.exception("Background workflow execution failed: %s", workflow_run_id)
    finally:
        db.close()


def execute_workflow_resume(workflow_run_id: str, resume_value: dict[str, Any]) -> None:
    db = SessionLocal()
    try:
        run = workflow_service.get_workflow_run(db, workflow_run_id)
        _execute(db, run, Command(resume=resume_value))
    except Exception:
        logger.exception("Background workflow resume failed: %s", workflow_run_id)
    finally:
        db.close()


def enqueue_workflow_run(workflow_run_id: str) -> None:
    Thread(target=execute_workflow_run, args=(workflow_run_id,), daemon=True).start()


def enqueue_workflow_resume(workflow_run_id: str, resume_value: dict[str, Any]) -> None:
    Thread(
        target=execute_workflow_resume,
        args=(workflow_run_id, resume_value),
        daemon=True,
    ).start()


def _execute(
    db: Session,
    run: WorkflowRun,
    command: Command | dict[str, Any],
) -> WorkflowRun:
    task = task_service.get_task(db, run.task_id)
    try:
        state, paused = _invoke_graph(run, command)
        db.expire_all()
        run = workflow_service.get_workflow_run(db, run.id)
        task = task_service.get_task(db, run.task_id)
        if paused:
            paused_run = workflow_service.pause_run(db, task, run, state)
            skill_service.update_skill_run_status(db, run.id, RunStatus.PAUSED)
            return paused_run
        definition = workflow_registry.get(run.workflow_name, run.graph_version)
        if not definition.completes_task:
            completed = workflow_service.update_run_state(
                db,
                run,
                status=RunStatus.SUCCEEDED,
                state_snapshot=state,
            )
            skill_service.update_skill_run_status(db, run.id, RunStatus.SUCCEEDED)
            return completed
        completed = workflow_service.complete_run(db, task, run, state)
        skill_service.update_skill_run_status(db, run.id, RunStatus.SUCCEEDED)
        return completed
    except Exception as exc:
        db.rollback()
        run = workflow_service.get_workflow_run(db, run.id)
        task = task_service.get_task(db, run.task_id)
        step_service.fail_running_steps_for_workflow(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            error=exc,
        )
        workflow_service.fail_run(db, task, run, exc)
        skill_service.update_skill_run_status(db, run.id, RunStatus.FAILED)
        if isinstance(exc, (InvalidStateTransition, WorkflowExecutionError)):
            raise
        raise WorkflowExecutionError(
            f"Workflow execution failed: {run.id}",
            {
                "workflow_run_id": run.id,
                "error_type": type(exc).__name__,
                "error_message": str(exc),
            },
        ) from exc


def cancel_workflow(db: Session, workflow_run_id: str, actor_name: str) -> WorkflowRun:
    run = workflow_service.get_workflow_run(db, workflow_run_id)
    task = task_service.get_task(db, run.task_id)
    if task.status != TaskStatus.CANCELLED:
        task_service.cancel_task(db, task, actor_name)
        db.expire_all()
        task = task_service.get_task(db, run.task_id)
    cancelled = workflow_service.cancel_run(db, task, run, actor_name)
    skill_service.update_skill_run_status(db, run.id, RunStatus.CANCELLED)
    return cancelled


def handle_approval_resolution(db: Session, approval: ApprovalRequest) -> WorkflowRun | None:
    run = workflow_service.get_latest_active_run(db, approval.task_id)
    if run is None:
        return None
    if approval.status == ApprovalStatus.APPROVED and run.status == RunStatus.PAUSED:
        return resume_workflow(
            db,
            run.id,
            {
                "approved": True,
                "approval_id": approval.id,
                "resolved_by": approval.resolved_by,
                "comment": approval.comment,
            },
        )
    if approval.status == ApprovalStatus.REJECTED:
        return workflow_service.cancel_run(
            db,
            task_service.get_task(db, approval.task_id),
            run,
            approval.resolved_by or "unknown",
        )
    return run
