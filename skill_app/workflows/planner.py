from __future__ import annotations

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from skill_app.agents.base import AgentContext
from skill_app.agents.planner import PlannerAgent, render_plan_markdown
from skill_app.db.database import SessionLocal
from skill_app.domain.status import RunStatus
from skill_app.schemas.approval import ApprovalCreate
from skill_app.schemas.plan import TaskPlan
from skill_app.services import artifact_service, step_service, task_service, workflow_service
from skill_app.workflows.state import ProductionWorkflowState


def _initialize_planning(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="initialize_planning",
            agent_name="system",
            input_data={"request": task.request},
        )
        workflow_service.start_planning_run(db, task, run)
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"planning_initialized": True},
        )
    return {"current_step": "initialize_planning"}


def _generate_plan(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="generate_plan",
            agent_name="planner",
            input_data={"request": task.request, "project_id": task.project_id},
        )
        try:
            plan = PlannerAgent().execute(
                AgentContext(db=db, task=task, workflow_run=run, step_run=step)
            )
            json_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="plan_json",
                logical_name="task-plan.json",
                content=plan.model_dump_json(indent=2),
                mime_type="application/json",
                metadata={"workflow_run_id": run.id, "prompt_version": PlannerAgent.prompt_version},
                validation_status="valid",
            )
            json_artifact_id = json_artifact.id
            markdown_artifact = artifact_service.create_text_artifact(
                db,
                task,
                step_run_id=step.id,
                artifact_type="plan_markdown",
                logical_name="task-plan.md",
                content=render_plan_markdown(plan),
                mime_type="text/markdown; charset=utf-8",
                metadata={"workflow_run_id": run.id, "json_artifact_id": json_artifact_id},
                validation_status="valid",
            )
            markdown_artifact_id = markdown_artifact.id
            step_service.complete_step(
                db,
                task_id=task.id,
                step=step,
                output_data={
                    "plan_json_artifact_id": json_artifact_id,
                    "plan_markdown_artifact_id": markdown_artifact_id,
                    "step_count": len(plan.steps),
                },
            )
        except Exception as exc:
            step_service.fail_step(db, task_id=task.id, step=step, error=exc)
            raise
    return {
        "current_step": "generate_plan",
        "plan": plan.model_dump(mode="json"),
        "artifact_ids": [json_artifact_id, markdown_artifact_id],
        "plan_json_artifact_id": json_artifact_id,
        "plan_markdown_artifact_id": markdown_artifact_id,
    }


def _stage_plan_steps(state: ProductionWorkflowState) -> dict:
    plan = TaskPlan.model_validate(state["plan"])
    planned_step_ids: list[str] = []
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        stage_step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="stage_plan_steps",
            agent_name="planner",
            input_data={"plan_json_artifact_id": state["plan_json_artifact_id"]},
        )
        for planned in plan.steps:
            step = step_service.create_pending_step(
                db,
                task_id=task.id,
                workflow_run_id=run.id,
                step_key=f"planned_{planned.key}",
                agent_name=planned.agent,
                input_data={
                    "plan_step": planned.model_dump(mode="json"),
                    "plan_json_artifact_id": state["plan_json_artifact_id"],
                },
            )
            planned_step_ids.append(step.id)
        step_service.complete_step(
            db,
            task_id=task.id,
            step=stage_step,
            output_data={"planned_step_ids": planned_step_ids},
        )
    return {"current_step": "stage_plan_steps", "planned_step_ids": planned_step_ids}


def _route_after_staging(state: ProductionWorkflowState) -> str:
    plan = TaskPlan.model_validate(state["plan"])
    return "request_plan_approval" if plan.requires_plan_approval else "activate_plan"


def _request_plan_approval(state: ProductionWorkflowState) -> dict:
    plan = TaskPlan.model_validate(state["plan"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="request_plan_approval",
            agent_name="planner",
            input_data={"plan_json_artifact_id": state["plan_json_artifact_id"]},
        )
        approval = task_service.create_approval(
            db,
            task,
            ApprovalCreate(
                approval_type="plan_approval",
                risk_summary=f"Plan risk: {plan.risk_level.value}; steps: {len(plan.steps)}.",
                diff_summary=(
                    f"Plan artifacts: {state['plan_json_artifact_id']}, "
                    f"{state['plan_markdown_artifact_id']}."
                ),
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
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=state["workflow_run_id"],
            step_key="wait_for_plan_approval",
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
    decision = interrupt({"approval_id": state["approval_id"], "type": "plan_approval"})
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.get_latest_step(db, state["workflow_run_id"], "wait_for_plan_approval")
        if step is None:
            raise RuntimeError("Plan approval step disappeared during resume.")
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"approval_id": state["approval_id"], "decision": decision},
        )
    return {"current_step": "wait_for_plan_approval", "approval_decision": decision}


def _activate_plan(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="activate_plan",
            agent_name="planner",
            input_data={"planned_step_ids": state.get("planned_step_ids", [])},
        )
        task_service.activate_planned_task(db, task, run.id)
        step_service.complete_step(
            db,
            task_id=task.id,
            step=step,
            output_data={"ready_for_execution": True},
        )
    return {
        "current_step": "activate_plan",
        "final_summary": "Plan validated and staged for execution.",
    }


def build_planner_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(ProductionWorkflowState)
    graph.add_node("initialize_planning", _initialize_planning)
    graph.add_node("generate_plan", _generate_plan)
    graph.add_node("stage_plan_steps", _stage_plan_steps)
    graph.add_node("request_plan_approval", _request_plan_approval)
    graph.add_node("wait_for_plan_approval", _wait_for_plan_approval)
    graph.add_node("activate_plan", _activate_plan)

    graph.add_edge(START, "initialize_planning")
    graph.add_edge("initialize_planning", "generate_plan")
    graph.add_edge("generate_plan", "stage_plan_steps")
    graph.add_conditional_edges(
        "stage_plan_steps",
        _route_after_staging,
        {
            "request_plan_approval": "request_plan_approval",
            "activate_plan": "activate_plan",
        },
    )
    graph.add_edge("request_plan_approval", "wait_for_plan_approval")
    graph.add_edge("wait_for_plan_approval", "activate_plan")
    graph.add_edge("activate_plan", END)
    return graph.compile(checkpointer=checkpointer, name="planner")
