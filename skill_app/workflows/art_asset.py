from __future__ import annotations

import json
import re

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from skill_app.agents.art_brief_agent import ArtBriefAgent
from skill_app.db.database import SessionLocal
from skill_app.domain.errors import ArtifactValidationError, InvalidStateTransition
from skill_app.domain.status import ApprovalStatus, RunStatus
from skill_app.schemas.approval import ApprovalCreate
from skill_app.schemas.art import ArtManifest, VisualBrief
from skill_app.services import artifact_service, event_service, step_service, task_service, workflow_service
from skill_app.tools.image_validation import build_contact_sheet, validate_image
from skill_app.tools.runtime import execute_tool
from skill_app.workflows.state import ProductionWorkflowState


def _initialize(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="initialize_art_asset",
            agent_name="system",
            input_data={"request": task.request},
        )
        workflow_service.start_run(db, task, run)
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"initialized": True})
    return {
        "current_step": "initialize_art_asset",
        "candidate_artifact_ids": [],
        "artifact_ids": [],
    }


def _create_brief(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="create_visual_brief",
            agent_name="art_brief_agent",
            input_data={"request": task.request},
        )
        brief = ArtBriefAgent().create_brief(task.request)
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="visual_brief",
            logical_name="visual-brief.json",
            content=brief.model_dump_json(indent=2),
            mime_type="application/json",
            metadata={
                "asset_type": brief.asset_type,
                "width": brief.width,
                "height": brief.height,
                "candidate_count": brief.candidate_count,
                "reference_artifact_ids": brief.reference_artifact_ids,
                "reference_hashes": [],
            },
            validation_status="valid",
            approval_status="pending",
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"visual_brief_artifact_id": artifact.id},
        )
        brief_artifact_id = artifact.id
    return {
        "current_step": "create_visual_brief",
        "visual_brief": brief.model_dump(mode="json"),
        "visual_brief_artifact_id": brief_artifact_id,
        "artifact_ids": [brief_artifact_id],
    }


def _request_brief_approval(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="request_brief_approval",
            agent_name="art_brief_agent",
        )
        approval = task_service.create_approval(
            db,
            task,
            ApprovalCreate(
                artifact_id=state["visual_brief_artifact_id"],
                approval_type="tool:generate_image_candidates",
                risk_summary="Approving this Brief permits image-provider invocation.",
                diff_summary="Review prompt, negative prompt, dimensions, alpha, and candidate count.",
            ),
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": approval.id},
        )
        approval_id = approval.id
    return {"current_step": "request_brief_approval", "approval_id": approval_id}


def _wait_for_brief_approval(state: ProductionWorkflowState) -> dict:
    return _approval_interrupt(state, "wait_for_brief_approval", "visual_brief")


def _generate_candidates(state: ProductionWorkflowState) -> dict:
    brief = VisualBrief.model_validate(state["visual_brief"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        brief_artifact = artifact_service.get_artifact(db, state["visual_brief_artifact_id"])
        if brief_artifact.approval_status != ApprovalStatus.APPROVED:
            raise InvalidStateTransition(
                "Image generation requires an approved Visual Brief.",
                {"visual_brief_artifact_id": brief_artifact.id},
            )
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="generate_art_candidates",
            agent_name="art_asset_agent",
            input_data={
                "visual_brief_artifact_id": brief_artifact.id,
                "candidate_count": brief.candidate_count,
            },
        )
        event_service.record_event(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_run_id=step.id,
            event_type="image_generation_started",
            actor_type="agent",
            actor_name="art_asset_agent",
            payload={"visual_brief_artifact_id": brief_artifact.id},
        )
        db.commit()
        tool_result = execute_tool(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_run_id=step.id,
            agent_name="art_asset_agent",
            tool_name="generate_image_candidates",
            arguments={
                "brief": brief.model_dump(mode="json"),
                "output_directory": f"generated/art/{task.id}",
            },
        )
        if not tool_result.ok:
            raise ArtifactValidationError(
                "Image generation tool failed.",
                {"error_type": tool_result.error_type, "stderr": tool_result.stderr},
            )
        generated_metadata = tool_result.output["candidates"]
        candidate_ids: list[str] = []
        validation_results: list[dict] = []
        seeds: list[int] = []
        for artifact_id, image_metadata in zip(
            tool_result.artifacts,
            generated_metadata,
            strict=True,
        ):
            artifact = artifact_service.get_artifact(db, artifact_id)
            validation = validate_image(artifact_service.read_artifact_bytes(artifact), brief)
            artifact.artifact_type = "art_candidate"
            artifact.artifact_metadata = {
                    "visual_brief_artifact_id": brief_artifact.id,
                    **image_metadata,
                    "reference_artifact_ids": brief.reference_artifact_ids,
                    "reference_hashes": [],
                    "validation": validation.model_dump(mode="json"),
                }
            artifact.validation_status = "valid" if validation.valid else "invalid"
            artifact.approval_status = "pending"
            db.commit()
            candidate_ids.append(artifact.id)
            validation_results.append(
                {"artifact_id": artifact.id, **validation.model_dump(mode="json")}
            )
            seeds.append(int(image_metadata["seed"]))
        if not all(result["valid"] for result in validation_results):
            raise ArtifactValidationError(
                "One or more generated image candidates failed validation.",
                {"results": validation_results},
            )
        report = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="image_validation_report",
            logical_name="image-validation-report.json",
            content=json.dumps({"valid": True, "results": validation_results}, indent=2),
            mime_type="application/json",
            metadata={"candidate_artifact_ids": candidate_ids},
            validation_status="valid",
        )
        contact_sheet = artifact_service.create_bytes_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="contact_sheet",
            logical_name="contact-sheet.png",
            content=build_contact_sheet(seeds),
            mime_type="image/png",
            metadata={
                "candidate_artifact_ids": candidate_ids,
                "layout": f"{len(candidate_ids)}x1",
                "cell_width": 256,
                "cell_height": 256,
            },
            validation_status="valid",
            approval_status="pending",
        )
        event_service.record_event(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_run_id=step.id,
            event_type="image_generation_completed",
            actor_type="agent",
            actor_name="art_asset_agent",
            payload={"candidate_artifact_ids": candidate_ids, "contact_sheet_artifact_id": contact_sheet.id},
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={
                "candidate_artifact_ids": candidate_ids,
                "validation_report_artifact_id": report.id,
                "contact_sheet_artifact_id": contact_sheet.id,
            },
        )
        report_id = report.id
        contact_sheet_id = contact_sheet.id
    return {
        "current_step": "generate_art_candidates",
        "candidate_artifact_ids": candidate_ids,
        "validation_report_artifact_id": report_id,
        "contact_sheet_artifact_id": contact_sheet_id,
        "artifact_ids": [
            *state.get("artifact_ids", []),
            *candidate_ids,
            report_id,
            contact_sheet_id,
        ],
    }


