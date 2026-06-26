from __future__ import annotations

from sqlalchemy.orm import Session

from skill_app.domain.errors import ConfigurationError
from skill_app.schemas.skill import SkillStartRequest
from skill_app.services import skill_service
from skill_app.services.skill_loader import load_skill_package
from skill_app.workflows.runtime import prepare_workflow_start, start_workflow


def start_skill(
    db: Session,
    *,
    task_id: str,
    skill_name: str,
    request: SkillStartRequest,
):
    package = load_skill_package(skill_name)
    manifest = package.manifest
    if manifest.status != "active":
        raise ConfigurationError(
            "Only active skills can be started.",
            {"skill_name": skill_name, "status": manifest.status},
        )
    if request.version is not None and request.version != manifest.version:
        raise ConfigurationError(
            "Requested skill version does not match the installed package.",
            {
                "skill_name": skill_name,
                "requested": request.version,
                "installed": manifest.version,
            },
        )
    if manifest.workflow is None:
        raise ConfigurationError(
            "Skill is not independently executable.",
            {"skill_name": skill_name, "kind": manifest.kind},
        )
    return start_workflow(
        db,
        task_id,
        manifest.workflow.name,
        manifest.workflow.version,
        skill_package=package,
    )


def prepare_skill_start(
    db: Session,
    *,
    task_id: str,
    skill_name: str,
    request: SkillStartRequest,
):
    package = load_skill_package(skill_name)
    manifest = package.manifest
    if manifest.status != "active":
        raise ConfigurationError(
            "Only active skills can be started.",
            {"skill_name": skill_name, "status": manifest.status},
        )
    if request.version is not None and request.version != manifest.version:
        raise ConfigurationError(
            "Requested skill version does not match the installed package.",
            {
                "skill_name": skill_name,
                "requested": request.version,
                "installed": manifest.version,
            },
        )
    if manifest.workflow is None:
        raise ConfigurationError(
            "Skill is not independently executable.",
            {"skill_name": skill_name, "kind": manifest.kind},
        )
    return prepare_workflow_start(
        db,
        task_id,
        manifest.workflow.name,
        manifest.workflow.version,
        skill_package=package,
    )


def list_task_skill_runs(db: Session, task_id: str):
    return skill_service.list_skill_runs(db, task_id)
