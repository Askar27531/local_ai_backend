from __future__ import annotations

import json
import time
import uuid
from collections.abc import Iterator
from functools import lru_cache

import yaml
from pydantic import BaseModel, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from skill_app.config import get_settings
from skill_app.db.models import AgentTask, ModelCall, StepRun, WorkflowRun
from skill_app.domain.errors import (
    ConfigurationError,
    DomainError,
    EntityNotFound,
    InvalidStateTransition,
    ModelOutputValidationError,
    ModelProfileNotFound,
    ModelProviderError,
)
from skill_app.domain.status import RunStatus, TaskStatus
from skill_app.schemas.model import ModelInvokeResponse, ModelMessage, ModelProfile, ModelProvider, ProviderResponse
from skill_app.services import artifact_service, event_service, skill_service
from skill_app.services.model_providers import FakeModelProvider, OpenAICompatibleProvider

_providers: dict[str, ModelProvider] = {
    "fake": FakeModelProvider(),
    "openai_compatible": OpenAICompatibleProvider(),
}


@lru_cache(maxsize=1)
def load_profiles() -> dict[str, ModelProfile]:
    path = get_settings().model_profiles_path
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        profiles = raw["profiles"]
    except (OSError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ConfigurationError(
            "Unable to load model profiles.",
            {"path": str(path)},
        ) from exc
    if not isinstance(profiles, dict):
        raise ConfigurationError("Model profiles must be a mapping.", {"path": str(path)})
    try:
        return {
            name: ModelProfile(name=name, **values)
            for name, values in profiles.items()
        }
    except (TypeError, ValidationError) as exc:
        raise ConfigurationError(
            "Model profile configuration is invalid.",
            {"path": str(path)},
        ) from exc


def list_profiles() -> list[ModelProfile]:
    return sorted(load_profiles().values(), key=lambda profile: profile.name)


def get_profile(profile_name: str) -> ModelProfile:
    profile = load_profiles().get(profile_name)
    if profile is None:
        raise ModelProfileNotFound(
            f"Model profile not found: {profile_name}",
            {"profile_name": profile_name},
        )
    return profile


def register_provider(name: str, provider: ModelProvider) -> None:
    _providers[name] = provider


def invoke_text(
    db: Session,
    *,
    task_id: str,
    step_run_id: str | None,
    profile_name: str,
    messages: list[ModelMessage],
    prompt_version: str,
) -> ModelInvokeResponse:
    task, _ = _validate_context(db, task_id, step_run_id)
    profile = get_profile(profile_name)
    response, model_call = _invoke_provider(
        db,
        task=task,
        step_run_id=step_run_id,
        profile=profile,
        messages=messages,
        prompt_version=prompt_version,
    )
    return ModelInvokeResponse(
        text=response.text,
        model_call_id=model_call.id,
        profile_name=profile.name,
        model=profile.model,
        usage=response.usage,
    )


def invoke_structured[T: BaseModel](
    db: Session,
    *,
    task_id: str,
    step_run_id: str | None,
    profile_name: str,
    messages: list[ModelMessage],
    response_model: type[T],
    prompt_version: str,
) -> T:
    task, _ = _validate_context(db, task_id, step_run_id)
    profile = get_profile(profile_name)
    repair_attempts = _model_parse_repair_attempts(db, step_run_id)
    current_messages = list(messages)
    last_error: ValidationError | None = None
    last_call: ModelCall | None = None
    for attempt in range(repair_attempts + 1):
        response, model_call = _invoke_provider(
            db,
            task=task,
            step_run_id=step_run_id,
            profile=profile,
            messages=current_messages,
            prompt_version=(
                prompt_version
                if attempt == 0
                else (
                    f"{prompt_version}:format-repair"
                    if attempt == 1
                    else f"{prompt_version}:format-repair-{attempt}"
                )
            ),
            response_model=response_model,
        )
        try:
            return _validate_structured_response(response_model, response.text)
        except ValidationError as error:
            _record_validation_failure(db, task, model_call, response.text, error)
            last_error = error
            last_call = model_call
            current_messages = [
                *messages,
                ModelMessage(role="assistant", content=response.text),
                ModelMessage(
                    role="user",
                    content=(
                        "Return only valid JSON matching this JSON Schema. "
                        "Do not add markdown fences or commentary.\n"
                        f"{json.dumps(response_model.model_json_schema(), ensure_ascii=False)}"
                    ),
                ),
            ]
    assert last_error is not None and last_call is not None
    raise ModelOutputValidationError(
        "Model output failed structured validation after configured repair attempts.",
        {
            "profile_name": profile.name,
            "model_call_id": last_call.id,
            "repair_attempts": repair_attempts,
            "errors": last_error.errors(include_url=False),
        },
    ) from last_error


def stream_text(
    db: Session,
    *,
    task_id: str,
    step_run_id: str | None,
    profile_name: str,
    messages: list[ModelMessage],
    prompt_version: str,
) -> Iterator[str]:
    task, _ = _validate_context(db, task_id, step_run_id)
    profile = get_profile(profile_name)
    provider = _get_provider(profile)
    input_chars = _input_chars(messages)

    def generate() -> Iterator[str]:
        started = time.perf_counter()
        chunks: list[str] = []
        try:
            for chunk in provider.stream(profile, messages):
                chunks.append(chunk)
                yield chunk
            _create_call(
                db,
                task=task,
                step_run_id=step_run_id,
                profile=profile,
                prompt_version=prompt_version,
                input_chars=input_chars,
                response=ProviderResponse(text="".join(chunks)),
                latency_ms=_latency_ms(started),
                status="succeeded",
            )
        except Exception as exc:
            normalized = _normalize_provider_error(profile, exc)
            _create_call(
                db,
                task=task,
                step_run_id=step_run_id,
                profile=profile,
                prompt_version=prompt_version,
                input_chars=input_chars,
                response=ProviderResponse(text="".join(chunks)),
                latency_ms=_latency_ms(started),
                status="failed",
                error_type=type(normalized).__name__,
            )
            if normalized is exc:
                raise
            raise normalized from exc

    return generate()


def list_model_calls(db: Session, task_id: str) -> list[ModelCall]:
    if db.get(AgentTask, task_id) is None:
        raise EntityNotFound(f"Task not found: {task_id}", {"task_id": task_id})
    stmt = select(ModelCall).where(ModelCall.task_id == task_id).order_by(ModelCall.created_at.asc())
    return list(db.execute(stmt).scalars().all())


def _invoke_provider(
    db: Session,
    *,
    task: AgentTask,
    step_run_id: str | None,
    profile: ModelProfile,
    messages: list[ModelMessage],
    prompt_version: str,
    response_model: type[BaseModel] | None = None,
) -> tuple[ProviderResponse, ModelCall]:
    _enforce_model_call_budget(db, task.id, step_run_id)
    provider = _get_provider(profile)
    started = time.perf_counter()
    input_chars = _input_chars(messages)
    try:
        if response_model is not None and hasattr(provider, "invoke_structured"):
            response = provider.invoke_structured(
                profile,
                messages,
                response_schema=response_model.model_json_schema(),
                schema_name=response_model.__name__,
            )
        else:
            response = provider.invoke(profile, messages)
    except Exception as exc:
        normalized = _normalize_provider_error(profile, exc)
        _create_call(
            db,
            task=task,
            step_run_id=step_run_id,
            profile=profile,
            prompt_version=prompt_version,
            input_chars=input_chars,
            response=ProviderResponse(text=""),
            latency_ms=_latency_ms(started),
            status="failed",
            error_type=type(normalized).__name__,
        )
        if normalized is exc:
            raise
        raise normalized from exc
    model_call = _create_call(
        db,
        task=task,
        step_run_id=step_run_id,
        profile=profile,
        prompt_version=prompt_version,
        input_chars=input_chars,
        response=response,
        latency_ms=_latency_ms(started),
        status="succeeded",
    )
    return response, model_call


def _model_parse_repair_attempts(db: Session, step_run_id: str | None) -> int:
    manifest = _skill_manifest_for_step(db, step_run_id)
    return manifest.retry.model_parse_attempts if manifest is not None else 1


def _enforce_model_call_budget(
    db: Session,
    task_id: str,
    step_run_id: str | None,
) -> None:
    manifest = _skill_manifest_for_step(db, step_run_id)
    if manifest is None or step_run_id is None:
        return
    step = db.get(StepRun, step_run_id)
    assert step is not None
    count = db.execute(
        select(ModelCall.id)
        .join(StepRun, ModelCall.step_run_id == StepRun.id)
        .where(
            ModelCall.task_id == task_id,
            StepRun.workflow_run_id == step.workflow_run_id,
        )
    ).all()
    if len(count) >= manifest.limits.max_model_calls:
        raise ModelOutputValidationError(
            "Skill model-call budget exhausted.",
            {
                "skill_name": manifest.name,
                "max_model_calls": manifest.limits.max_model_calls,
                "workflow_run_id": step.workflow_run_id,
            },
        )


def _skill_manifest_for_step(db: Session, step_run_id: str | None):
    if step_run_id is None:
        return None
    step = db.get(StepRun, step_run_id)
    if step is None:
        return None
    binding = skill_service.manifest_for_workflow(db, step.workflow_run_id)
    return binding[1] if binding is not None else None


def _create_call(
    db: Session,
    *,
    task: AgentTask,
    step_run_id: str | None,
    profile: ModelProfile,
    prompt_version: str,
    input_chars: int,
    response: ProviderResponse,
    latency_ms: float,
    status: str,
    error_type: str | None = None,
) -> ModelCall:
    model_call = ModelCall(
        id=str(uuid.uuid4()),
        task_id=task.id,
        step_run_id=step_run_id,
        profile_name=profile.name,
        provider=profile.provider,
        model=profile.model,
        prompt_version=prompt_version,
        input_chars=input_chars,
        output_chars=len(response.text),
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        latency_ms=latency_ms,
        status=status,
        error_type=error_type,
    )
    db.add(model_call)
    event_service.record_event(
        db,
        task_id=task.id,
        step_run_id=step_run_id,
        event_type="model_call_completed" if status == "succeeded" else "model_call_failed",
        actor_type="model",
        actor_name=profile.name,
        payload={
            "model_call_id": model_call.id,
            "provider": profile.provider,
            "model": profile.model,
            "prompt_version": prompt_version,
            "status": status,
            "latency_ms": latency_ms,
            "error_type": error_type,
        },
    )
    db.commit()
    db.refresh(model_call)
    return model_call


def _record_validation_failure(
    db: Session,
    task: AgentTask,
    model_call: ModelCall,
    raw_output: str,
    error: ValidationError,
) -> None:
    artifact = artifact_service.create_text_artifact(
        db,
        task,
        step_run_id=model_call.step_run_id,
        artifact_type="model_raw_output",
        logical_name=f"model-call-{model_call.id}.txt",
        content=raw_output,
        metadata={
            "model_call_id": model_call.id,
            "profile_name": model_call.profile_name,
            "validation_errors": error.errors(include_url=False),
        },
        validation_status="invalid",
    )
    model_call.status = "validation_failed"
    model_call.error_type = "ModelOutputValidationError"
    model_call.raw_output_artifact_id = artifact.id
    event_service.record_event(
        db,
        task_id=task.id,
        step_run_id=model_call.step_run_id,
        event_type="model_output_validation_failed",
        actor_type="model",
        actor_name=model_call.profile_name,
        payload={
            "model_call_id": model_call.id,
            "raw_output_artifact_id": artifact.id,
            "errors": error.errors(include_url=False),
        },
    )
    db.commit()


def _validate_context(
    db: Session,
    task_id: str,
    step_run_id: str | None,
) -> tuple[AgentTask, StepRun | None]:
    task = db.get(AgentTask, task_id)
    if task is None:
        raise EntityNotFound(f"Task not found: {task_id}", {"task_id": task_id})
    current = TaskStatus(task.status)
    if current not in {TaskStatus.PLANNING, TaskStatus.RUNNING}:
        raise InvalidStateTransition(
            "Models can only be invoked while a task is planning or running.",
            {"task_id": task_id, "current": str(task.status)},
        )
    if step_run_id is None:
        if current == TaskStatus.PLANNING:
            raise InvalidStateTransition(
                "Planning model calls must belong to a planner step.",
                {"task_id": task_id},
            )
        return task, None
    step = db.get(StepRun, step_run_id)
    if step is None:
        raise EntityNotFound(f"Step run not found: {step_run_id}", {"step_run_id": step_run_id})
    run = db.get(WorkflowRun, step.workflow_run_id)
    if run is None or run.task_id != task_id:
        raise InvalidStateTransition("Step run does not belong to the model task.")
    if step.status != RunStatus.RUNNING:
        raise InvalidStateTransition(
            "Models can only be invoked for a running step.",
            {"step_run_id": step_run_id, "current": str(step.status)},
        )
    if current == TaskStatus.PLANNING and step.agent_name != "planner":
        raise InvalidStateTransition(
            "Only the planner agent can invoke models during planning.",
            {"step_run_id": step_run_id, "agent_name": step.agent_name},
        )
    return task, step


def _get_provider(profile: ModelProfile) -> ModelProvider:
    provider = _providers.get(profile.provider)
    if provider is None:
        raise ConfigurationError(
            f"Model provider is not registered: {profile.provider}",
            {"profile_name": profile.name, "provider": profile.provider},
        )
    return provider


def _normalize_provider_error(profile: ModelProfile, error: Exception) -> Exception:
    if isinstance(error, DomainError):
        return error
    return ModelProviderError(
        "Model provider invocation failed.",
        {
            "profile_name": profile.name,
            "provider": profile.provider,
            "error_type": type(error).__name__,
        },
    )


def _input_chars(messages: list[ModelMessage]) -> int:
    return sum(len(message.content) for message in messages)


def _validate_structured_response[T: BaseModel](
    response_model: type[T],
    text: str,
) -> T:
    try:
        return response_model.model_validate_json(text)
    except ValidationError as direct_error:
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            raise direct_error from None
        if isinstance(payload, dict) and len(payload) == 1:
            wrapped = next(iter(payload.values()))
            if isinstance(wrapped, dict):
                return response_model.model_validate(wrapped)
        raise direct_error


def _latency_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
