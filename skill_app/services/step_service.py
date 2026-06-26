from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.db.models import StepRun
from skill_app.domain.errors import EntityNotFound, InvalidStateTransition
from skill_app.domain.status import RunStatus
from skill_app.services import event_service


def get_step(db: Session, step_id: str) -> StepRun:
    step = db.get(StepRun, step_id)
    if step is None:
        raise EntityNotFound(f"Step run not found: {step_id}", {"step_id": step_id})
    return step


def get_latest_step(db: Session, workflow_run_id: str, step_key: str) -> StepRun | None:
    stmt = (
        select(StepRun)
        .where(StepRun.workflow_run_id == workflow_run_id, StepRun.step_key == step_key)
        .order_by(StepRun.retry_count.desc())
        .limit(1)
    )
    return db.execute(stmt).scalars().first()


def start_step(
    db: Session,
    *,
    task_id: str,
    workflow_run_id: str,
    step_key: str,
    agent_name: str,
    input_data: dict[str, Any] | None = None,
) -> StepRun:
    existing = get_latest_step(db, workflow_run_id, step_key)
    if existing is not None:
        if existing.status in {RunStatus.RUNNING, RunStatus.PAUSED}:
            return existing
        if existing.status == RunStatus.SUCCEEDED:
            raise InvalidStateTransition(
                f"Step has already succeeded: {step_key}",
                {"workflow_run_id": workflow_run_id, "step_key": step_key},
            )
        retry_count = existing.retry_count + 1
    else:
        retry_count = 0

    step = StepRun(
        id=str(uuid.uuid4()),
        workflow_run_id=workflow_run_id,
        step_key=step_key,
        agent_name=agent_name,
        status=RunStatus.RUNNING,
        input_data=input_data or {},
        output_data={},
        retry_count=retry_count,
        started_at=datetime.now(),
    )
    db.add(step)
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step.id,
        event_type="step_started",
        actor_type="agent",
        actor_name=agent_name,
        payload={"step_key": step_key, "retry_count": retry_count},
    )
    db.commit()
    db.refresh(step)
    return step


def create_pending_step(
    db: Session,
    *,
    task_id: str,
    workflow_run_id: str,
    step_key: str,
    agent_name: str,
    input_data: dict[str, Any] | None = None,
) -> StepRun:
    existing = get_latest_step(db, workflow_run_id, step_key)
    if existing is not None:
        return existing
    step = StepRun(
        id=str(uuid.uuid4()),
        workflow_run_id=workflow_run_id,
        step_key=step_key,
        agent_name=agent_name,
        status=RunStatus.PENDING,
        input_data=input_data or {},
        output_data={},
        retry_count=0,
    )
    db.add(step)
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step.id,
        event_type="step_planned",
        actor_type="agent",
        actor_name="planner",
        payload={"step_key": step_key, "agent_name": agent_name},
    )
    db.commit()
    db.refresh(step)
    return step


def pause_step(db: Session, *, task_id: str, step: StepRun, output_data: dict[str, Any]) -> StepRun:
    step.status = RunStatus.PAUSED
    step.output_data = output_data
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=step.workflow_run_id,
        step_run_id=step.id,
        event_type="step_paused",
        actor_type="agent",
        actor_name=step.agent_name,
        payload={"step_key": step.step_key, **output_data},
    )
    db.commit()
    db.refresh(step)
    return step


def complete_step(db: Session, *, task_id: str, step: StepRun, output_data: dict[str, Any]) -> StepRun:
    step.status = RunStatus.SUCCEEDED
    step.output_data = output_data
    step.finished_at = datetime.now()
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=step.workflow_run_id,
        step_run_id=step.id,
        event_type="step_completed",
        actor_type="agent",
        actor_name=step.agent_name,
        payload={"step_key": step.step_key},
    )
    db.commit()
    db.refresh(step)
    return step


def fail_step(db: Session, *, task_id: str, step: StepRun, error: Exception) -> StepRun:
    step.status = RunStatus.FAILED
    step.error_type = type(error).__name__
    step.error_message = str(error)
    step.finished_at = datetime.now()
    event_service.record_event(
        db,
        task_id=task_id,
        workflow_run_id=step.workflow_run_id,
        step_run_id=step.id,
        event_type="step_failed",
        actor_type="agent",
        actor_name=step.agent_name,
        payload={
            "step_key": step.step_key,
            "error_type": step.error_type,
            "error_message": step.error_message,
        },
    )
    db.commit()
    db.refresh(step)
    return step


def fail_running_steps_for_workflow(
    db: Session,
    *,
    task_id: str,
    workflow_run_id: str,
    error: Exception,
) -> list[StepRun]:
    stmt = select(StepRun).where(
        StepRun.workflow_run_id == workflow_run_id,
        StepRun.status == RunStatus.RUNNING,
    )
    failed = []
    for step in db.execute(stmt).scalars().all():
        failed.append(fail_step(db, task_id=task_id, step=step, error=error))
    return failed


def list_steps(db: Session, workflow_run_id: str) -> list[StepRun]:
    stmt = (
        select(StepRun)
        .where(StepRun.workflow_run_id == workflow_run_id)
        .order_by(StepRun.started_at.asc(), StepRun.id.asc())
    )
    return list(db.execute(stmt).scalars().all())
