from __future__ import annotations

import json
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from skill_app.api.evaluations import router as evaluations_router
from skill_app.config import get_settings
from skill_app.db.database import get_db, init_db
from skill_app.domain.errors import (
    ApprovalRequired,
    ArtifactValidationError,
    DomainError,
    EntityNotFound,
    InvalidStateTransition,
    ModelProfileNotFound,
    ModelProviderError,
    ModelTimeoutError,
    ToolNotFound,
    ToolPermissionDenied,
    WorkflowNotFound,
)
from skill_app.domain.status import RunStatus
from skill_app.schemas.approval import ApprovalCreate, ApprovalDecision, ApprovalResponse
from skill_app.schemas.artifact import ArtifactResponse, ArtifactTextCreate
from skill_app.schemas.event import TaskEventResponse
from skill_app.schemas.model import (
    ModelCallResponse,
    ModelInvokeRequest,
    ModelInvokeResponse,
    ModelProfile,
)
from skill_app.schemas.skill import (
    SkillInfo,
    SkillPackageInfo,
    SkillRunResponse,
    SkillStartRequest,
)
from skill_app.schemas.step import StepRunResponse
from skill_app.schemas.task import TaskCancelRequest, TaskCreate, TaskResponse
from skill_app.schemas.tool import ToolDefinition, ToolExecuteRequest, ToolExecutionResponse
from skill_app.schemas.workflow import (
    WorkflowCancelRequest,
    WorkflowInfo,
    WorkflowResumeRequest,
    WorkflowRunResponse,
    WorkflowStartRequest,
)
from skill_app.schemas.workspace import (
    WorkspaceCleanupResponse,
    WorkspaceCopyRequest,
    WorkspaceCreate,
    WorkspaceDiffResponse,
    WorkspaceResponse,
)
from skill_app.services import (
    artifact_service,
    event_service,
    model_gateway,
    skill_service,
    step_service,
    task_service,
    workflow_service,
    workspace_service,
)
from skill_app.services.skill_loader import (
    discover_skill_packages,
    discover_skills,
)
from skill_app.services.skill_runtime import prepare_skill_start
from skill_app.tools.builtin import register_builtin_tools
from skill_app.tools.registry import tool_registry
from skill_app.tools.runtime import execute_tool
from skill_app.workflows.builtin import register_builtin_workflows
from skill_app.workflows.registry import workflow_registry
from skill_app.workflows.runtime import (
    cancel_workflow,
    enqueue_workflow_resume,
    enqueue_workflow_run,
    prepare_workflow_resume,
    prepare_workflow_start,
)

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    register_builtin_workflows()
    register_builtin_tools()
    yield


app = FastAPI(
    title=settings.app_name,
    description="Foundation for task, workflow, skill, tool, artifact, and approval orchestration.",
    version=settings.app_version,
    lifespan=lifespan,
)
app.include_router(evaluations_router)


@app.exception_handler(DomainError)
def handle_domain_error(_: Request, exc: DomainError) -> JSONResponse:
    if isinstance(exc, (EntityNotFound, WorkflowNotFound, ToolNotFound, ModelProfileNotFound)):
        status_code = 404
    elif isinstance(exc, ToolPermissionDenied):
        status_code = 403
    elif isinstance(exc, (InvalidStateTransition, ApprovalRequired)):
        status_code = 409
    elif isinstance(exc, ModelTimeoutError):
        status_code = 504
    elif isinstance(exc, ModelProviderError):
        status_code = 502
    else:
        status_code = 400
    return JSONResponse(
        status_code=status_code,
        content={
            "error": {
                "code": exc.code,
                "message": exc.message,
                "details": exc.details,
            }
        },
    )


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "app": "skill_app", "version": app.version}


@app.get("/skills", response_model=list[SkillInfo])
def list_skills() -> list[SkillInfo]:
    return discover_skills()


@app.get("/skill-packages", response_model=list[SkillPackageInfo])
def list_skill_packages() -> list[SkillPackageInfo]:
    return discover_skill_packages()


