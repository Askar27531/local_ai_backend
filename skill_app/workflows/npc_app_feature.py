from __future__ import annotations

import hashlib
from pathlib import Path

from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from skill_app.agents.base import AgentContext
from skill_app.agents.npc_app_feature import (
    NpcAppFeatureAgent,
    NpcAppReviewAgent,
    NpcAppTestAgent,
)
from skill_app.config import get_settings
from skill_app.db.database import SessionLocal
from skill_app.domain.errors import CandidateValidationError
from skill_app.domain.status import RunStatus
from skill_app.schemas.approval import ApprovalCreate
from skill_app.schemas.project_workflows import (
    NpcAppDeliveryManifest,
    NpcAppPatchResult,
    NpcAppReviewResult,
    NpcAppTestSuite,
)
from skill_app.schemas.skill import SkillManifest
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
from skill_app.services.skill_registry import run_skill_validators
from skill_app.tools.inputs import ApplyCodePatchInput, PatchFileInput, RunPytestInput, RunPythonCompileInput
from skill_app.tools.patch import apply_code_patch
from skill_app.tools.testing import run_pytest, run_python_compile
from skill_app.workflows.state import ProductionWorkflowState

MAX_REPAIRS = 2
MAX_REPAIR_STDOUT_CHARS = 1000
MAX_REPAIR_FILE_CHARS = 4000
MAX_REPAIR_HISTORY_ITEMS = 3
TEST_EXCERPT_RADIUS = 12


def _initialize(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=run.id,
            step_key="initialize_npc_app_feature", agent_name="system",
        )
        workflow_service.start_run(db, task, run)
        workspace = workspace_service.get_or_create_active_workspace(db, task)
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={"workspace_id": workspace.id, "target_root": "npc_app"},
        )
        workspace_id = workspace.id
    return {
        "current_step": "initialize_npc_app_feature",
        "workspace_id": workspace_id,
        "repair_count": 0,
        "candidate_artifact_ids": [],
        "test_plan_artifact_ids": [],
        "test_patch_artifact_ids": [],
        "validation_artifact_ids": [],
        "review_artifact_ids": [],
        "code_patch_artifact_ids": [],
        "repair_summaries": [],
    }


def _generate(state: ProductionWorkflowState) -> dict:
    repair_count = state.get("repair_count", 0)
    previous = (
        NpcAppPatchResult.model_validate(state["npc_app_patch"])
        if state.get("npc_app_patch")
        else None
    )
    evidence = {}
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        key = "generate_npc_app_patch" if previous is None else f"repair_npc_app_patch_{repair_count}"
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=run.id,
            step_key=key, agent_name="code_generator",
            input_data={"repair_count": repair_count, "evidence": evidence},
        )
        manifest = _skill_manifest(state)
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        evidence = _build_repair_evidence(state, workspace) if previous is not None else {}
        if manifest is not None:
            run_skill_validators(
                manifest.validators.pre,
                None,
                {
                    "task_status": task.status,
                    "workspace_status": workspace.status,
                },
            )
        result = NpcAppFeatureAgent().generate(
            AgentContext(db=db, task=task, workflow_run=run, step_run=step),
            request=task.request,
            repair_count=repair_count,
            previous_patch=previous,
            evidence=evidence,
            limits=manifest.limits if manifest else None,
            output_schema_name=(
                manifest.model.output_schema
                if manifest is not None
                else "npc_app_patch_result"
            ),
            output_validators=manifest.validators.output if manifest else [],
            profile_name=manifest.model.profile if manifest else None,
        )
        existing = [
            item.path for item in result.patch.files
            if (Path(workspace.source_root) / item.path).is_file()
            and item.path not in workspace.copied_paths
        ]
        if existing:
            workspace_service.copy_files(db, workspace, existing)
        if previous is not None:
            for item in result.patch.files:
                target = workspace_service.resolve_workspace_path(workspace, item.path)
                item.expected_sha256 = (
                    hashlib.sha256(target.read_bytes()).hexdigest()
                    if target.is_file()
                    else None
                )
        context = ToolContext(
            task_id=task.id, workflow_run_id=run.id, step_run_id=step.id,
            agent_name="code_generator", project_root=str(get_settings().project_root),
            workspace_root=workspace.workspace_root, permissions=["apply_code_patch"],
            timeout_seconds=get_settings().tool_timeout_seconds,
        )
        applied = apply_code_patch(
            context,
            ApplyCodePatchInput(
                files=[
                    PatchFileInput(
                        path=item.path,
                        content=item.content,
                        expected_sha256=item.expected_sha256,
                    )
                    for item in result.patch.files
                ],
                max_changed_lines=(
                    min(1200, manifest.limits.max_changed_lines)
                    if manifest is not None
                    else 1200
                ),
            ),
        )
        candidate_ids = list(state.get("candidate_artifact_ids", []))
        for item in result.patch.files:
            artifact = artifact_service.create_text_artifact(
                db, task, step_run_id=step.id,
                artifact_type="npc_app_candidate",
                logical_name=item.path.replace("/", "__"),
                content=item.content,
                mime_type="text/x-python",
                metadata={
                    "workspace_id": workspace.id,
                    "workspace_path": item.path,
                    "repair_count": repair_count,
                    "expected_sha256": item.expected_sha256,
                },
            )
            candidate_ids.append(artifact.id)
        patch_manifest = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id,
            artifact_type="npc_app_patch_manifest",
            logical_name="npc-app-patch.json",
            content=result.model_dump_json(indent=2),
            mime_type="application/json",
            metadata={"affected_files": result.affected_files, "repair_count": repair_count},
            validation_status="valid",
        )
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={"affected_files": applied.output["affected_files"], "patch_artifact_id": patch_manifest.id},
        )
        patch_manifest_id = patch_manifest.id
    return {
        "current_step": key,
        "npc_app_patch": result.model_dump(mode="json"),
        "candidate_artifact_ids": candidate_ids,
        "code_patch_artifact_ids": [*state.get("code_patch_artifact_ids", []), patch_manifest_id],
    }


