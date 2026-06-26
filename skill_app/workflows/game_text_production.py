from __future__ import annotations

import json
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from skill_app.agents.base import AgentContext
from skill_app.agents.game_text import (
    GameTextWriterAgent,
    LoreReviewAgent,
    normalize_candidate_set,
    validate_game_text,
)
from skill_app.db.database import SessionLocal
from skill_app.domain.errors import CandidateValidationError
from skill_app.domain.status import RunStatus
from skill_app.schemas.approval import ApprovalCreate
from skill_app.schemas.project_workflows import (
    GameTextCandidate,
    GameTextCandidateSet,
    GameTextDeliveryManifest,
    GameTextReviewResult,
    GameTextValidationResult,
)
from skill_app.schemas.skill import SkillManifest, SkillStageBinding
from skill_app.services import (
    artifact_service,
    event_service,
    step_service,
    task_service,
    workflow_service,
    workspace_service,
)
from skill_app.services.skill_registry import run_skill_validators
from skill_app.workflows.state import ProductionWorkflowState

MAX_REPAIRS = 2


def _initialize(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=run.id,
            step_key="initialize_game_text", agent_name="system",
        )
        workflow_service.start_run(db, task, run)
        workspace = workspace_service.get_or_create_active_workspace(db, task)
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={"workspace_id": workspace.id, "target_root": "game_docs"},
        )
        workspace_id = workspace.id
    return {
        "current_step": "initialize_game_text",
        "workspace_id": workspace_id,
        "repair_count": 0,
        "candidate_artifact_ids": [],
        "validation_artifact_ids": [],
        "review_artifact_ids": [],
        "repair_summaries": [],
    }


def _generate(state: ProductionWorkflowState) -> dict:
    previous = (
        _candidate_set_from_state(state)
        if state.get("game_text_candidate")
        else None
    )
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        key = "generate_game_text" if previous is None else f"repair_game_text_{state.get('repair_count', 0)}"
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=run.id,
            step_key=key, agent_name="game_text_writer",
        )
        evidence = {
            "validation": state.get("game_text_validation"),
            "review": state.get("game_text_review"),
            "repair_history": state.get("repair_summaries", []),
        }
        manifest = _skill_manifest(state)
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        if manifest is not None:
            run_skill_validators(
                manifest.validators.pre,
                None,
                {
                    "task_status": task.status,
                    "workspace_status": workspace.status,
                },
            )
        writer_stage = _stage(state, "writer")
        candidate = GameTextWriterAgent().generate(
            AgentContext(db=db, task=task, workflow_run=run, step_run=step),
            request=task.request,
            repair_count=state.get("repair_count", 0),
            previous=previous,
            evidence=evidence,
            instruction_text=_instruction(state, writer_stage.skill) if writer_stage else None,
            profile_name=writer_stage.profile if writer_stage else None,
            output_schema_name=(
                writer_stage.output_schema
                if writer_stage
                else "game_text_candidate_set"
            ),
            output_validators=manifest.validators.output if manifest else [],
            limits=manifest.limits if manifest else None,
        )
        candidate_set = normalize_candidate_set(candidate)
        existing_paths = [
            item.path
            for item in candidate_set.candidates
            if (Path(workspace.source_root) / item.path).is_file()
            and item.path not in workspace.copied_paths
        ]
        if existing_paths:
            workspace_service.copy_files(db, workspace, existing_paths)
        file_artifact_ids = []
        for item in candidate_set.candidates:
            target = workspace_service.resolve_workspace_path(workspace, item.path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(item.content, encoding="utf-8")
            artifact = artifact_service.create_text_artifact(
                db, task, step_run_id=step.id, artifact_type="game_text_candidate",
                logical_name=Path(item.path).name, content=item.content,
                mime_type="text/markdown; charset=utf-8",
                metadata={
                    "workspace_id": workspace.id,
                    "workspace_path": item.path,
                    "repair_count": state.get("repair_count", 0),
                    "source_paths": item.source_paths,
                    "affected_npcs": item.affected_npcs,
                    "intended_unlock_level": item.intended_unlock_level,
                    "parent_candidate_artifact_id": (
                        state.get("current_candidate_artifact_id") if previous else None
                    ),
                },
                approval_status="pending",
            )
            file_artifact_ids.append(artifact.id)
        set_artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="game_text_candidate",
            logical_name="game-text-candidate-set.json",
            content=candidate_set.model_dump_json(indent=2),
            mime_type="application/json",
            metadata={
                "workspace_id": workspace.id,
                "workspace_paths": [item.path for item in candidate_set.candidates],
                "candidate_artifact_ids": file_artifact_ids,
                "repair_count": state.get("repair_count", 0),
                "parent_candidate_artifact_id": (
                    state.get("current_candidate_artifact_id") if previous else None
                ),
            },
            approval_status="pending",
        )
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={
                "candidate_artifact_id": set_artifact.id,
                "candidate_artifact_ids": file_artifact_ids,
                "paths": [item.path for item in candidate_set.candidates],
            },
        )
        candidate_artifact_id = set_artifact.id
    return {
        "current_step": key,
        "game_text_candidate": candidate_set.model_dump(mode="json"),
        "current_candidate_artifact_id": candidate_artifact_id,
        "current_candidate_artifact_ids": file_artifact_ids,
        "candidate_artifact_ids": [
            *state.get("candidate_artifact_ids", []),
            *file_artifact_ids,
        ],
    }