def _request_selection(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="request_candidate_selection",
            agent_name="art_asset_agent",
        )
        approval = task_service.create_approval(
            db,
            task,
            ApprovalCreate(
                artifact_id=state["contact_sheet_artifact_id"],
                approval_type="art_candidate_selection",
                risk_summary="Select one candidate for delivery.",
                diff_summary=(
                    "Approve the default first candidate, or include "
                    "`candidate=<artifact-id>` in the approval comment."
                ),
            ),
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": approval.id},
        )
        approval_id = approval.id
    return {"current_step": "request_candidate_selection", "approval_id": approval_id}


def _wait_for_selection(state: ProductionWorkflowState) -> dict:
    return _approval_interrupt(state, "wait_for_candidate_selection", "art_candidate_selection")


def _deliver(state: ProductionWorkflowState) -> dict:
    candidate_ids = state["candidate_artifact_ids"]
    decision = state.get("approval_decision") or {}
    comment = decision.get("comment") or ""
    match = re.search(r"candidate=([0-9a-f-]{36})", comment, flags=re.IGNORECASE)
    selected_id = match.group(1) if match and match.group(1) in candidate_ids else candidate_ids[0]
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="deliver_art_asset",
            agent_name="system",
            input_data={"selected_artifact_id": selected_id},
        )
        selected = artifact_service.get_artifact(db, selected_id)
        selected.approval_status = ApprovalStatus.APPROVED
        manifest = ArtManifest(
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            visual_brief_artifact_id=state["visual_brief_artifact_id"],
            candidate_artifact_ids=candidate_ids,
            validation_report_artifact_id=state["validation_report_artifact_id"],
            contact_sheet_artifact_id=state["contact_sheet_artifact_id"],
            selected_artifact_id=selected_id,
        )
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="art_delivery_manifest",
            logical_name="art-delivery-manifest.json",
            content=manifest.model_dump_json(indent=2),
            mime_type="application/json",
            metadata={"selected_artifact_id": selected_id},
            validation_status="valid",
            approval_status="approved",
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"manifest_artifact_id": artifact.id, "selected_artifact_id": selected_id},
        )
        manifest_artifact_id = artifact.id
    return {
        "current_step": "deliver_art_asset",
        "selected_artifact_id": selected_id,
        "delivery_manifest_artifact_id": manifest_artifact_id,
        "artifact_ids": [*state.get("artifact_ids", []), manifest_artifact_id],
        "final_summary": "Approved art asset delivered without modifying the source project.",
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


def build_art_asset_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(ProductionWorkflowState)
    graph.add_node("initialize", _initialize)
    graph.add_node("create_brief", _create_brief)
    graph.add_node("request_brief_approval", _request_brief_approval)
    graph.add_node("wait_for_brief_approval", _wait_for_brief_approval)
    graph.add_node("generate_candidates", _generate_candidates)
    graph.add_node("request_selection", _request_selection)
    graph.add_node("wait_for_selection", _wait_for_selection)
    graph.add_node("deliver", _deliver)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "create_brief")
    graph.add_edge("create_brief", "request_brief_approval")
    graph.add_edge("request_brief_approval", "wait_for_brief_approval")
    graph.add_edge("wait_for_brief_approval", "generate_candidates")
    graph.add_edge("generate_candidates", "request_selection")
    graph.add_edge("request_selection", "wait_for_selection")
    graph.add_edge("wait_for_selection", "deliver")
    graph.add_edge("deliver", END)
    return graph.compile(checkpointer=checkpointer, name="art_asset")
