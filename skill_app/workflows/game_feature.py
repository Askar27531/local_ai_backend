from __future__ import annotations

import difflib
import hashlib
import json

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt
from sqlalchemy import select

from skill_app.agents import game_feature as game_feature_agent
from skill_app.agents.base import AgentContext
from skill_app.agents.config_generator import normalized_json, render_config_explanation
from skill_app.agents.planner import PlannerAgent, render_plan_markdown, validate_plan
from skill_app.agents.review_agent import ReviewAgent
from skill_app.agents.test_agent import TestAgent
from skill_app.config import get_settings
from skill_app.db.database import SessionLocal
from skill_app.db.models import Artifact
from skill_app.domain.errors import CandidateValidationError, PlanValidationError
from skill_app.domain.status import RunStatus, TaskStatus
from skill_app.schemas.approval import ApprovalCreate
from skill_app.schemas.code_generation import RepairEvidencePack
from skill_app.schemas.game_config import NPCBehaviorConfig
from skill_app.schemas.game_feature import CandidateReview, CandidateSpec, CandidateValidation
from skill_app.schemas.plan import TaskPlan
from skill_app.schemas.test_report import TestExecutionReport
from skill_app.schemas.tool import ToolContext
from skill_app.services import (
    artifact_service,
    event_service,
    step_service,
    task_service,
    workflow_service,
    workspace_service,
)
from skill_app.workflows.state import ProductionWorkflowState

MAX_REPAIRS = 2


def _intake(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="intake",
            agent_name="system",
            input_data={"request": task.request, "task_type": task.task_type},
        )
        if task.status == TaskStatus.PLANNING:
            workflow_service.start_planning_run(db, task, run)
        else:
            workflow_service.start_run(db, task, run)
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"accepted": True},
        )
    return {
        "current_step": "intake",
        "candidate_artifact_ids": [],
        "code_patch_artifact_ids": [],
        "code_explanation_artifact_ids": [],
        "test_plan_artifact_ids": [],
        "test_patch_artifact_ids": [],
        "validation_artifact_ids": [],
        "config_artifact_ids": [],
        "review_artifact_ids": [],
        "repair_count": 0,
        "repair_summaries": [],
    }


def _plan(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        existing = _latest_plan_artifact(db, task.id)
        if existing is not None:
            plan = TaskPlan.model_validate_json(artifact_service.read_artifact_bytes(existing))
            return {
                "current_step": "plan",
                "plan": plan.model_dump(mode="json"),
                "plan_json_artifact_id": existing.id,
            }
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="plan",
            agent_name="planner",
            input_data={"request": task.request},
        )
        try:
            plan = PlannerAgent().execute(AgentContext(db=db, task=task, workflow_run=run, step_run=step))
            json_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="plan_json",
                logical_name="task-plan.json",
                content=plan.model_dump_json(indent=2),
                mime_type="application/json",
                metadata={"workflow_run_id": run.id, "workflow_name": "game_feature"},
                validation_status="valid",
            )
            json_id = json_artifact.id
            markdown_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="plan_markdown",
                logical_name="task-plan.md",
                content=render_plan_markdown(plan),
                mime_type="text/markdown; charset=utf-8",
                metadata={"workflow_run_id": run.id, "json_artifact_id": json_id},
                validation_status="valid",
            )
            markdown_id = markdown_artifact.id
            step_service.complete_step(
                db,
                task_id=task.id,
                step=step,
                output_data={
                    "plan_json_artifact_id": json_id,
                    "plan_markdown_artifact_id": markdown_id,
                },
            )
        except Exception as exc:
            step_service.fail_step(db, task_id=task.id, step=step, error=exc)
            raise
    return {
        "current_step": "plan",
        "plan": plan.model_dump(mode="json"),
        "plan_json_artifact_id": json_id,
        "plan_markdown_artifact_id": markdown_id,
        "artifact_ids": [json_id, markdown_id],
    }


