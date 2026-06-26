from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient
from pydantic import BaseModel

from skill_app.db.database import SessionLocal
from skill_app.domain.errors import ModelOutputValidationError, ModelProviderError
from skill_app.main import app
from skill_app.schemas.model import ModelMessage, ModelProfile, ModelUsage, ProviderResponse
from skill_app.schemas.task import TaskCreate
from skill_app.schemas.workflow import WorkflowRunCreate
from skill_app.services import model_gateway, step_service, task_service, workflow_service
from skill_app.services.model_providers import FakeModelProvider


class QueueProvider:
    def __init__(self, responses: list[str] | None = None, error: Exception | None = None) -> None:
        self.responses = list(responses or [])
        self.error = error

    def invoke(self, profile: ModelProfile, messages: list[ModelMessage]) -> ProviderResponse:
        if self.error is not None:
            raise self.error
        text = self.responses.pop(0)
        return ProviderResponse(
            text=text,
            usage=ModelUsage(input_tokens=10, output_tokens=5),
        )

    def stream(self, profile: ModelProfile, messages: list[ModelMessage]) -> Iterator[str]:
        yield self.invoke(profile, messages).text


class DemoOutput(BaseModel):
    title: str
    score: int


def create_running_step() -> tuple[str, str]:
    with SessionLocal() as db:
        task = task_service.create_task(
            db,
            TaskCreate(project_id="model-demo", title="Model Gateway", request="Invoke model."),
        )
        run = task_service.create_workflow_run(
            db,
            task,
            WorkflowRunCreate(workflow_name="foundation_smoke"),
        )
        workflow_service.start_run(db, task, run)
        step = step_service.start_step(
            db,
            task_id=task.id,
            workflow_run_id=run.id,
            step_key="model_step",
            agent_name="planner",
        )
        return task.id, step.id


def teardown_function() -> None:
    model_gateway.register_provider("fake", FakeModelProvider())


def test_profile_is_predefined_and_text_call_is_audited():
    task_id, step_id = create_running_step()
    with TestClient(app) as client:
        profiles = client.get("/model-profiles")
        assert profiles.status_code == 200
        assert "local-default" in [profile["name"] for profile in profiles.json()]

        response = client.post(
            "/models/invoke/text",
            json={
                "task_id": task_id,
                "step_run_id": step_id,
                "profile_name": "local-default",
                "messages": [{"role": "user", "content": "hello gateway"}],
                "prompt_version": "test-v1",
            },
        )
        assert response.status_code == 200, response.text
        assert response.json()["text"] == "hello gateway"
        calls = client.get(f"/tasks/{task_id}/model-calls").json()
        assert len(calls) == 1
        assert calls[0]["profile_name"] == "local-default"
        assert calls[0]["prompt_version"] == "test-v1"
        assert calls[0]["status"] == "succeeded"


def test_api_cannot_supply_arbitrary_model_address():
    task_id, step_id = create_running_step()
    with TestClient(app) as client:
        response = client.post(
            "/models/invoke/text",
            json={
                "task_id": task_id,
                "step_run_id": step_id,
                "profile_name": "local-default",
                "base_url": "https://attacker.invalid/v1",
                "messages": [{"role": "user", "content": "blocked"}],
            },
        )
        assert response.status_code == 422


def test_structured_output_repairs_once_and_records_invalid_raw_artifact():
    task_id, step_id = create_running_step()
    model_gateway.register_provider(
        "fake",
        QueueProvider(["not-json", '{"title":"fixed","score":9}']),
    )
    with SessionLocal() as db:
        result = model_gateway.invoke_structured(
            db,
            task_id=task_id,
            step_run_id=step_id,
            profile_name="local-default",
            messages=[ModelMessage(role="user", content="return a demo object")],
            response_model=DemoOutput,
            prompt_version="structured-v1",
        )
        assert result == DemoOutput(title="fixed", score=9)
        calls = model_gateway.list_model_calls(db, task_id)
        assert [call.status for call in calls] == ["validation_failed", "succeeded"]
        assert calls[0].raw_output_artifact_id is not None
        assert calls[1].prompt_version == "structured-v1:format-repair"


def test_structured_output_accepts_single_named_wrapper_without_repair():
    task_id, step_id = create_running_step()
    model_gateway.register_provider(
        "fake",
        QueueProvider(['{"demo_output":{"title":"wrapped","score":7}}']),
    )
    with SessionLocal() as db:
        result = model_gateway.invoke_structured(
            db,
            task_id=task_id,
            step_run_id=step_id,
            profile_name="local-default",
            messages=[ModelMessage(role="user", content="return a demo object")],
            response_model=DemoOutput,
            prompt_version="structured-wrapper-v1",
        )

        assert result == DemoOutput(title="wrapped", score=7)
        calls = model_gateway.list_model_calls(db, task_id)
        assert len(calls) == 1
        assert calls[0].status == "succeeded"


def test_structured_output_fails_after_exactly_one_repair():
    task_id, step_id = create_running_step()
    provider = QueueProvider(["bad-one", "bad-two"])
    model_gateway.register_provider("fake", provider)
    with SessionLocal() as db:
        try:
            model_gateway.invoke_structured(
                db,
                task_id=task_id,
                step_run_id=step_id,
                profile_name="local-default",
                messages=[ModelMessage(role="user", content="return json")],
                response_model=DemoOutput,
                prompt_version="structured-v2",
            )
        except ModelOutputValidationError:
            pass
        else:
            raise AssertionError("Expected structured validation failure.")
        calls = model_gateway.list_model_calls(db, task_id)
        assert len(calls) == 2
        assert all(call.status == "validation_failed" for call in calls)
        assert provider.responses == []


def test_provider_failure_has_uniform_call_record():
    task_id, step_id = create_running_step()
    model_gateway.register_provider("fake", QueueProvider(error=ModelProviderError("offline")))
    with SessionLocal() as db:
        try:
            model_gateway.invoke_text(
                db,
                task_id=task_id,
                step_run_id=step_id,
                profile_name="local-default",
                messages=[ModelMessage(role="user", content="hello")],
                prompt_version="failure-v1",
            )
        except ModelProviderError:
            pass
        else:
            raise AssertionError("Expected provider failure.")
        calls = model_gateway.list_model_calls(db, task_id)
        assert len(calls) == 1
        assert calls[0].status == "failed"
        assert calls[0].error_type == "ModelProviderError"


def test_unexpected_provider_exception_is_normalized():
    task_id, step_id = create_running_step()
    model_gateway.register_provider("fake", QueueProvider(error=ValueError("bad client")))
    with SessionLocal() as db:
        try:
            model_gateway.invoke_text(
                db,
                task_id=task_id,
                step_run_id=step_id,
                profile_name="local-default",
                messages=[ModelMessage(role="user", content="hello")],
                prompt_version="normalized-v1",
            )
        except ModelProviderError as error:
            assert error.details["error_type"] == "ValueError"
        else:
            raise AssertionError("Expected normalized provider failure.")
        assert model_gateway.list_model_calls(db, task_id)[0].error_type == "ModelProviderError"


def test_fake_gateway_can_be_injected_and_streamed():
    task_id, step_id = create_running_step()
    model_gateway.register_provider("fake", QueueProvider(["streamed"]))
    with SessionLocal() as db:
        chunks = list(
            model_gateway.stream_text(
                db,
                task_id=task_id,
                step_run_id=step_id,
                profile_name="local-default",
                messages=[ModelMessage(role="user", content="stream")],
                prompt_version="stream-v1",
            )
        )
        assert chunks == ["streamed"]
        assert model_gateway.list_model_calls(db, task_id)[0].status == "succeeded"
