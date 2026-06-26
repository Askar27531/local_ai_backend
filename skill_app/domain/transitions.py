from __future__ import annotations

from skill_app.db.models import AgentTask
from skill_app.domain.errors import InvalidStateTransition
from skill_app.domain.status import TaskStatus

ALLOWED_TASK_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.DRAFT: frozenset({TaskStatus.PLANNING, TaskStatus.CANCELLED}),
    TaskStatus.PLANNING: frozenset(
        {
            TaskStatus.AWAITING_PLAN_APPROVAL,
            TaskStatus.RUNNING,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.AWAITING_PLAN_APPROVAL: frozenset(
        {TaskStatus.RUNNING, TaskStatus.FAILED, TaskStatus.CANCELLED}
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.AWAITING_ARTIFACT_APPROVAL,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.AWAITING_ARTIFACT_APPROVAL: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.SUCCEEDED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
        }
    ),
    TaskStatus.FAILED: frozenset({TaskStatus.RUNNING, TaskStatus.CANCELLED}),
    TaskStatus.SUCCEEDED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
}


def task_status(task: AgentTask) -> TaskStatus:
    return TaskStatus(task.status)


def transition_task(task: AgentTask, target: TaskStatus) -> tuple[TaskStatus, TaskStatus] | None:
    current = task_status(task)
    if current == target:
        return None
    if target not in ALLOWED_TASK_TRANSITIONS[current]:
        raise InvalidStateTransition(
            f"Task cannot move from {current.value} to {target.value}.",
            {
                "task_id": task.id,
                "current": current.value,
                "target": target.value,
            },
        )
    task.status = target
    return current, target