def _validate_plan(state: ProductionWorkflowState) -> dict:
    plan = TaskPlan.model_validate(state["plan"])
    result = validate_plan(plan)
    if not result.valid:
        raise PlanValidationError("game_feature plan is invalid.", {"errors": result.errors})
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="validate_plan",
            agent_name="planner",
            input_data={"plan_json_artifact_id": state["plan_json_artifact_id"]},
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"valid": True, "step_count": len(plan.steps)},
        )
    return {"current_step": "validate_plan"}


def _route_plan(state: ProductionWorkflowState) -> str:
    plan = TaskPlan.model_validate(state["plan"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        if task.status == TaskStatus.RUNNING:
            return "prepare_workspace"
    return "request_plan_approval" if plan.requires_plan_approval else "activate_plan"


def _request_plan_approval(state: ProductionWorkflowState) -> dict:
    plan = TaskPlan.model_validate(state["plan"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="request_plan_approval",
            agent_name="planner",
        )
        approval = task_service.create_approval(
            db,
            task,
            ApprovalCreate(
                approval_type="plan_approval",
                risk_summary=f"game_feature plan risk: {plan.risk_level.value}.",
                diff_summary=f"Review plan artifact {state['plan_json_artifact_id']}.",
            ),
        )
        approval_id = approval.id
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": approval_id},
        )
    return {"current_step": "request_plan_approval", "approval_id": approval_id}


def _wait_for_plan_approval(state: ProductionWorkflowState) -> dict:
    return _approval_interrupt(state, "wait_for_plan_approval", "plan_approval")


def _activate_plan(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="activate_plan",
            agent_name="planner",
        )
        task_service.activate_planned_task(db, task, state["workflow_run_id"])
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"ready": True},
        )
    return {"current_step": "activate_plan"}


def _prepare_workspace(state: ProductionWorkflowState) -> dict:
    plan = TaskPlan.model_validate(state["plan"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="prepare_workspace",
            agent_name="system",
            input_data={"relevant_paths": plan.relevant_paths},
        )
        workspace = workspace_service.get_or_create_active_workspace(db, task)
        existing_paths = [
            path
            for path in plan.relevant_paths
            if (get_settings().project_root / path).is_file()
        ]
        if existing_paths:
            workspace_service.copy_files(db, workspace, existing_paths[:50])
        workspace_id = workspace.id
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"workspace_id": workspace_id, "copied_paths": existing_paths[:50]},
        )
    return {"current_step": "prepare_workspace", "workspace_id": workspace_id}


def _generate_candidate(state: ProductionWorkflowState) -> dict:
    return _write_candidate(state, repair=False)


def _repair_candidate(state: ProductionWorkflowState) -> dict:
    return _write_candidate(state, repair=True)


