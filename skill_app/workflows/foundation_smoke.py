from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from skill_app.db.database import SessionLocal
from skill_app.domain.status import RunStatus
from skill_app.schemas.approval import ApprovalCreate
from skill_app.services import artifact_service, step_service, task_service, workflow_service
from skill_app.workflows.state import ProductionWorkflowState


def _initialize(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="initialize",
            agent_name="system",
            input_data={"request": task.request},
        )
        workflow_service.start_run(db, task, run)
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"initialized": True})
    return {"current_step": "initialize"}


def _create_demo_artifact(state: ProductionWorkflowState) -> dict:
    content = f"foundation-smoke:{state['task_id']}:{state['workflow_run_id']}"
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="create_demo_artifact",
            agent_name="foundation_agent",
            input_data={"content": content},
        )
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            step_run_id=step.id,
            artifact_type="foundation_demo",
            logical_name="foundation-smoke.txt",
            content=content,
            mime_type="text/plain; charset=utf-8",
            metadata={"workflow_run_id": run.id},
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"artifact_id": artifact.id},
        )
        artifact_id = artifact.id
    return {"current_step": "create_demo_artifact", "artifact_ids": [artifact_id]}


def _request_approval(state: ProductionWorkflowState) -> dict:
    artifact_id = state["artifact_ids"][-1]
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="request_approval",
            agent_name="foundation_agent",
            input_data={"artifact_id": artifact_id},
        )
        approval = task_service.create_approval(
            db,
            task,
            ApprovalCreate(
                artifact_id=artifact_id,
                approval_type="artifact_review",
                risk_summary="Foundation workflow approval gate.",
                diff_summary="Review the generated foundation artifact.",
            ),
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": approval.id},
        )
        approval_id = approval.id
    return {"current_step": "request_approval", "approval_id": approval_id}


def _wait_for_approval(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="wait_for_approval",
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

    decision = interrupt({"approval_id": state["approval_id"], "type": "artifact_review"})

    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.get_latest_step(db, state["workflow_run_id"], "wait_for_approval")
        if step is None:
            raise RuntimeError("Approval step disappeared during resume.")
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": state["approval_id"], "decision": decision},
        )
    return {"current_step": "wait_for_approval", "approval_decision": decision}


def _finish(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="finish",
            agent_name="system",
            input_data={"approval_decision": state.get("approval_decision")},
        )
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"finished": True},
        )
    return {
        "current_step": "finish",
        "final_summary": "Foundation workflow completed after approval.",
    }


def build_foundation_smoke_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(ProductionWorkflowState)
    graph.add_node("initialize", _initialize)
    graph.add_node("create_demo_artifact", _create_demo_artifact)
    graph.add_node("request_approval", _request_approval)
    graph.add_node("wait_for_approval", _wait_for_approval)
    graph.add_node("finish", _finish)

    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "create_demo_artifact")
    graph.add_edge("create_demo_artifact", "request_approval")
    graph.add_edge("request_approval", "wait_for_approval")
    graph.add_edge("wait_for_approval", "finish")
    graph.add_edge("finish", END)
    return graph.compile(checkpointer=checkpointer, name="foundation_smoke")