def _test(state: ProductionWorkflowState) -> dict:
    patch = NpcAppPatchResult.model_validate(state["npc_app_patch"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=run.id,
            step_key=f"test_npc_app_patch_{state.get('repair_count', 0)}",
            agent_name="test_agent",
        )
        suite = NpcAppTestAgent().generate(
            AgentContext(db=db, task=task, workflow_run=run, step_run=step),
            request=task.request,
            patch=patch,
            acceptance_criteria=[
                "All changed Python files compile.",
                "Focused npc_app tests pass without network or persistent services.",
                "Source project remains unchanged.",
            ],
        )
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        test_target = workspace_service.resolve_workspace_path(workspace, suite.plan.test_path)
        test_target.parent.mkdir(parents=True, exist_ok=True)
        test_target.write_text(suite.test_content, encoding="utf-8")
        context = ToolContext(
            task_id=task.id, workflow_run_id=run.id, step_run_id=step.id,
            agent_name="test_agent", project_root=str(get_settings().project_root),
            workspace_root=workspace.workspace_root, permissions=["run_python_compile", "run_pytest"],
            timeout_seconds=get_settings().tool_timeout_seconds,
        )
        compile_result = run_python_compile(
            context,
            RunPythonCompileInput(paths=[*patch.affected_files, suite.plan.test_path]),
        )
        pytest_result = run_pytest(
            context,
            RunPytestInput(paths=[suite.plan.test_path], max_failures=1),
        )
        errors = []
        if not compile_result.ok:
            errors.append("npc_app candidate compilation failed.")
        if not pytest_result.ok:
            errors.append("Generated npc_app pytest failed.")
        report = TestExecutionReport(
            passed=compile_result.ok and pytest_result.ok,
            compile_passed=compile_result.ok,
            pytest_passed=pytest_result.ok,
            candidate_path=patch.affected_files[0],
            test_path=suite.plan.test_path,
            stdout=pytest_result.stdout,
            stderr="\n".join(filter(None, [compile_result.stderr, pytest_result.stderr])),
            errors=errors,
            uncovered_risks=suite.plan.uncovered_risks,
        )
        plan_artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="npc_app_test_plan",
            logical_name="npc-app-test-plan.json", content=suite.plan.model_dump_json(indent=2),
            mime_type="application/json", validation_status="valid",
        )
        test_artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="npc_app_test_patch",
            logical_name=Path(suite.plan.test_path).name, content=suite.test_content,
            mime_type="text/x-python", validation_status="valid",
        )
        report_artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="npc_app_test_report",
            logical_name="npc-app-test-report.json", content=report.model_dump_json(indent=2),
            mime_type="application/json",
            validation_status="valid" if report.passed else "invalid",
        )
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={"passed": report.passed, "report_artifact_id": report_artifact.id},
        )
        step_key = step.step_key
        plan_artifact_id = plan_artifact.id
        test_artifact_id = test_artifact.id
        report_artifact_id = report_artifact.id
    return {
        "current_step": step_key,
        "npc_app_test_suite": suite.model_dump(mode="json"),
        "npc_app_test_report": report.model_dump(mode="json"),
        "test_plan_artifact_ids": [*state.get("test_plan_artifact_ids", []), plan_artifact_id],
        "test_patch_artifact_ids": [*state.get("test_patch_artifact_ids", []), test_artifact_id],
        "validation_artifact_ids": [*state.get("validation_artifact_ids", []), report_artifact_id],
    }