def _write_candidate(state: ProductionWorkflowState, *, repair: bool) -> dict:
    repair_count = state.get("repair_count", 0) + (1 if repair else 0)
    previous = CandidateSpec.model_validate(state["candidate"]) if state.get("candidate") else None
    validation = CandidateValidation.model_validate(state["validation"]) if state.get("validation") else None
    review = CandidateReview.model_validate(state["review"]) if state.get("review") else None
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        key = f"repair_{repair_count}" if repair else "generate_candidate"
        repair_evidence = (
            _build_repair_evidence(db, state, previous, validation, review)
            if repair and previous is not None
            else None
        )
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key=key,
            agent_name="code_generator",
            input_data={
                "repair_count": repair_count,
                "previous_artifact_id": state.get("current_candidate_artifact_id"),
                "validation_errors": validation.errors if validation else [],
                "review_findings": [item.model_dump() for item in review.findings] if review else [],
                "previous_sha256": (
                    repair_evidence.previous_sha256
                    if repair_evidence is not None
                    else None
                ),
                "test_plan_artifact_id": (
                    state.get("test_plan_artifact_ids", [])[-1]
                    if state.get("test_plan_artifact_ids")
                    else None
                ),
                "test_report_artifact_id": (
                    state.get("validation_artifact_ids", [])[-1]
                    if state.get("validation_artifact_ids")
                    else None
                ),
                "review_report_artifact_id": (
                    state.get("review_artifact_ids", [])[-1]
                    if state.get("review_artifact_ids")
                    else None
                ),
            },
        )
        candidate = game_feature_agent.candidate_generator.generate(
            task_id=task.id,
            request=task.request,
            task_type=task.task_type,
            repair_count=repair_count,
            previous_content=previous.content if previous else None,
            validation_errors=validation.errors if validation else [],
            review_findings=[item.model_dump() for item in review.findings] if review else [],
            repair_evidence=repair_evidence,
            context=AgentContext(db=db, task=task, workflow_run=run, step_run=step),
            relevant_paths=TaskPlan.model_validate(state["plan"]).relevant_paths,
        )
        path = workspace_service.resolve_workspace_path(workspace, candidate.relative_path)
        if len(candidate.content.splitlines()) > 400:
            raise CandidateValidationError(
                "Candidate exceeds maximum changed-line limit.",
                {"path": candidate.relative_path, "limit": 400},
            )
        if repair and previous is not None and path.is_file():
            expected = hashlib.sha256(previous.content.encode("utf-8")).hexdigest()
            actual = hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
            if actual != expected:
                raise CandidateValidationError(
                    "Workspace candidate changed before repair.",
                    {"path": candidate.relative_path, "expected": expected, "actual": actual},
                )
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(candidate.content, encoding="utf-8")
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="candidate_config" if candidate.kind == "game_config" else "candidate_code",
            logical_name=path.name,
            content=candidate.content,
            mime_type="application/yaml" if candidate.kind == "game_config" else "text/x-python",
            metadata={
                "workspace_id": workspace.id,
                "workspace_path": candidate.relative_path,
                "repair_count": repair_count,
                "summary": candidate.summary,
                "parent_candidate_artifact_id": (
                    state.get("current_candidate_artifact_id") if repair else None
                ),
                "previous_sha256": (
                    repair_evidence.previous_sha256
                    if repair_evidence is not None
                    else None
                ),
                "evidence_artifact_ids": {
                    "test_plan": (
                        state.get("test_plan_artifact_ids", [])[-1]
                        if state.get("test_plan_artifact_ids")
                        else None
                    ),
                    "test_report": (
                        state.get("validation_artifact_ids", [])[-1]
                        if state.get("validation_artifact_ids")
                        else None
                    ),
                    "review_report": (
                        state.get("review_artifact_ids", [])[-1]
                        if state.get("review_artifact_ids")
                        else None
                    ),
                },
            },
        )
        artifact_id = artifact.id
        code_patch_ids = list(state.get("code_patch_artifact_ids", []))
        code_explanation_ids = list(state.get("code_explanation_artifact_ids", []))
        if candidate.kind == "python_patch":
            source_path = get_settings().project_root / candidate.relative_path
            source_text = source_path.read_text(encoding="utf-8") if source_path.is_file() else ""
            patch_text = "".join(
                difflib.unified_diff(
                    source_text.splitlines(keepends=True),
                    candidate.content.splitlines(keepends=True),
                    fromfile=f"a/{candidate.relative_path}",
                    tofile=f"b/{candidate.relative_path}",
                )
            )
            patch_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="code_patch",
                logical_name="candidate.patch",
                content=patch_text,
                mime_type="text/x-diff; charset=utf-8",
                metadata={
                    "candidate_artifact_id": artifact_id,
                    "affected_files": [candidate.relative_path],
                    "repair_count": repair_count,
                    "parent_candidate_artifact_id": (
                        state.get("current_candidate_artifact_id") if repair else None
                    ),
                    "previous_sha256": (
                        repair_evidence.previous_sha256
                        if repair_evidence is not None
                        else None
                    ),
                },
                validation_status="valid",
            )
            explanation_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="code_explanation",
                logical_name="code-change.md",
                content=(
                    f"# Code change\n\n{candidate.summary}\n\n"
                    f"- Affected file: `{candidate.relative_path}`\n"
                    f"- Repair count: {repair_count}\n"
                    "- Source project modified: no\n"
                ),
                mime_type="text/markdown; charset=utf-8",
                metadata={"candidate_artifact_id": artifact_id},
                validation_status="valid",
            )
            code_patch_ids.append(patch_artifact.id)
            code_explanation_ids.append(explanation_artifact.id)
        summary = (
            (
                f"Repair {repair_count}: validation_errors="
                f"{validation.errors if validation else []}; "
                f"blocking_findings="
                f"{[finding.title for finding in review.findings if finding.blocking] if review else []}; "
                f"previous_sha256={repair_evidence.previous_sha256 if repair_evidence else None}."
            )
            if repair
            else "Initial candidate generated."
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"artifact_id": artifact_id, "candidate": candidate.model_dump(mode="json")},
        )
    return {
        "current_step": key,
        "candidate": candidate.model_dump(mode="json"),
        "current_candidate_artifact_id": artifact_id,
        "candidate_artifact_ids": [*state.get("candidate_artifact_ids", []), artifact_id],
        "code_patch_artifact_ids": code_patch_ids,
        "code_explanation_artifact_ids": code_explanation_ids,
        "repair_count": repair_count,
        "repair_summaries": [*state.get("repair_summaries", []), summary],
    }


