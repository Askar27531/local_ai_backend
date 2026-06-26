from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.db.models import TaskEvent


def record_event(
    db: Session,
    *,
    task_id: str,
    event_type: str,
    actor_type: str,
    actor_name: str,
    payload: dict[str, Any] | None = None,
    workflow_run_id: str | None = None,
    step_run_id: str | None = None,
) -> TaskEvent:
    event = TaskEvent(
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        step_run_id=step_run_id,
        event_type=event_type,
        actor_type=actor_type,
        actor_name=actor_name,
        payload=payload or {},
    )
    db.add(event)
    return event


def record_status_change(
    db: Session,
    *,
    task_id: str,
    current: str,
    target: str,
    actor_type: str,
    actor_name: str,
    reason: str,
    workflow_run_id: str | None = None,
) -> TaskEvent:
    return record_event(
        db,
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        event_type="task_status_changed",
        actor_type=actor_type,
        actor_name=actor_name,
        payload={
            "from": current,
            "to": target,
            "reason": reason,
        },
    )


def list_task_events(db: Session, task_id: str, limit: int = 500) -> list[TaskEvent]:
    stmt = select(TaskEvent).where(TaskEvent.task_id == task_id).order_by(TaskEvent.id.asc()).limit(limit)
    return list(db.execute(stmt).scalars().all())