def _validate(state: ProductionWorkflowState) -> dict:
    candidate = _candidate_set_from_state(state)
    result = validate_game_text(candidate)
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=state["workflow_run_id"],
            step_key=f"validate_game_text_{state.get('repair_count', 0)}",
            agent_name="lore_reviewer",
        )
        artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="game_text_validation_report",
            logical_name="game-text-validation.json",
            content=result.model_dump_json(indent=2), mime_type="application/json",
            validation_status="valid" if result.valid else "invalid",
        )
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={"valid": result.valid, "report_artifact_id": artifact.id},
        )
        step_key = step.step_key
        artifact_id = artifact.id
    return {
        "current_step": step_key,
        "game_text_validation": result.model_dump(mode="json"),
        "validation_artifact_ids": [*state.get("validation_artifact_ids", []), artifact_id],
    }


def _route_validation(state: ProductionWorkflowState) -> str:
    result = GameTextValidationResult.model_validate(state["game_text_validation"])
    if result.valid:
        manifest = _skill_manifest(state)
        if manifest is not None and "game_text_validation" in manifest.validators.post:
            run_skill_validators(["game_text_validation"], result)
        return "review"
    return "repair" if state.get("repair_count", 0) < _max_repairs(state) else "manual"


def _review(state: ProductionWorkflowState) -> dict:
    candidate = _candidate_set_from_state(state)
    validation = GameTextValidationResult.model_validate(state["game_text_validation"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=run.id,
            step_key=f"review_game_text_{state.get('repair_count', 0)}",
            agent_name="lore_reviewer",
        )
        reviewer_stage = _stage(state, "reviewer")
        review = LoreReviewAgent().review(
            AgentContext(db=db, task=task, workflow_run=run, step_run=step),
            request=task.request, candidate=candidate, validation=validation,
            instruction_text=(
                _instruction(state, reviewer_stage.skill)
                if reviewer_stage
                else None
            ),
            profile_name=reviewer_stage.profile if reviewer_stage else None,
            output_schema_name=(
                reviewer_stage.output_schema
                if reviewer_stage
                else "game_text_review_result"
            ),
            output_validators=["structured_output"] if reviewer_stage else [],
        )
        artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="lore_review_report",
            logical_name="lore-review.json", content=review.model_dump_json(indent=2),
            mime_type="application/json",
            validation_status="valid" if review.approved else "invalid",
        )
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={"approved": review.approved, "report_artifact_id": artifact.id},
        )
        step_key = step.step_key
        artifact_id = artifact.id
    return {
        "current_step": step_key,
        "game_text_review": review.model_dump(mode="json"),
        "review_artifact_ids": [*state.get("review_artifact_ids", []), artifact_id],
    }


def _route_review(state: ProductionWorkflowState) -> str:
    review = GameTextReviewResult.model_validate(state["game_text_review"])
    if review.approved:
        manifest = _skill_manifest(state)
        if manifest is not None and "lore_review" in manifest.validators.post:
            run_skill_validators(["lore_review"], review)
        return "approval"
    return "repair" if state.get("repair_count", 0) < _max_repairs(state) else "manual"


def _skill_manifest(state: ProductionWorkflowState) -> SkillManifest | None:
    runtime = state.get("skill_runtime")
    if not runtime:
        return None
    return SkillManifest.model_validate(runtime["manifest"])


def _stage(
    state: ProductionWorkflowState,
    name: str,
) -> SkillStageBinding | None:
    manifest = _skill_manifest(state)
    return manifest.stages.get(name) if manifest is not None else None


def _instruction(state: ProductionWorkflowState, skill_name: str) -> str:
    runtime = state.get("skill_runtime") or {}
    instructions = runtime.get("instructions") or {}
    instruction = instructions.get(skill_name)
    if not instruction:
        raise CandidateValidationError(
            "Skill instruction snapshot is missing.",
            {"skill_name": skill_name},
        )
    return str(instruction)


def _max_repairs(state: ProductionWorkflowState) -> int:
    manifest = _skill_manifest(state)
    if manifest is None or manifest.workflow is None:
        return MAX_REPAIRS
    return min(MAX_REPAIRS, manifest.workflow.max_repairs)