def _build_repair_evidence(
    db,
    state: ProductionWorkflowState,
    previous: CandidateSpec,
    validation: CandidateValidation | None,
    review: CandidateReview | None,
) -> RepairEvidencePack:
    plan = TaskPlan.model_validate(state["plan"])
    criteria = [
        *plan.global_acceptance_criteria,
        *[
            criterion
            for plan_step in plan.steps
            for criterion in plan_step.acceptance_criteria
        ],
    ]
    test_report = (
        validation.details.get("test_execution", {})
        if validation is not None
        else {}
    )
    return RepairEvidencePack(
        previous_path=previous.relative_path,
        previous_content=previous.content,
        previous_sha256=hashlib.sha256(previous.content.encode("utf-8")).hexdigest(),
        validation_errors=validation.errors if validation is not None else [],
        test_plan=_read_latest_json_artifact(
            db,
            state.get("test_plan_artifact_ids", []),
        ),
        test_content=_read_latest_text_artifact(
            db,
            state.get("test_patch_artifact_ids", []),
        ),
        test_report=test_report,
        review_findings=(
            [finding.model_dump(mode="json") for finding in review.findings]
            if review is not None
            else []
        ),
        acceptance_criteria=criteria,
        repair_history=list(state.get("repair_summaries", [])),
    )