def _route_test(state: ProductionWorkflowState) -> str:
    report = TestExecutionReport.model_validate(state["npc_app_test_report"])
    if report.passed:
        manifest = _skill_manifest(state)
        if manifest is not None:
            run_skill_validators(
                [
                    name
                    for name in manifest.validators.post
                    if name in {"python_compile", "focused_pytest"}
                ],
                report,
            )
        return "review"
    return "repair" if state.get("repair_count", 0) < _max_repairs(state) else "manual"


def _review(state: ProductionWorkflowState) -> dict:
    patch = NpcAppPatchResult.model_validate(state["npc_app_patch"])
    suite = NpcAppTestSuite.model_validate(state["npc_app_test_suite"])
    report = TestExecutionReport.model_validate(state["npc_app_test_report"])
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        run = workflow_service.get_workflow_run(db, state["workflow_run_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=run.id,
            step_key=f"review_npc_app_patch_{state.get('repair_count', 0)}",
            agent_name="review_agent",
        )
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        diff, _ = workspace_service.build_diff(db, workspace, create_artifact=False)
        result = NpcAppReviewAgent().review(
            AgentContext(db=db, task=task, workflow_run=run, step_run=step),
            request=task.request,
            patch=patch,
            diff=diff.content,
            test_suite=suite,
            test_report=report,
            acceptance_criteria=[
                "Patch remains under npc_app/.",
                "API, schema, service, and test behavior remain consistent.",
                "No source project mutation occurs before approval.",
            ],
        )
        artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="npc_app_review_report",
            logical_name="npc-app-review.json", content=result.model_dump_json(indent=2),
            mime_type="application/json",
            validation_status="valid" if not any(item.blocking for item in result.findings) else "invalid",
        )
        step_service.complete_step(
            db, task_id=task.id, step=step,
            output_data={"approved": not any(item.blocking for item in result.findings)},
        )
        step_key = step.step_key
        artifact_id = artifact.id
    return {
        "current_step": step_key,
        "npc_app_review": result.model_dump(mode="json"),
        "review_artifact_ids": [*state.get("review_artifact_ids", []), artifact_id],
    }


def _route_review(state: ProductionWorkflowState) -> str:
    review = NpcAppReviewResult.model_validate(state["npc_app_review"])
    if not any(item.blocking for item in review.findings):
        manifest = _skill_manifest(state)
        if manifest is not None and "semantic_review" in manifest.validators.post:
            run_skill_validators(["semantic_review"], review)
        return "approval"
    return "repair" if state.get("repair_count", 0) < _max_repairs(state) else "manual"


def _skill_manifest(state: ProductionWorkflowState) -> SkillManifest | None:
    runtime = state.get("skill_runtime")
    if not runtime:
        return None
    return SkillManifest.model_validate(runtime["manifest"])


def _max_repairs(state: ProductionWorkflowState) -> int:
    manifest = _skill_manifest(state)
    if manifest is None or manifest.workflow is None:
        return MAX_REPAIRS
    return min(MAX_REPAIRS, manifest.workflow.max_repairs)


def _repair(state: ProductionWorkflowState) -> dict:
    count = state.get("repair_count", 0) + 1
    return {
        "current_step": f"prepare_npc_app_repair_{count}",
        "repair_count": count,
        "repair_summaries": [
            *state.get("repair_summaries", []),
            _repair_summary(state),
        ],
    }


def _build_repair_evidence(state: ProductionWorkflowState, workspace) -> dict:
    patch = NpcAppPatchResult.model_validate(state["npc_app_patch"])
    report = (
        TestExecutionReport.model_validate(state["npc_app_test_report"])
        if state.get("npc_app_test_report")
        else None
    )
    suite = (
        NpcAppTestSuite.model_validate(state["npc_app_test_suite"])
        if state.get("npc_app_test_suite")
        else None
    )
    review = (
        NpcAppReviewResult.model_validate(state["npc_app_review"])
        if state.get("npc_app_review")
        else None
    )
    current_files = []
    for item in patch.patch.files:
        path = workspace_service.resolve_workspace_path(workspace, item.path)
        content = path.read_text(encoding="utf-8") if path.is_file() else item.content
        current_files.append(
            {
                "path": item.path,
                "sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
                "content": _truncate(content, MAX_REPAIR_FILE_CHARS),
            }
        )
    failure = _failure_summary(report, review)
    test_excerpt = _test_excerpt(suite, report, workspace) if suite and report else None
    return {
        "failure": failure,
        "current_files": current_files,
        "test_excerpt": test_excerpt,
        "repair_history": state.get("repair_summaries", [])[-MAX_REPAIR_HISTORY_ITEMS:],
        "constraints": {
            "allowed_paths": [item.path for item in patch.patch.files],
            "must_return_complete_files": True,
            "max_files": len(patch.patch.files),
        },
    }


