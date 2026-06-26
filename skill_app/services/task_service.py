from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.db.models import AgentTask, ApprovalRequest, Artifact, WorkflowRun
from skill_app.domain.errors import EntityNotFound, InvalidStateTransition
from skill_app.domain.status import ApprovalStatus, RunStatus, TaskStatus
from skill_app.domain.transitions import task_status, transition_task
from skill_app.schemas.approval import ApprovalCreate, ApprovalDecision
from skill_app.schemas.task import TaskCreate
from skill_app.schemas.workflow import WorkflowRunCreate
from skill_app.services import event_service

PLAN_APPROVAL_TYPES = frozenset({"plan", "plan_review", "plan_approval"})


def _commit(db: Session) -> None:
    try:
        db.commit()
    except Exception:
        db.rollback()
        raise


def _record_transition(
    db: Session,
    task: AgentTask,
    target: TaskStatus,
    *,
    actor_type: str,
    actor_name: str,
    reason: str,
    workflow_run_id: str | None = None,
) -> bool:
    transition = transition_task(task, target)
    if transition is None:
        return False
    current, updated = transition
    event_service.record_status_change(
        db,
        task_id=task.id,
        workflow_run_id=workflow_run_id,
        current=current.value,
        target=updated.value,
        actor_type=actor_type,
        actor_name=actor_name,
        reason=reason,
    )
    return True


def create_task(db: Session, payload: TaskCreate) -> AgentTask:
    task = AgentTask(id=str(uuid.uuid4()), **payload.model_dump(mode="json"))
    db.add(task)
    event_service.record_event(
        db,
        task_id=task.id,
        event_type="task_created",
        actor_type="user",
        actor_name=payload.created_by,
        payload={
            "project_id": payload.project_id,
            "task_type": payload.task_type,
            "risk_level": payload.risk_level.value,
        },
    )
    _commit(db)
    db.refresh(task)
    return task


def list_tasks(db: Session, limit: int = 100) -> list[AgentTask]:
    stmt = select(AgentTask).order_by(AgentTask.created_at.desc()).limit(limit)
    return list(db.execute(stmt).scalars().all())


def get_task(db: Session, task_id: str) -> AgentTask:
    task = db.get(AgentTask, task_id)
    if task is None:
        raise EntityNotFound(f"Task not found: {task_id}", {"task_id": task_id})
    return task


def cancel_task(db: Session, task: AgentTask, actor_name: str = "local-user") -> AgentTask:
    changed = _record_transition(
        db,
        task,
        TaskStatus.CANCELLED,
        actor_type="user",
        actor_name=actor_name,
        reason="task_cancelled",
    )
    if not changed:
        return task

    event_service.record_event(
        db,
        task_id=task.id,
        event_type="task_cancelled",
        actor_type="user",
        actor_name=actor_name,
    )
    _commit(db)
    db.refresh(task)
    return task


def activate_planned_task(db: Session, task: AgentTask, workflow_run_id: str) -> AgentTask:
    changed = _record_transition(
        db,
        task,
        TaskStatus.RUNNING,
        actor_type="system",
        actor_name="planner",
        reason="plan_ready_for_execution",
        workflow_run_id=workflow_run_id,
    )
    if changed:
        event_service.record_event(
            db,
            task_id=task.id,
            workflow_run_id=workflow_run_id,
            event_type="plan_activated",
            actor_type="agent",
            actor_name="planner",
        )
        _commit(db)
        db.refresh(task)
    return task


def create_workflow_run(db: Session, task: AgentTask, payload: WorkflowRunCreate) -> WorkflowRun:
    if task_status(task) != TaskStatus.DRAFT:
        raise InvalidStateTransition(
            "A workflow run can only be created for a draft task.",
            {"task_id": task.id, "current": task_status(task).value},
        )

    run = WorkflowRun(
        id=str(uuid.uuid4()),
        task_id=task.id,
        workflow_name=payload.workflow_name,
        graph_version=payload.graph_version,
        status=RunStatus.PENDING,
    )
    task.workflow_name = payload.workflow_name
    db.add(run)
    _record_transition(
        db,
        task,
        TaskStatus.PLANNING,
        actor_type="system",
        actor_name="workflow_service",
        reason="workflow_run_created",
        workflow_run_id=run.id,
    )
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_run_created",
        actor_type="system",
        actor_name="workflow_service",
        payload={
            "workflow_name": payload.workflow_name,
            "graph_version": payload.graph_version,
        },
    )
    _commit(db)
    db.refresh(run)
    return run