def _validate_candidate(state: ProductionWorkflowState) -> dict:
    candidate = CandidateSpec.model_validate(state["candidate"])
    result = game_feature_agent.validate_candidate(candidate)
    attempt = state.get("repair_count", 0)
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key=f"validate_candidate_{attempt}",
            agent_name="test_agent",
            input_data={"candidate_artifact_id": state["current_candidate_artifact_id"]},
        )
        report_type = "config_validation_report" if candidate.kind == "game_config" else "test_report"
        test_plan_ids = list(state.get("test_plan_artifact_ids", []))
        test_patch_ids = list(state.get("test_patch_artifact_ids", []))
        if candidate.kind == "python_patch":
            task_plan = TaskPlan.model_validate(state["plan"])
            acceptance_criteria = [
                *task_plan.global_acceptance_criteria,
                *[
                    criterion
                    for plan_step in task_plan.steps
                    for criterion in plan_step.acceptance_criteria
                ],
            ]
            workspace = workspace_service.get_workspace(db, state["workspace_id"])
            context = ToolContext(
                task_id=task.id,
                workflow_run_id=state["workflow_run_id"],
                step_run_id=step.id,
                agent_name="test_agent",
                project_root=str(get_settings().project_root),
                workspace_root=workspace.workspace_root,
                permissions=["run_python_compile", "run_pytest", "write_workspace_file"],
                timeout_seconds=get_settings().tool_timeout_seconds,
            )
            plan, execution, test_content = TestAgent().execute(
                candidate=candidate,
                workspace_root=workspace_service.resolve_workspace_path(workspace, "."),
                context=context,
                static_validation=result,
                agent_context=AgentContext(db=db, task=task, workflow_run=run, step_run=step),
                request=task.request,
                acceptance_criteria=acceptance_criteria,
            )
            result = CandidateValidation(
                valid=execution.passed,
                errors=execution.errors,
                checks=[*result.checks, "python_compile", "generated_pytest"],
                details={"test_execution": execution.model_dump(mode="json")},
            )
            plan_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="test_plan",
                logical_name="generated-test-plan.json",
                content=plan.model_dump_json(indent=2),
                mime_type="application/json",
                metadata={"candidate_artifact_id": state["current_candidate_artifact_id"]},
                validation_status="valid",
            )
            test_patch_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="test_patch",
                logical_name="generated-test.py",
                content=test_content,
                mime_type="text/x-python",
                metadata={
                    "candidate_artifact_id": state["current_candidate_artifact_id"],
                    "workspace_path": plan.test_path,
                },
                validation_status="valid",
            )
            test_plan_ids.append(plan_artifact.id)
            test_patch_ids.append(test_patch_artifact.id)
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type=report_type,
            logical_name="candidate-validation.json",
            content=result.model_dump_json(indent=2),
            mime_type="application/json",
            metadata={"candidate_artifact_id": state["current_candidate_artifact_id"], "attempt": attempt},
            validation_status="valid" if result.valid else "invalid",
        )
        report_id = artifact.id
        config_artifact_ids = list(state.get("config_artifact_ids", []))
        normalized_id = state.get("normalized_config_artifact_id")
        explanation_id = state.get("config_explanation_artifact_id")
        if candidate.kind == "game_config" and result.valid:
            config = NPCBehaviorConfig.model_validate(result.details["normalized_config"])
            normalized_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="normalized_config",
                logical_name="npc_behavior.json",
                content=normalized_json(config),
                mime_type="application/json",
                metadata={
                    "source_candidate_artifact_id": state["current_candidate_artifact_id"],
                    "schema_version": config.schema_version,
                },
                validation_status="valid",
            )
            normalized_id = normalized_artifact.id
            explanation_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="config_explanation",
                logical_name="npc_behavior_design.md",
                content=render_config_explanation(config, task.request),
                mime_type="text/markdown; charset=utf-8",
                metadata={
                    "source_candidate_artifact_id": state["current_candidate_artifact_id"],
                    "requirement": task.request,
                },
                validation_status="valid",
            )
            explanation_id = explanation_artifact.id
            config_artifact_ids.extend([normalized_id, explanation_id])
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"valid": result.valid, "report_artifact_id": report_id},
        )
    return {
        "current_step": f"validate_candidate_{attempt}",
        "validation": result.model_dump(mode="json"),
        "validation_artifact_ids": [*state.get("validation_artifact_ids", []), report_id],
        "test_plan_artifact_ids": test_plan_ids,
        "test_patch_artifact_ids": test_patch_ids,
        "config_artifact_ids": config_artifact_ids,
        "normalized_config_artifact_id": normalized_id,
        "config_explanation_artifact_id": explanation_id,
    }


def _route_validation(state: ProductionWorkflowState) -> str:
    result = CandidateValidation.model_validate(state["validation"])
    if result.valid:
        return "review"
    return "repair" if state.get("repair_count", 0) < MAX_REPAIRS else "manual_takeover"


