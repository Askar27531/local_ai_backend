from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.db.models import AgentTask, WorkflowRun
from skill_app.domain.errors import EntityNotFound, InvalidStateTransition
from skill_app.domain.status import RunStatus, TaskStatus
from skill_app.domain.transitions import transition_task
from skill_app.services import event_service


def get_workflow_run(db: Session, workflow_run_id: str) -> WorkflowRun:
    run = db.get(WorkflowRun, workflow_run_id)
    if run is None:
        raise EntityNotFound(
            f"Workflow run not found: {workflow_run_id}",
            {"workflow_run_id": workflow_run_id},
        )
    return run


def get_latest_active_run(db: Session, task_id: str) -> WorkflowRun | None:
    stmt = (
        select(WorkflowRun)
        .where(
            WorkflowRun.task_id == task_id,
            WorkflowRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED]),
        )
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def start_run(db: Session, task: AgentTask, run: WorkflowRun) -> None:
    if run.status not in {RunStatus.PENDING, RunStatus.PAUSED}:
        raise InvalidStateTransition(
            f"Workflow run cannot start from {run.status}.",
            {"workflow_run_id": run.id, "current": str(run.status)},
        )
    run.status = RunStatus.RUNNING
    run.started_at = run.started_at or datetime.now()
    transition = transition_task(task, TaskStatus.RUNNING)
    if transition is not None:
        current, target = transition
        event_service.record_status_change(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            current=current.value,
            target=target.value,
            actor_type="system",
            actor_name="workflow_runtime",
            reason="workflow_started",
        )
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_started",
        actor_type="system",
        actor_name="workflow_runtime",
        payload={"workflow_name": run.workflow_name, "graph_version": run.graph_version},
    )
    db.commit()


def start_planning_run(db: Session, task: AgentTask, run: WorkflowRun) -> None:
    if run.status != RunStatus.PENDING or task.status != TaskStatus.PLANNING:
        raise InvalidStateTransition(
            "Planning run requires a pending run and planning task.",
            {"workflow_run_id": run.id, "run_status": str(run.status), "task_status": str(task.status)},
        )
    run.status = RunStatus.RUNNING
    run.started_at = run.started_at or datetime.now()
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_started",
        actor_type="system",
        actor_name="workflow_runtime",
        payload={
            "workflow_name": run.workflow_name,
            "graph_version": run.graph_version,
            "phase": "planning",
        },
    )
    db.commit()


def update_run_state(
    db: Session,
    run: WorkflowRun,
    *,
    status: RunStatus,
    state_snapshot: dict[str, Any],
) -> WorkflowRun:
    run.status = status
    run.state_snapshot = state_snapshot
    if status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
        run.finished_at = datetime.now()
    db.commit()
    db.refresh(run)
    return run


def pause_run(db: Session, task: AgentTask, run: WorkflowRun, state_snapshot: dict[str, Any]) -> WorkflowRun:
    run.status = RunStatus.PAUSED
    run.state_snapshot = state_snapshot
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_paused",
        actor_type="system",
        actor_name="workflow_runtime",
        payload={"approval_id": state_snapshot.get("approval_id")},
    )
    db.commit()
    db.refresh(run)
    return run


def resume_run(db: Session, task: AgentTask, run: WorkflowRun) -> WorkflowRun:
    if run.status != RunStatus.PAUSED:
        raise InvalidStateTransition(
            f"Workflow run cannot resume from {run.status}.",
            {"workflow_run_id": run.id, "current": str(run.status)},
        )
    run.status = RunStatus.RUNNING
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_resumed",
        actor_type="system",
        actor_name="workflow_runtime",
    )
    db.commit()
    db.refresh(run)
    return run


def complete_run(db: Session, task: AgentTask, run: WorkflowRun, state_snapshot: dict[str, Any]) -> WorkflowRun:
    transition = transition_task(task, TaskStatus.SUCCEEDED)
    if transition is not None:
        current, target = transition
        event_service.record_status_change(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            current=current.value,
            target=target.value,
            actor_type="system",
            actor_name="workflow_runtime",
            reason="workflow_completed",
        )
    run.status = RunStatus.SUCCEEDED
    run.state_snapshot = state_snapshot
    run.finished_at = datetime.now()
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_completed",
        actor_type="system",
        actor_name="workflow_runtime",
    )
    db.commit()
    db.refresh(run)
    return run


def fail_run(db: Session, task: AgentTask, run: WorkflowRun, error: Exception) -> WorkflowRun:
    if task.status not in {TaskStatus.CANCELLED, TaskStatus.SUCCEEDED}:
        transition = transition_task(task, TaskStatus.FAILED)
        if transition is not None:
            current, target = transition
            event_service.record_status_change(
                db,
                task_id=task.id,
                workflow_run_id=run.id,
                current=current.value,
                target=target.value,
                actor_type="system",
                actor_name="workflow_runtime",
                reason="workflow_failed",
            )
    run.status = RunStatus.FAILED
    run.finished_at = datetime.now()
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_failed",
        actor_type="system",
        actor_name="workflow_runtime",
        payload={"error_type": type(error).__name__, "error_message": str(error)},
    )
    db.commit()
    db.refresh(run)
    return run


def cancel_run(db: Session, task: AgentTask, run: WorkflowRun, actor_name: str) -> WorkflowRun:
    if run.status == RunStatus.CANCELLED:
        return run
    if run.status in {RunStatus.SUCCEEDED, RunStatus.FAILED}:
        raise InvalidStateTransition(
            f"Workflow run cannot be cancelled from {run.status}.",
            {"workflow_run_id": run.id, "current": str(run.status)},
        )
    run.status = RunStatus.CANCELLED
    run.finished_at = datetime.now()
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_cancelled",
        actor_type="user",
        actor_name=actor_name,
    )
    db.commit()
    db.refresh(run)
    return run