@app.get("/tools", response_model=list[ToolDefinition])
def list_tools() -> list[ToolDefinition]:
    register_builtin_tools()
    return tool_registry.list_definitions()


@app.get("/model-profiles", response_model=list[ModelProfile])
def list_model_profiles():
    return model_gateway.list_profiles()


@app.post("/models/invoke/text", response_model=ModelInvokeResponse)
def invoke_model_text(
    payload: ModelInvokeRequest,
    db: Session = Depends(get_db),
):
    return model_gateway.invoke_text(
        db,
        task_id=payload.task_id,
        step_run_id=payload.step_run_id,
        profile_name=payload.profile_name,
        messages=payload.messages,
        prompt_version=payload.prompt_version,
    )


@app.post(
    "/tasks/{task_id}/tools/{tool_name}/execute",
    response_model=ToolExecutionResponse,
)
def execute_task_tool(
    task_id: str,
    tool_name: str,
    payload: ToolExecuteRequest,
    db: Session = Depends(get_db),
):
    result = execute_tool(
        db,
        task_id=task_id,
        workflow_run_id=payload.workflow_run_id,
        step_run_id=payload.step_run_id,
        agent_name=payload.agent_name,
        tool_name=tool_name,
        arguments=payload.arguments,
    )
    return ToolExecutionResponse(tool_name=tool_name, result=result)


@app.get("/workflows", response_model=list[WorkflowInfo])
def list_workflows() -> list[WorkflowInfo]:
    register_builtin_workflows()
    return workflow_registry.list()


@app.post("/tasks", response_model=TaskResponse, status_code=201)
def create_task(payload: TaskCreate, db: Session = Depends(get_db)):
    return task_service.create_task(db, payload)