def _review_candidate(state: ProductionWorkflowState) -> dict:
    candidate = CandidateSpec.model_validate(state["candidate"])
    validation = CandidateValidation.model_validate(state["validation"])
    attempt = state.get("repair_count", 0)
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key=f"review_candidate_{attempt}",
            agent_name="review_agent",
            input_data={"candidate_artifact_id": state["current_candidate_artifact_id"]},
        )
        if candidate.kind == "python_patch":
            workspace = workspace_service.get_workspace(db, state["workspace_id"])
            diff, _ = workspace_service.build_diff(db, workspace, create_artifact=False)
            execution = TestExecutionReport.model_validate(validation.details["test_execution"])
            plan = TaskPlan.model_validate(state["plan"])
            criteria = [
                *plan.global_acceptance_criteria,
                *[
                    criterion
                    for plan_step in plan.steps
                    for criterion in plan_step.acceptance_criteria
                ],
            ]
            test_plan = _read_latest_json_artifact(
                db,
                state.get("test_plan_artifact_ids", []),
            )
            test_content = _read_latest_text_artifact(
                db,
                state.get("test_patch_artifact_ids", []),
            )
            code_review_report = ReviewAgent().review(
                candidate=candidate,
                diff=diff.content,
                test_report=execution,
                acceptance_criteria=criteria,
                config_consistent=True,
                test_report_artifact_ids=state.get("validation_artifact_ids", []),
                agent_context=AgentContext(db=db, task=task, workflow_run=run, step_run=step),
                request=task.request,
                generated_test_plan=test_plan,
                generated_test_content=test_content,
            )
            review = CandidateReview(
                approved=code_review_report.approved,
                summary=code_review_report.summary,
                findings=code_review_report.findings,
            )
        else:
            code_review_report = None
            review = game_feature_agent.review_candidate(candidate, validation)
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="review_report",
            logical_name="candidate-review.json",
            content=(
                code_review_report.model_dump_json(indent=2)
                if code_review_report is not None
                else review.model_dump_json(indent=2)
            ),
            mime_type="application/json",
            metadata={
                "candidate_artifact_id": state["current_candidate_artifact_id"],
                "attempt": attempt,
                "test_report_artifact_ids": state.get("validation_artifact_ids", []),
            },
            validation_status="valid" if review.approved else "invalid",
        )
        review_id = artifact.id
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approved": review.approved, "review_artifact_id": review_id},
        )
    return {
        "current_step": f"review_candidate_{attempt}",
        "review": review.model_dump(mode="json"),
        "review_artifact_ids": [*state.get("review_artifact_ids", []), review_id],
    }


def _read_latest_json_artifact(db, artifact_ids: list[str]) -> dict:
    if not artifact_ids:
        return {}
    artifact = artifact_service.get_artifact(db, artifact_ids[-1])
    return json.loads(artifact_service.read_artifact_bytes(artifact).decode("utf-8"))


def _read_latest_text_artifact(db, artifact_ids: list[str]) -> str:
    if not artifact_ids:
        return ""
    artifact = artifact_service.get_artifact(db, artifact_ids[-1])
    return artifact_service.read_artifact_bytes(artifact).decode("utf-8")


def _route_review(state: ProductionWorkflowState) -> str:
    review = CandidateReview.model_validate(state["review"])
    if review.approved:
        return "request_artifact_approval"
    return "repair" if state.get("repair_count", 0) < MAX_REPAIRS else "manual_takeover"


