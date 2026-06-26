from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.db.models import SkillRun
from skill_app.domain.errors import EntityNotFound, InvalidStateTransition
from skill_app.domain.status import RunStatus
from skill_app.schemas.skill import LoadedSkillPackage, SkillManifest


def create_skill_run(
    db: Session,
    *,
    task_id: str,
    workflow_run_id: str,
    package: LoadedSkillPackage,
) -> SkillRun:
    existing = get_skill_run_for_workflow(db, workflow_run_id)
    if existing is not None:
        raise InvalidStateTransition(
            "Workflow already has a skill run.",
            {"workflow_run_id": workflow_run_id, "skill_run_id": existing.id},
        )
    prior = (
        db.execute(
            select(SkillRun)
            .where(
                SkillRun.skill_name == package.manifest.name,
                SkillRun.skill_version == package.manifest.version,
            )
            .order_by(SkillRun.started_at.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )
    if prior is not None and prior.package_sha256 != package.package_sha256:
        raise InvalidStateTransition(
            "Skill package content changed without a version bump.",
            {
                "skill_name": package.manifest.name,
                "skill_version": package.manifest.version,
                "previous_sha256": prior.package_sha256,
                "current_sha256": package.package_sha256,
            },
        )
    run = SkillRun(
        id=str(uuid.uuid4()),
        task_id=task_id,
        workflow_run_id=workflow_run_id,
        skill_name=package.manifest.name,
        skill_version=package.manifest.version,
        package_sha256=package.package_sha256,
        instruction_sha256=package.instruction_sha256,
        manifest_snapshot=package.manifest.model_dump(mode="json"),
        instruction_snapshots={
            package.manifest.name: package.instructions.body,
            **{
                name: skill.body
                for name, skill in package.component_instructions.items()
            },
        },
        component_instruction_sha256s=package.component_instruction_sha256s,
        status=RunStatus.RUNNING,
    )
    db.add(run)
    db.commit()
    db.refresh(run)
    return run


def get_skill_run(db: Session, skill_run_id: str) -> SkillRun:
    run = db.get(SkillRun, skill_run_id)
    if run is None:
        raise EntityNotFound(
            f"Skill run not found: {skill_run_id}",
            {"skill_run_id": skill_run_id},
        )
    return run


def get_skill_run_for_workflow(db: Session, workflow_run_id: str) -> SkillRun | None:
    stmt = select(SkillRun).where(SkillRun.workflow_run_id == workflow_run_id)
    return db.execute(stmt).scalars().one_or_none()


def manifest_for_workflow(db: Session, workflow_run_id: str) -> tuple[SkillRun, SkillManifest] | None:
    run = get_skill_run_for_workflow(db, workflow_run_id)
    if run is None:
        return None
    return run, SkillManifest.model_validate(run.manifest_snapshot)


def list_skill_runs(db: Session, task_id: str) -> list[SkillRun]:
    stmt = (
        select(SkillRun)
        .where(SkillRun.task_id == task_id)
        .order_by(SkillRun.started_at.desc())
    )
    return list(db.execute(stmt).scalars().all())


def update_skill_run_status(
    db: Session,
    workflow_run_id: str,
    status: RunStatus,
) -> SkillRun | None:
    run = get_skill_run_for_workflow(db, workflow_run_id)
    if run is None:
        return None
    run.status = status
    if status in {RunStatus.SUCCEEDED, RunStatus.FAILED, RunStatus.CANCELLED}:
        run.finished_at = datetime.now()
    db.commit()
    db.refresh(run)
    return run