def _candidate_set_from_state(state: ProductionWorkflowState) -> GameTextCandidateSet:
    payload = state["game_text_candidate"]
    try:
        return GameTextCandidateSet.model_validate(payload)
    except Exception:
        return normalize_candidate_set(GameTextCandidate.model_validate(payload))


def _repair(state: ProductionWorkflowState) -> dict:
    count = state.get("repair_count", 0) + 1
    return {
        "current_step": f"prepare_game_text_repair_{count}",
        "repair_count": count,
        "repair_summaries": [
            *state.get("repair_summaries", []),
            json.dumps(
                {
                    "validation": state.get("game_text_validation"),
                    "review": state.get("game_text_review"),
                },
                ensure_ascii=False,
            ),
        ],
    }


def _manual(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        event_service.record_event(
            db, task_id=state["task_id"], workflow_run_id=state["workflow_run_id"],
            event_type="manual_takeover_required", actor_type="system",
            actor_name="game_text_production",
            payload={"repair_count": state.get("repair_count", 0)},
        )
        db.commit()
    raise CandidateValidationError("Game text candidate exceeded automatic repair limit.")


def _request_approval(state: ProductionWorkflowState) -> dict:
    manifest = _skill_manifest(state)
    if manifest is not None and not manifest.approval.delivery_required:
        raise CandidateValidationError(
            "Active game-text Skill must require delivery approval."
        )
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=state["workflow_run_id"],
            step_key="request_game_text_approval", agent_name="lore_reviewer",
        )
        approval = task_service.create_approval(
            db, task,
            ApprovalCreate(
                artifact_id=state["current_candidate_artifact_id"],
                approval_type="game_text_review",
                risk_summary=f"Game text repairs: {state.get('repair_count', 0)}.",
                diff_summary="Review candidate text, validation report, and lore findings.",
            ),
        )
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"approval_id": approval.id})
        approval_id = approval.id
    return {"current_step": "request_game_text_approval", "approval_id": approval_id}


def _wait(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=state["workflow_run_id"],
            step_key="wait_for_game_text_approval", agent_name="approval_gate",
        )
        if step.status != RunStatus.PAUSED:
            step_service.pause_step(db, task_id=task.id, step=step, output_data={"approval_id": state["approval_id"]})
    decision = interrupt({"approval_id": state["approval_id"], "type": "game_text_review"})
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.get_latest_step(db, state["workflow_run_id"], "wait_for_game_text_approval")
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"decision": decision})
    return {"approval_decision": decision}


def _deliver(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=state["workflow_run_id"],
            step_key="deliver_game_text", agent_name="system",
        )
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        _, diff_id = workspace_service.build_diff(db, workspace)
        manifest = GameTextDeliveryManifest(
            task_id=task.id, workflow_run_id=state["workflow_run_id"],
            workspace_id=workspace.id,
            candidate_artifact_ids=state.get("candidate_artifact_ids", []),
            validation_report_artifact_ids=state.get("validation_artifact_ids", []),
            review_report_artifact_ids=state.get("review_artifact_ids", []),
            diff_artifact_id=diff_id,
            final_candidate_artifact_id=state["current_candidate_artifact_id"],
            final_candidate_artifact_ids=state.get("current_candidate_artifact_ids", []),
            repair_count=state.get("repair_count", 0),
        )
        artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="game_text_delivery_manifest",
            logical_name="game-text-delivery-manifest.json",
            content=manifest.model_dump_json(indent=2), mime_type="application/json",
            validation_status="valid", approval_status="approved",
        )
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"manifest_artifact_id": artifact.id})
        manifest_artifact_id = artifact.id
    return {
        "current_step": "deliver_game_text",
        "delivery_manifest_artifact_id": manifest_artifact_id,
        "final_summary": "Approved game text delivered without modifying source.",
    }


def build_game_text_production_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(ProductionWorkflowState)
    for name, node in {
        "initialize": _initialize, "generate": _generate, "validate": _validate,
        "review": _review, "repair": _repair, "manual": _manual,
        "approval": _request_approval, "wait": _wait, "deliver": _deliver,
    }.items():
        graph.add_node(name, node)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "generate")
    graph.add_edge("generate", "validate")
    graph.add_conditional_edges(
        "validate",
        _route_validation,
        {"review": "review", "repair": "repair", "manual": "manual"},
    )
    graph.add_conditional_edges(
        "review",
        _route_review,
        {"approval": "approval", "repair": "repair", "manual": "manual"},
    )
    graph.add_edge("repair", "generate")
    graph.add_edge("approval", "wait")
    graph.add_edge("wait", "deliver")
    graph.add_edge("deliver", END)
    return graph.compile(checkpointer=checkpointer, name="game_text_production")