def _manual_takeover(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        event_service.record_event(
            db,
            task_id=state["task_id"],
            workflow_run_id=state["workflow_run_id"],
            event_type="manual_takeover_required",
            actor_type="system",
            actor_name="game_feature_workflow",
            payload={
                "repair_count": state.get("repair_count", 0),
                "candidate_artifact_ids": state.get("candidate_artifact_ids", []),
                "validation": state.get("validation"),
                "review": state.get("review"),
            },
        )
        db.commit()
    raise CandidateValidationError(
        "Candidate exceeded the automatic repair limit.",
        {
            "repair_count": state.get("repair_count", 0),
            "candidate_artifact_ids": state.get("candidate_artifact_ids", []),
        },
    )


def _request_artifact_approval(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="request_artifact_approval",
            agent_name="review_agent",
        )
        approval = task_service.create_approval(
            db,
            task,
            ApprovalCreate(
                artifact_id=state["current_candidate_artifact_id"],
                approval_type="artifact_review",
                risk_summary=f"Candidate repairs: {state.get('repair_count', 0)}.",
                diff_summary=f"Candidate artifact: {state['current_candidate_artifact_id']}.",
            ),
        )
        approval_id = approval.id
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": approval_id},
        )
    return {"current_step": "request_artifact_approval", "approval_id": approval_id}


def _wait_for_artifact_approval(state: ProductionWorkflowState) -> dict:
    return _approval_interrupt(state, "wait_for_artifact_approval", "artifact_review")


def _deliver(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="deliver",
            agent_name="system",
        )
        diff, diff_artifact_id = workspace_service.build_diff(db, workspace)
        config_diff_id = state.get("config_diff_artifact_id")
        candidate = CandidateSpec.model_validate(state["candidate"])
        config_artifact_ids = list(state.get("config_artifact_ids", []))
        if candidate.kind == "game_config":
            config_diff = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="config_diff",
                logical_name="npc_behavior.diff",
                content=diff.content,
                mime_type="text/x-diff; charset=utf-8",
                metadata={
                    "candidate_artifact_id": state["current_candidate_artifact_id"],
                    "changed_paths": diff.changed_paths,
                },
                validation_status="valid",
                approval_status="approved",
            )
            config_diff_id = config_diff.id
            config_artifact_ids.append(config_diff_id)
        manifest = {
            "schema_version": 1,
            "task_id": task.id,
            "workflow_run_id": state["workflow_run_id"],
            "workspace_id": workspace.id,
            "candidate_artifact_ids": state.get("candidate_artifact_ids", []),
            "code_patch_artifact_ids": state.get("code_patch_artifact_ids", []),
            "code_explanation_artifact_ids": state.get("code_explanation_artifact_ids", []),
            "final_candidate_artifact_id": state["current_candidate_artifact_id"],
            "diff_artifact_id": diff_artifact_id,
            "test_report_artifact_ids": state.get("validation_artifact_ids", []),
            "test_plan_artifact_ids": state.get("test_plan_artifact_ids", []),
            "test_patch_artifact_ids": state.get("test_patch_artifact_ids", []),
            "review_report_artifact_ids": state.get("review_artifact_ids", []),
            "config_artifact_ids": config_artifact_ids,
            "normalized_config_artifact_id": state.get("normalized_config_artifact_id"),
            "config_explanation_artifact_id": state.get("config_explanation_artifact_id"),
            "config_diff_artifact_id": config_diff_id,
            "repair_count": state.get("repair_count", 0),
            "source_project_modified": False,
        }
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="delivery_manifest",
            logical_name="delivery-manifest.json",
            content=json.dumps(manifest, ensure_ascii=False, indent=2),
            mime_type="application/json",
            metadata={"diff_artifact_id": diff_artifact_id},
            validation_status="valid",
            approval_status="approved",
        )
        manifest_id = artifact.id
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"manifest_artifact_id": manifest_id, "diff_artifact_id": diff_artifact_id},
        )
    return {
        "current_step": "deliver",
        "delivery_manifest_artifact_id": manifest_id,
        "config_artifact_ids": config_artifact_ids,
        "config_diff_artifact_id": config_diff_id,
        "artifact_ids": [
            *state.get("artifact_ids", []),
            *(state.get("candidate_artifact_ids", [])),
            *(state.get("code_patch_artifact_ids", [])),
            *(state.get("code_explanation_artifact_ids", [])),
            *(state.get("validation_artifact_ids", [])),
            *(state.get("test_plan_artifact_ids", [])),
            *(state.get("test_patch_artifact_ids", [])),
            *(state.get("review_artifact_ids", [])),
            *config_artifact_ids,
            diff_artifact_id,
            manifest_id,
        ],
    }