def _failure_summary(
    report: TestExecutionReport | None,
    review: NpcAppReviewResult | None,
) -> dict:
    if report and not report.passed:
        return {
            "source": "pytest" if not report.pytest_passed else "compile",
            "candidate_path": report.candidate_path,
            "test_path": report.test_path,
            "errors": report.errors,
            "message": _first_failure_message(report.stdout or report.stderr),
            "stdout": _truncate(report.stdout, MAX_REPAIR_STDOUT_CHARS),
            "stderr": _truncate(report.stderr, MAX_REPAIR_STDOUT_CHARS),
        }
    if review and review.findings:
        blocking = [item for item in review.findings if item.blocking]
        findings = blocking or review.findings
        return {
            "source": "review",
            "findings": [item.model_dump(mode="json") for item in findings[:5]],
        }
    return {"source": "unknown", "message": "Repair the candidate using the latest workspace files."}


def _repair_summary(state: ProductionWorkflowState) -> str:
    report = (
        TestExecutionReport.model_validate(state["npc_app_test_report"])
        if state.get("npc_app_test_report")
        else None
    )
    review = (
        NpcAppReviewResult.model_validate(state["npc_app_review"])
        if state.get("npc_app_review")
        else None
    )
    if report and not report.passed:
        return (
            f"repair_{state.get('repair_count', 0)} failed "
            f"{'pytest' if not report.pytest_passed else 'compile'}: "
            f"{_first_failure_message(report.stdout or report.stderr)}"
        )
    if review and review.findings:
        finding = review.findings[0]
        return f"repair_{state.get('repair_count', 0)} review finding: {finding.title} at {finding.file}:{finding.line}"
    return f"repair_{state.get('repair_count', 0)} requested."


def _test_excerpt(
    suite: NpcAppTestSuite,
    report: TestExecutionReport,
    workspace,
) -> dict | None:
    test_path = workspace_service.resolve_workspace_path(workspace, suite.plan.test_path)
    if not test_path.is_file():
        return None
    lines = test_path.read_text(encoding="utf-8").splitlines()
    line_no = _failure_line(report.stdout, suite.plan.test_path) or _failure_line(
        report.stdout,
        Path(suite.plan.test_path).name,
    )
    if line_no is None:
        line_no = min(len(lines), max(1, len(lines) // 2))
    start = max(1, line_no - TEST_EXCERPT_RADIUS)
    end = min(len(lines), line_no + TEST_EXCERPT_RADIUS)
    return {
        "path": suite.plan.test_path,
        "start_line": start,
        "end_line": end,
        "content": "\n".join(lines[start - 1:end]),
    }


def _failure_line(output: str, path: str) -> int | None:
    normalized = path.replace("/", "\\")
    for line in output.splitlines():
        if path in line or normalized in line:
            try:
                return int(line.rsplit(":", 2)[1])
            except (IndexError, ValueError):
                continue
    return None


def _first_failure_message(output: str) -> str:
    for line in output.splitlines():
        stripped = line.strip()
        if stripped.startswith(("E       ", "E   ", "FAILED ")):
            return stripped[:500]
    return _truncate(output.strip(), 500)


def _truncate(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[:limit]}\n[truncated {len(value) - limit} chars]"


def _manual(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        event_service.record_event(
            db, task_id=state["task_id"], workflow_run_id=state["workflow_run_id"],
            event_type="manual_takeover_required", actor_type="system",
            actor_name="npc_app_feature", payload={"repair_count": state.get("repair_count", 0)},
        )
        db.commit()
    raise CandidateValidationError("npc_app patch exceeded automatic repair limit.")


def _request_approval(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=state["workflow_run_id"],
            step_key="request_npc_app_patch_approval", agent_name="review_agent",
        )
        artifact_id = state["code_patch_artifact_ids"][-1]
        approval = task_service.create_approval(
            db, task,
            ApprovalCreate(
                artifact_id=artifact_id,
                approval_type="npc_app_patch_review",
                risk_summary=f"Multi-file npc_app patch; repairs={state.get('repair_count', 0)}.",
                diff_summary="Review candidate files, generated tests, and review report.",
            ),
        )
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"approval_id": approval.id})
        approval_id = approval.id
    return {"current_step": "request_npc_app_patch_approval", "approval_id": approval_id}


def _wait(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=state["workflow_run_id"],
            step_key="wait_for_npc_app_patch_approval", agent_name="approval_gate",
        )
        if step.status != RunStatus.PAUSED:
            step_service.pause_step(db, task_id=task.id, step=step, output_data={"approval_id": state["approval_id"]})
    decision = interrupt({"approval_id": state["approval_id"], "type": "npc_app_patch_review"})
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.get_latest_step(db, state["workflow_run_id"], "wait_for_npc_app_patch_approval")
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"decision": decision})
    return {"approval_decision": decision}


