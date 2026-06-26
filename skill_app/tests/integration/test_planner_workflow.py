from __future__ import annotations

from collections.abc import Iterator

from fastapi.testclient import TestClient

from skill_app.main import app
from skill_app.schemas.model import ModelMessage, ModelProfile, ProviderResponse
from skill_app.services import model_gateway
from skill_app.services.model_providers import FakeModelProvider


class InvalidPlanProvider:
    def invoke(self, profile: ModelProfile, messages: list[ModelMessage]) -> ProviderResponse:
        return ProviderResponse(
            text=(
                '{"goal":"Implement code","task_type":"code","summary":"bad",'
                '"affected_areas":["code"],"relevant_paths":[],"steps":['
                '{"key":"write_code","title":"Write","agent":"planner","skill":null,'
                '"depends_on":[],"required_tools":["write_workspace_file"],'
                '"expected_artifacts":["workspace_file"],"acceptance_criteria":["done"],'
                '"risk_level":"low"}],"global_acceptance_criteria":["done"],'
                '"risk_level":"low","requires_plan_approval":false}'
            )
        )

    def stream(self, profile: ModelProfile, messages: list[ModelMessage]) -> Iterator[str]:
        yield self.invoke(profile, messages).text


def teardown_function() -> None:
    model_gateway.register_provider("fake", FakeModelProvider())


def create_task(client: TestClient, *, request: str, risk_level: str = "low") -> dict:
    response = client.post(
        "/tasks",
        json={
            "project_id": "planner-demo",
            "title": "Planner workflow",
            "request": request,
            "risk_level": risk_level,
        },
    )
    assert response.status_code == 201
    return response.json()


def test_low_risk_planner_generates_two_artifacts_and_stages_steps():
    with TestClient(app) as client:
        task = create_task(client, request="实现 FastAPI 代码并补充自动化测试")
        started = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "planner"},
        )
        assert started.status_code == 201, started.text
        run = started.json()
        assert run["status"] == "succeeded"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "running"

        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        artifact_types = [artifact["artifact_type"] for artifact in artifacts]
        assert artifact_types == ["plan_json", "plan_markdown"]
        json_artifact = next(artifact for artifact in artifacts if artifact["artifact_type"] == "plan_json")
        markdown_artifact = next(
            artifact for artifact in artifacts if artifact["artifact_type"] == "plan_markdown"
        )
        assert client.get(f"/artifacts/{json_artifact['id']}/content").json()["steps"]
        assert b"## Steps" in client.get(f"/artifacts/{markdown_artifact['id']}/content").content

        steps = client.get(f"/workflow-runs/{run['id']}/steps").json()
        planned = [step for step in steps if step["step_key"].startswith("planned_")]
        planned_agents = {step["step_key"]: step["agent_name"] for step in planned}
        assert planned_agents == {
            "planned_generate_candidate": "code_generator",
            "planned_verify_candidate": "test_agent",
            "planned_review_candidate": "review_agent",
        }
        assert all(step["status"] == "pending" for step in planned)
        calls = client.get(f"/tasks/{task['id']}/model-calls").json()
        assert len(calls) == 1
        assert calls[0]["profile_name"] == "local-default"


def test_high_risk_planner_pauses_for_approval_and_resumes():
    with TestClient(app) as client:
        task = create_task(
            client,
            request="实现核心程序代码并执行测试",
            risk_level="high",
        )
        run = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "planner"},
        ).json()
        assert run["status"] == "paused"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "awaiting_plan_approval"
        approval_id = run["state_snapshot"]["approval_id"]

        resolved = client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": True, "resolved_by": "producer"},
        )
        assert resolved.status_code == 200, resolved.text
        completed = client.get(f"/workflow-runs/{run['id']}").json()
        assert completed["status"] == "succeeded"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "running"
        assert completed["state_snapshot"]["final_summary"]


def test_documentation_planner_has_no_code_generator_or_write_tool():
    with TestClient(app) as client:
        task = create_task(client, request="更新 README 文档和部署说明")
        run = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "planner"},
        ).json()
        plan = run["state_snapshot"]["plan"]
        assert all(step["agent"] != "code_generator" for step in plan["steps"])
        assert all(
            "write_workspace_file" not in step["required_tools"]
            for step in plan["steps"]
        )


def test_planner_rejection_cancels_task_and_run():
    with TestClient(app) as client:
        task = create_task(client, request="实现高风险代码", risk_level="high")
        run = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "planner"},
        ).json()
        response = client.post(
            f"/approvals/{run['state_snapshot']['approval_id']}/resolve",
            json={"approved": False, "resolved_by": "producer", "comment": "Replan."},
        )
        assert response.status_code == 200
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "cancelled"
        assert client.get(f"/workflow-runs/{run['id']}").json()["status"] == "cancelled"


def test_semantically_invalid_plan_fails_without_execution_artifacts_or_steps():
    model_gateway.register_provider("fake", InvalidPlanProvider())
    with TestClient(app) as client:
        task = create_task(client, request="Implement code")
        response = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "planner"},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "workflow_execution_error"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "failed"
        assert client.get(f"/tasks/{task['id']}/artifacts").json() == []
        runs = client.get(f"/tasks/{task['id']}/workflow-runs").json()
        steps = client.get(f"/workflow-runs/{runs[0]['id']}/steps").json()
        assert not any(step["step_key"].startswith("planned_") for step in steps)