def _finish(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="finish",
            agent_name="system",
            input_data={"manifest_artifact_id": state["delivery_manifest_artifact_id"]},
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"completed": True},
        )
    return {
        "current_step": "finish",
        "final_summary": "game_feature delivery completed without modifying the source project.",
    }


def _approval_interrupt(
    state: ProductionWorkflowState,
    step_key: str,
    approval_type: str,
) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key=step_key,
            agent_name="approval_gate",
            input_data={"approval_id": state["approval_id"]},
        )
        if step.status != RunStatus.PAUSED:
            step_service.pause_step(
                db,
                task_id=task.id,
                step=step,
                output_data={"approval_id": state["approval_id"]},
            )
    decision = interrupt({"approval_id": state["approval_id"], "type": approval_type})
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.get_latest_step(db, state["workflow_run_id"], step_key)
        if step is None:
            raise RuntimeError("Approval step disappeared during resume.")
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": state["approval_id"], "decision": decision},
        )
    return {"current_step": step_key, "approval_decision": decision}


def _latest_plan_artifact(db, task_id: str) -> Artifact | None:
    return (
        db.execute(
            select(Artifact)
            .where(
                Artifact.task_id == task_id,
                Artifact.artifact_type == "plan_json",
                Artifact.validation_status == "valid",
            )
            .order_by(Artifact.version.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def build_game_feature_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(ProductionWorkflowState)
    graph.add_node("intake", _intake)
    graph.add_node("plan", _plan)
    graph.add_node("validate_plan", _validate_plan)
    graph.add_node("request_plan_approval", _request_plan_approval)
    graph.add_node("wait_for_plan_approval", _wait_for_plan_approval)
    graph.add_node("activate_plan", _activate_plan)
    graph.add_node("prepare_workspace", _prepare_workspace)
    graph.add_node("generate_candidate", _generate_candidate)
    graph.add_node("validate_candidate", _validate_candidate)
    graph.add_node("repair", _repair_candidate)
    graph.add_node("review", _review_candidate)
    graph.add_node("manual_takeover", _manual_takeover)
    graph.add_node("request_artifact_approval", _request_artifact_approval)
    graph.add_node("wait_for_artifact_approval", _wait_for_artifact_approval)
    graph.add_node("deliver", _deliver)
    graph.add_node("finish", _finish)

    graph.add_edge(START, "intake")
    graph.add_edge("intake", "plan")
    graph.add_edge("plan", "validate_plan")
    graph.add_conditional_edges(
        "validate_plan",
        _route_plan,
        {
            "request_plan_approval": "request_plan_approval",
            "activate_plan": "activate_plan",
            "prepare_workspace": "prepare_workspace",
        },
    )
    graph.add_edge("request_plan_approval", "wait_for_plan_approval")
    graph.add_edge("wait_for_plan_approval", "prepare_workspace")
    graph.add_edge("activate_plan", "prepare_workspace")
    graph.add_edge("prepare_workspace", "generate_candidate")
    graph.add_edge("generate_candidate", "validate_candidate")
    graph.add_conditional_edges(
        "validate_candidate",
        _route_validation,
        {"review": "review", "repair": "repair", "manual_takeover": "manual_takeover"},
    )
    graph.add_edge("repair", "validate_candidate")
    graph.add_conditional_edges(
        "review",
        _route_review,
        {
            "request_artifact_approval": "request_artifact_approval",
            "repair": "repair",
            "manual_takeover": "manual_takeover",
        },
    )
    graph.add_edge("request_artifact_approval", "wait_for_artifact_approval")
    graph.add_edge("wait_for_artifact_approval", "deliver")
    graph.add_edge("deliver", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer, name="game_feature")
