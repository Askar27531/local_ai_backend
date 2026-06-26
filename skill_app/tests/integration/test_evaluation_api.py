from __future__ import annotations

from fastapi.testclient import TestClient

from skill_app.main import app
from skill_app.services import evaluation_service


def test_evaluation_catalog_and_offline_regression_are_persisted():
    with TestClient(app) as client:
        catalog = client.get("/evaluations/datasets")
        assert catalog.status_code == 200
        assert {item["suite"] for item in catalog.json()} == {
            "planning",
            "config",
            "patch",
            "review",
        }
        assert all(item["cases"] == 10 for item in catalog.json())

        response = client.post(
            "/evaluations/runs",
            json={"suite": "all", "dataset_version": "v1"},
        )
        assert response.status_code == 201, response.text
        run = response.json()
        assert run["status"] == "succeeded"
        assert run["total_cases"] == 40
        assert run["passed_cases"] == 40
        assert run["failed_cases"] == 0
        assert run["dataset_sha256"]
        assert run["summary"]["case_pass_rate"] == 1

        detail = client.get(f"/evaluations/runs/{run['id']}")
        assert detail.status_code == 200
        body = detail.json()
        assert body["results"]
        metric_names = {result["metric_name"] for result in body["results"]}
        assert "plan_structure_valid" in metric_names
        assert "schema_first_pass_rate" in metric_names
        assert "patch_apply_rate" in metric_names
        assert "known_defect_recall" in metric_names

        listed = client.get("/evaluations/runs").json()
        assert listed[0]["id"] == run["id"]

        repeated = client.post(
            "/evaluations/runs",
            json={"suite": "all", "dataset_version": "v1"},
        ).json()
        comparison = repeated["summary"]["comparison"]
        assert comparison["previous_run_id"] == run["id"]
        assert comparison["regressions"] == []
        assert all(delta == 0 for delta in comparison["deltas"].values())


def test_evaluation_aggregation_supports_agent_workflow_and_model_profile():
    with TestClient(app) as client:
        client.post("/evaluations/runs", json={"suite": "planning"})
        task = client.post(
            "/tasks",
            json={
                "project_id": "evaluation-aggregate",
                "title": "Profile aggregation",
                "request": "实现 Python 功能和测试",
            },
        ).json()
        planner = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "planner"},
        )
        assert planner.status_code == 201
        agent = client.get("/evaluations/aggregate", params={"group_by": "agent"})
        workflow = client.get("/evaluations/aggregate", params={"group_by": "workflow"})
        profile = client.get("/evaluations/aggregate", params={"group_by": "model_profile"})

        assert agent.status_code == 200
        assert "planner" in agent.json()["groups"]
        assert workflow.status_code == 200
        assert "planner" in workflow.json()["groups"]
        assert profile.status_code == 200
        assert "local-default" in profile.json()["groups"]


def test_unknown_evaluation_run_returns_404():
    with TestClient(app) as client:
        response = client.get("/evaluations/runs/missing")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "entity_not_found"


def test_failed_regression_exposes_specific_case_details(monkeypatch):
    filename, original_runner = evaluation_service.SUITES["planning"]

    def fail_case(case):
        outcome = original_runner(case)
        outcome["passed"] = False
        outcome["details"]["forced_regression"] = True
        return outcome

    monkeypatch.setitem(evaluation_service.SUITES, "planning", (filename, fail_case))
    with TestClient(app) as client:
        run = client.post("/evaluations/runs", json={"suite": "planning"}).json()
        assert run["failed_cases"] == 10
        failed = run["summary"]["failed_cases"]
        assert failed[0]["case_id"]
        assert failed[0]["details"]["forced_regression"] is True