@app.get("/tasks", response_model=list[TaskResponse])
def list_tasks(
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    return task_service.list_tasks(db, limit)


@app.get("/tasks/{task_id}", response_model=TaskResponse)
def get_task(task_id: str, db: Session = Depends(get_db)):
    return task_service.get_task(db, task_id)


@app.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
def cancel_task(
    task_id: str,
    payload: TaskCancelRequest,
    db: Session = Depends(get_db),
):
    task = task_service.get_task(db, task_id)
    active_run = workflow_service.get_latest_active_run(db, task_id)
    if active_run is not None:
        cancel_workflow(db, active_run.id, payload.actor_name)
        db.expire_all()
        return task_service.get_task(db, task_id)
    return task_service.cancel_task(db, task, payload.actor_name)


@app.get("/tasks/{task_id}/events", response_model=list[TaskEventResponse])
def list_task_events(
    task_id: str,
    limit: int = Query(default=500, ge=1, le=2000),
    db: Session = Depends(get_db),
):
    task_service.get_task(db, task_id)
    return event_service.list_task_events(db, task_id, limit)


@app.get("/tasks/{task_id}/model-calls", response_model=list[ModelCallResponse])
def list_task_model_calls(task_id: str, db: Session = Depends(get_db)):
    return model_gateway.list_model_calls(db, task_id)


@app.post("/tasks/{task_id}/start", response_model=WorkflowRunResponse, status_code=201)
def start_task_workflow(
    task_id: str,
    payload: WorkflowStartRequest,
    db: Session = Depends(get_db),
):
    run = prepare_workflow_start(db, task_id, payload.workflow_name, payload.graph_version)
    enqueue_workflow_run(run.id)
    return run


@app.post(
    "/tasks/{task_id}/skills/{skill_name}/start",
    response_model=WorkflowRunResponse,
    status_code=201,
)
def start_task_skill(
    task_id: str,
    skill_name: str,
    payload: SkillStartRequest,
    db: Session = Depends(get_db),
):
    run = prepare_skill_start(
        db,
        task_id=task_id,
        skill_name=skill_name,
        request=payload,
    )
    enqueue_workflow_run(run.id)
    return run


@app.get("/tasks/{task_id}/skill-runs", response_model=list[SkillRunResponse])
def list_task_skill_runs(
    task_id: str,
    db: Session = Depends(get_db),
):
    task_service.get_task(db, task_id)
    return skill_service.list_skill_runs(db, task_id)


@app.get("/tasks/{task_id}/workflow-runs", response_model=list[WorkflowRunResponse])
def list_workflow_runs(task_id: str, db: Session = Depends(get_db)):
    task_service.get_task(db, task_id)
    return task_service.list_workflow_runs(db, task_id)


@app.post("/tasks/{task_id}/workspaces", response_model=WorkspaceResponse, status_code=201)
def create_task_workspace(
    task_id: str,
    payload: WorkspaceCreate,
    db: Session = Depends(get_db),
):
    task = task_service.get_task(db, task_id)
    return workspace_service.create_workspace(
        db,
        task,
        source_root=payload.source_root,
        strategy=payload.strategy,
        base_revision=payload.base_revision,
    )


@app.get("/tasks/{task_id}/workspaces", response_model=list[WorkspaceResponse])
def list_task_workspaces(task_id: str, db: Session = Depends(get_db)):
    task_service.get_task(db, task_id)
    return workspace_service.list_task_workspaces(db, task_id)


@app.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def get_task_workspace(workspace_id: str, db: Session = Depends(get_db)):
    return workspace_service.get_workspace(db, workspace_id)


@app.post("/workspaces/{workspace_id}/files", response_model=WorkspaceResponse)
def copy_workspace_files(
    workspace_id: str,
    payload: WorkspaceCopyRequest,
    db: Session = Depends(get_db),
):
    workspace = workspace_service.get_workspace(db, workspace_id)
    return workspace_service.copy_files(db, workspace, payload.paths)


@app.post("/workspaces/{workspace_id}/diff", response_model=WorkspaceDiffResponse)
def build_workspace_diff(workspace_id: str, db: Session = Depends(get_db)):
    workspace = workspace_service.get_workspace(db, workspace_id)
    result, artifact_id = workspace_service.build_diff(db, workspace)
    return WorkspaceDiffResponse(
        workspace_id=workspace.id,
        changed=result.changed,
        changed_paths=result.changed_paths,
        diff=result.content,
        artifact_id=artifact_id,
    )


@app.post("/workspaces/{workspace_id}/cleanup", response_model=WorkspaceCleanupResponse)
def cleanup_task_workspace(workspace_id: str, db: Session = Depends(get_db)):
    workspace = workspace_service.cleanup_workspace(
        db,
        workspace_service.get_workspace(db, workspace_id),
    )
    return WorkspaceCleanupResponse(
        workspace_id=workspace.id,
        status=workspace.status,
        cleaned_at=workspace.cleaned_at,
    )


@app.get("/workflow-runs/{workflow_run_id}", response_model=WorkflowRunResponse)
def get_workflow_run(workflow_run_id: str, db: Session = Depends(get_db)):
    return workflow_service.get_workflow_run(db, workflow_run_id)


@app.get("/workflow-runs/{workflow_run_id}/steps", response_model=list[StepRunResponse])
def list_workflow_steps(workflow_run_id: str, db: Session = Depends(get_db)):
    workflow_service.get_workflow_run(db, workflow_run_id)
    return step_service.list_steps(db, workflow_run_id)


@app.post("/workflow-runs/{workflow_run_id}/resume", response_model=WorkflowRunResponse)
def resume_task_workflow(
    workflow_run_id: str,
    payload: WorkflowResumeRequest,
    db: Session = Depends(get_db),
):
    run = prepare_workflow_resume(db, workflow_run_id)
    enqueue_workflow_resume(workflow_run_id, payload.resume_value)
    return run


@app.post("/workflow-runs/{workflow_run_id}/cancel", response_model=WorkflowRunResponse)
def cancel_task_workflow(
    workflow_run_id: str,
    payload: WorkflowCancelRequest,
    db: Session = Depends(get_db),
):
    return cancel_workflow(db, workflow_run_id, payload.actor_name)


@app.post("/tasks/{task_id}/artifacts/text", response_model=ArtifactResponse, status_code=201)
def create_text_artifact(
    task_id: str,
    payload: ArtifactTextCreate,
    db: Session = Depends(get_db),
):
    task = task_service.get_task(db, task_id)
    return artifact_service.create_text_artifact(
        db,
        task,
        artifact_type=payload.artifact_type,
        logical_name=payload.logical_name,
        content=payload.content,
        step_run_id=payload.step_run_id,
        mime_type=payload.mime_type,
        metadata=payload.metadata,
        validation_status=payload.validation_status,
        approval_status=payload.approval_status,
    )


@app.post("/tasks/{task_id}/artifacts/upload", response_model=ArtifactResponse, status_code=201)
async def upload_artifact(
    task_id: str,
    artifact_type: str = Form(...),
    file: UploadFile = File(...),
    logical_name: str | None = Form(default=None),
    step_run_id: str | None = Form(default=None),
    metadata_json: str = Form(default="{}"),
    validation_status: str = Form(default="pending"),
    approval_status: str = Form(default="not_required"),
    db: Session = Depends(get_db),
):
    try:
        metadata = json.loads(metadata_json)
    except json.JSONDecodeError as exc:
        raise ArtifactValidationError(
            "metadata_json must be a JSON object.",
            {"metadata_json": metadata_json},
        ) from exc
    if not isinstance(metadata, dict):
        raise ArtifactValidationError("metadata_json must decode to an object.")
    content = await file.read()
    task = task_service.get_task(db, task_id)
    return artifact_service.create_bytes_artifact(
        db,
        task,
        artifact_type=artifact_type,
        logical_name=logical_name or file.filename or "upload.bin",
        content=content,
        step_run_id=step_run_id,
        mime_type=file.content_type,
        metadata=metadata,
        validation_status=validation_status,
        approval_status=approval_status,
    )


@app.get("/tasks/{task_id}/artifacts", response_model=list[ArtifactResponse])
def list_artifacts(task_id: str, db: Session = Depends(get_db)):
    task_service.get_task(db, task_id)
    return artifact_service.list_artifacts(db, task_id)


@app.get("/artifacts/{artifact_id}", response_model=ArtifactResponse)
def get_artifact(artifact_id: str, db: Session = Depends(get_db)):
    return artifact_service.get_artifact(db, artifact_id)


@app.get("/artifacts/{artifact_id}/content")
def get_artifact_content(artifact_id: str, db: Session = Depends(get_db)):
    artifact = artifact_service.get_artifact(db, artifact_id)
    artifact_service.read_artifact_bytes(artifact)
    path = artifact_service.resolve_artifact_path(artifact)
    return FileResponse(
        path=path,
        media_type=artifact.mime_type,
        filename=artifact.logical_name,
    )


@app.post("/tasks/{task_id}/approvals", response_model=ApprovalResponse, status_code=201)
def create_approval(
    task_id: str,
    payload: ApprovalCreate,
    db: Session = Depends(get_db),
):
    task = task_service.get_task(db, task_id)
    return task_service.create_approval(db, task, payload)


@app.post("/approvals/{approval_id}/resolve", response_model=ApprovalResponse)
def resolve_approval(
    approval_id: str,
    payload: ApprovalDecision,
    db: Session = Depends(get_db),
):
    approval = task_service.resolve_approval(db, approval_id, payload)
    run = workflow_service.get_latest_active_run(db, approval.task_id)
    if payload.approved and run is not None and run.status == RunStatus.PAUSED:
        resume_value = {
            "approved": True,
            "approval_id": approval.id,
            "resolved_by": approval.resolved_by,
            "comment": approval.comment,
        }
        prepare_workflow_resume(db, run.id)
        enqueue_workflow_resume(run.id, resume_value)
    elif not payload.approved and run is not None:
        workflow_service.cancel_run(
            db,
            task_service.get_task(db, approval.task_id),
            run,
            approval.resolved_by or "unknown",
        )
        skill_service.update_skill_run_status(db, run.id, RunStatus.CANCELLED)
    return approval