def _deliver(state: ProductionWorkflowState) -> dict:
    with SessionLocal() as db:
        task = task_service.get_task(db, state["task_id"])
        step = step_service.start_step(
            db, task_id=task.id, workflow_run_id=state["workflow_run_id"],
            step_key="deliver_npc_app_patch", agent_name="system",
        )
        workspace = workspace_service.get_workspace(db, state["workspace_id"])
        _, diff_id = workspace_service.build_diff(db, workspace)
        applied_paths = _apply_workspace_patch_to_source(state, workspace)
        manifest = NpcAppDeliveryManifest(
            task_id=task.id, workflow_run_id=state["workflow_run_id"],
            workspace_id=workspace.id,
            candidate_artifact_ids=state.get("candidate_artifact_ids", []),
            patch_manifest_artifact_ids=state.get("code_patch_artifact_ids", []),
            test_plan_artifact_ids=state.get("test_plan_artifact_ids", []),
            test_patch_artifact_ids=state.get("test_patch_artifact_ids", []),
            test_report_artifact_ids=state.get("validation_artifact_ids", []),
            review_report_artifact_ids=state.get("review_artifact_ids", []),
            diff_artifact_id=diff_id,
            repair_count=state.get("repair_count", 0),
            source_project_modified=True,
            applied_paths=applied_paths,
        )
        artifact = artifact_service.create_text_artifact(
            db, task, step_run_id=step.id, artifact_type="npc_app_delivery_manifest",
            logical_name="npc-app-delivery-manifest.json",
            content=manifest.model_dump_json(indent=2), mime_type="application/json",
            validation_status="valid", approval_status="approved",
        )
        step_service.complete_step(db, task_id=task.id, step=step, output_data={"manifest_artifact_id": artifact.id})
        manifest_artifact_id = artifact.id
    return {
        "current_step": "deliver_npc_app_patch",
        "delivery_manifest_artifact_id": manifest_artifact_id,
        "applied_paths": applied_paths,
        "final_summary": "Approved npc_app patch applied to source.",
    }


def _apply_workspace_patch_to_source(state: ProductionWorkflowState, workspace) -> list[str]:
    patch = NpcAppPatchResult.model_validate(state["npc_app_patch"])
    source_root = Path(workspace.source_root).resolve()
    applied = []
    for relative_path in patch.affected_files:
        if not relative_path.startswith("npc_app/"):
            raise CandidateValidationError("Only npc_app paths can be applied to source.")
        workspace_path = workspace_service.resolve_workspace_path(workspace, relative_path, must_exist=True)
        target = (source_root / relative_path).resolve()
        try:
            target.relative_to(source_root / "npc_app")
        except ValueError as exc:
            raise CandidateValidationError("npc_app patch target escaped source root.") from exc
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(workspace_path.read_bytes())
        applied.append(relative_path)
    return applied


def build_npc_app_feature_graph(checkpointer: BaseCheckpointSaver):
    graph = StateGraph(ProductionWorkflowState)
    for name, node in {
        "initialize": _initialize, "generate": _generate, "test": _test,
        "review": _review, "repair": _repair, "manual": _manual,
        "approval": _request_approval, "wait": _wait, "deliver": _deliver,
    }.items():
        graph.add_node(name, node)
    graph.add_edge(START, "initialize")
    graph.add_edge("initialize", "generate")
    graph.add_edge("generate", "test")
    graph.add_conditional_edges("test", _route_test, {"review": "review", "repair": "repair", "manual": "manual"})
    graph.add_conditional_edges(
        "review",
        _route_review,
        {"approval": "approval", "repair": "repair", "manual": "manual"},
    )
    graph.add_edge("repair", "generate")
    graph.add_edge("approval", "wait")
    graph.add_edge("wait", "deliver")
    graph.add_edge("deliver", END)
    return graph.compile(checkpointer=checkpointer, name="npc_app_feature")