def create_execution_workflow_run(
    db: Session,
    task: AgentTask,
    payload: WorkflowRunCreate,
) -> WorkflowRun:
    if task_status(task) != TaskStatus.RUNNING:
        raise InvalidStateTransition(
            "An execution workflow requires a running planned task.",
            {"task_id": task.id, "current": task_status(task).value},
        )
    active = (
        db.execute(
            select(WorkflowRun).where(
                WorkflowRun.task_id == task.id,
                WorkflowRun.status.in_([RunStatus.PENDING, RunStatus.RUNNING, RunStatus.PAUSED]),
            )
        )
        .scalars()
        .first()
    )
    if active is not None:
        raise InvalidStateTransition(
            "Task already has an active workflow run.",
            {"task_id": task.id, "workflow_run_id": active.id},
        )
    plan_artifact = (
        db.execute(
            select(Artifact)
            .where(
                Artifact.task_id == task.id,
                Artifact.artifact_type == "plan_json",
                Artifact.validation_status == "valid",
            )
            .order_by(Artifact.version.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if plan_artifact is None:
        raise InvalidStateTransition(
            "Execution workflow requires a validated plan artifact.",
            {"task_id": task.id},
        )
    run = WorkflowRun(
        id=str(uuid.uuid4()),
        task_id=task.id,
        workflow_name=payload.workflow_name,
        graph_version=payload.graph_version,
        status=RunStatus.PENDING,
    )
    task.workflow_name = payload.workflow_name
    db.add(run)
    event_service.record_event(
        db,
        task_id=task.id,
        workflow_run_id=run.id,
        event_type="workflow_run_created",
        actor_type="system",
        actor_name="workflow_service",
        payload={
            "workflow_name": payload.workflow_name,
            "graph_version": payload.graph_version,
            "phase": "execution",
            "plan_artifact_id": plan_artifact.id,
        },
    )
    _commit(db)
    db.refresh(run)
    return run


def list_workflow_runs(db: Session, task_id: str) -> list[WorkflowRun]:
    stmt = (
        select(WorkflowRun)
        .where(WorkflowRun.task_id == task_id)
        .order_by(WorkflowRun.started_at.desc(), WorkflowRun.id.desc())
    )
    return list(db.execute(stmt).scalars().all())


def create_approval(db: Session, task: AgentTask, payload: ApprovalCreate) -> ApprovalRequest:
    is_plan_approval = payload.approval_type in PLAN_APPROVAL_TYPES
    target = TaskStatus.AWAITING_PLAN_APPROVAL if is_plan_approval else TaskStatus.AWAITING_ARTIFACT_APPROVAL
    expected = TaskStatus.PLANNING if is_plan_approval else TaskStatus.RUNNING
    current = task_status(task)
    if current != expected:
        raise InvalidStateTransition(
            f"{payload.approval_type} cannot be requested while task is {current.value}.",
            {
                "task_id": task.id,
                "approval_type": payload.approval_type,
                "current": current.value,
                "expected": expected.value,
            },
        )
    if not is_plan_approval and payload.artifact_id is None:
        raise InvalidStateTransition(
            "Artifact approval requires artifact_id.",
            {"task_id": task.id, "approval_type": payload.approval_type},
        )
    if is_plan_approval and payload.artifact_id is not None:
        raise InvalidStateTransition(
            "Plan approval cannot reference an artifact.",
            {"task_id": task.id, "artifact_id": payload.artifact_id},
        )
    if payload.artifact_id is not None:
        artifact = db.get(Artifact, payload.artifact_id)
        if artifact is None:
            raise EntityNotFound(
                f"Artifact not found: {payload.artifact_id}",
                {"artifact_id": payload.artifact_id},
            )
        if artifact.task_id != task.id:
            raise InvalidStateTransition(
                "Artifact does not belong to the task requesting approval.",
                {
                    "task_id": task.id,
                    "artifact_id": artifact.id,
                    "artifact_task_id": artifact.task_id,
                },
            )

    approval = ApprovalRequest(
        id=str(uuid.uuid4()),
        task_id=task.id,
        status=ApprovalStatus.PENDING,
        **payload.model_dump(),
    )
    db.add(approval)
    _record_transition(
        db,
        task,
        target,
        actor_type="system",
        actor_name="approval_service",
        reason="approval_requested",
    )
    event_service.record_event(
        db,
        task_id=task.id,
        event_type="approval_requested",
        actor_type="system",
        actor_name="approval_service",
        payload={
            "approval_id": approval.id,
            "approval_type": approval.approval_type,
            "artifact_id": approval.artifact_id,
        },
    )
    _commit(db)
    db.refresh(approval)
    return approval


def resolve_approval(db: Session, approval_id: str, decision: ApprovalDecision) -> ApprovalRequest:
    approval = db.get(ApprovalRequest, approval_id)
    if approval is None:
        raise EntityNotFound(f"Approval not found: {approval_id}", {"approval_id": approval_id})
    if approval.status != ApprovalStatus.PENDING:
        raise InvalidStateTransition(
            "Approval has already been resolved.",
            {"approval_id": approval_id, "current": str(approval.status)},
        )

    task = get_task(db, approval.task_id)
    approval.status = ApprovalStatus.APPROVED if decision.approved else ApprovalStatus.REJECTED
    approval.resolved_at = datetime.now()
    approval.resolved_by = decision.resolved_by
    approval.comment = decision.comment
    if approval.artifact_id is not None:
        artifact = db.get(Artifact, approval.artifact_id)
        if artifact is not None:
            artifact.approval_status = approval.status.value

    target = TaskStatus.RUNNING if decision.approved else TaskStatus.CANCELLED
    _record_transition(
        db,
        task,
        target,
        actor_type="user",
        actor_name=decision.resolved_by,
        reason="approval_approved" if decision.approved else "approval_rejected",
    )
    event_service.record_event(
        db,
        task_id=task.id,
        event_type="approval_resolved",
        actor_type="user",
        actor_name=decision.resolved_by,
        payload={
            "approval_id": approval.id,
            "approval_type": approval.approval_type,
            "decision": approval.status,
            "comment": approval.comment,
        },
    )
    _commit(db)
    db.refresh(approval)
    return approval
