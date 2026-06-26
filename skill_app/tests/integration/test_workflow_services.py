from __future__ import annotations

from skill_app.db.database import SessionLocal
from skill_app.domain.status import RunStatus, TaskStatus
from skill_app.schemas.task import TaskCreate
from skill_app.schemas.workflow import WorkflowRunCreate
from skill_app.services import step_service, task_service, workflow_service


def test_step_and_workflow_failures_are_persisted_with_events():
    with SessionLocal() as db:
        task = task_service.create_task(
            db,
            TaskCreate(project_id="demo", title="Failure path", request="Exercise failure recording."),
        )
        run = task_service.create_workflow_run(
            db,
            task,
            WorkflowRunCreate(workflow_name="foundation_smoke"),
        )
        workflow_service.start_run(db, task, run)
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="failing_step",
            agent_name="failure_agent",
        )

        error = RuntimeError("planned failure")
        step_service.fail_step(db, task_id=task.id, step=step, error=error)
        workflow_service.fail_run(db, task, run, error)

        db.refresh(task)
        db.refresh(run)
        db.refresh(step)
        assert task.status == TaskStatus.FAILED
        assert run.status == RunStatus.FAILED
        assert step.status == RunStatus.FAILED
        assert step.error_type == "RuntimeError"
        assert step.error_message == "planned failure"
