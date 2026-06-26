from __future__ import annotations

import pytest

from skill_app.db.models import AgentTask
from skill_app.domain.errors import InvalidStateTransition
from skill_app.domain.status import TaskStatus
from skill_app.domain.transitions import transition_task


def make_task(status: TaskStatus = TaskStatus.DRAFT) -> AgentTask:
    return AgentTask(
        id="task-1",
        project_id="demo",
        title="Demo task",
        request="Do work.",
        status=status,
    )


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (TaskStatus.DRAFT, TaskStatus.PLANNING),
        (TaskStatus.PLANNING, TaskStatus.AWAITING_PLAN_APPROVAL),
        (TaskStatus.PLANNING, TaskStatus.RUNNING),
        (TaskStatus.AWAITING_PLAN_APPROVAL, TaskStatus.RUNNING),
        (TaskStatus.RUNNING, TaskStatus.AWAITING_ARTIFACT_APPROVAL),
        (TaskStatus.RUNNING, TaskStatus.SUCCEEDED),
        (TaskStatus.RUNNING, TaskStatus.FAILED),
        (TaskStatus.AWAITING_ARTIFACT_APPROVAL, TaskStatus.RUNNING),
        (TaskStatus.AWAITING_ARTIFACT_APPROVAL, TaskStatus.SUCCEEDED),
        (TaskStatus.FAILED, TaskStatus.RUNNING),
    ],
)
def test_transition_task_allows_defined_transition(current, target):
    task = make_task(current)

    transition = transition_task(task, target)

    assert transition == (current, target)
    assert task.status == target


def test_transition_task_is_idempotent_for_same_status():
    task = make_task(TaskStatus.CANCELLED)

    assert transition_task(task, TaskStatus.CANCELLED) is None


@pytest.mark.parametrize("terminal_status", [TaskStatus.SUCCEEDED, TaskStatus.CANCELLED])
def test_transition_task_rejects_leaving_terminal_state(terminal_status):
    task = make_task(terminal_status)

    with pytest.raises(InvalidStateTransition) as error:
        transition_task(task, TaskStatus.RUNNING)

    assert error.value.details["current"] == terminal_status.value
    assert error.value.details["target"] == TaskStatus.RUNNING.value


def test_transition_task_rejects_skipping_planning():
    task = make_task(TaskStatus.DRAFT)

    with pytest.raises(InvalidStateTransition, match="draft to running"):
        transition_task(task, TaskStatus.RUNNING)
