from __future__ import annotations

import time

from fastapi.testclient import TestClient

from skill_app.main import app


def create_task(client: TestClient) -> dict:
    response = client.post(
        "/tasks",
        json={
            "project_id": "demo-game",
            "title": "Foundation runtime",
            "request": "Exercise pause and resume.",
        },
    )
    assert response.status_code == 201
    return response.json()


def wait_for_status(client: TestClient, workflow_run_id: str, *statuses: str) -> dict:
    deadline = time.monotonic() + 5
    expected = set(statuses)
    while time.monotonic() < deadline:
        run = client.get(f"/workflow-runs/{workflow_run_id}").json()
        if run["status"] in expected:
            return run
        time.sleep(0.05)
    return client.get(f"/workflow-runs/{workflow_run_id}").json()


def test_foundation_workflow_pauses_and_resumes_after_approval():
    with TestClient(app) as client:
        task = create_task(client)
        started = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        )

        assert started.status_code == 201
        run = wait_for_status(client, started.json()["id"], "paused")
        assert run["status"] == "paused"
        assert run["state_snapshot"]["approval_id"]
        artifact_id = run["state_snapshot"]["artifact_ids"][0]
        artifact = client.get(f"/artifacts/{artifact_id}").json()
        assert artifact["storage_uri"].startswith("local://")
        assert artifact["size_bytes"] > 0
        assert client.get(f"/artifacts/{artifact_id}/content").content.startswith(b"foundation-smoke:")
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "awaiting_artifact_approval"

        steps = client.get(f"/workflow-runs/{run['id']}/steps").json()
        assert [(step["step_key"], step["status"]) for step in steps] == [
            ("initialize", "succeeded"),
            ("create_demo_artifact", "succeeded"),
            ("request_approval", "succeeded"),
            ("wait_for_approval", "paused"),
        ]

        approval_id = run["state_snapshot"]["approval_id"]
        resolved = client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": True, "resolved_by": "runtime-tester"},
        )

        assert resolved.status_code == 200
        completed = wait_for_status(client, run["id"], "succeeded")
        assert completed["status"] == "succeeded"
        assert completed["state_snapshot"]["final_summary"]
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "succeeded"

        completed_steps = client.get(f"/workflow-runs/{run['id']}/steps").json()
        assert [(step["step_key"], step["status"]) for step in completed_steps] == [
            ("initialize", "succeeded"),
            ("create_demo_artifact", "succeeded"),
            ("request_approval", "succeeded"),
            ("wait_for_approval", "succeeded"),
            ("finish", "succeeded"),
        ]
        events = client.get(f"/tasks/{task['id']}/events").json()
        assert "workflow_resumed" in [event["event_type"] for event in events]


def test_checkpoint_resume_works_across_new_test_client_lifespan():
    with TestClient(app) as first_client:
        task = create_task(first_client)
        started = first_client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        ).json()
        run = wait_for_status(first_client, started["id"], "paused")
        approval_id = run["state_snapshot"]["approval_id"]

    with TestClient(app) as second_client:
        resolved = second_client.post(
            f"/approvals/{approval_id}/resolve",
            json={"approved": True, "resolved_by": "restart-tester"},
        )

        assert resolved.status_code == 200
        completed = wait_for_status(second_client, run["id"], "succeeded")
        assert completed["status"] == "succeeded"


def test_rejected_approval_cancels_paused_workflow():
    with TestClient(app) as client:
        task = create_task(client)
        started = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        ).json()
        run = wait_for_status(client, started["id"], "paused")

        resolved = client.post(
            f"/approvals/{run['state_snapshot']['approval_id']}/resolve",
            json={"approved": False, "resolved_by": "producer", "comment": "Reject candidate."},
        )

        assert resolved.status_code == 200
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "cancelled"
        assert client.get(f"/workflow-runs/{run['id']}").json()["status"] == "cancelled"


def test_manual_cancel_stops_paused_workflow():
    with TestClient(app) as client:
        task = create_task(client)
        run = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        ).json()

        cancelled = client.post(
            f"/workflow-runs/{run['id']}/cancel",
            json={"actor_name": "producer"},
        )

        assert cancelled.status_code == 200
        assert cancelled.json()["status"] == "cancelled"
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "cancelled"


def test_task_cancel_also_cancels_active_workflow():
    with TestClient(app) as client:
        task = create_task(client)
        run = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        ).json()

        cancelled_task = client.post(
            f"/tasks/{task['id']}/cancel",
            json={"actor_name": "producer"},
        )

        assert cancelled_task.status_code == 200
        assert cancelled_task.json()["status"] == "cancelled"
        assert client.get(f"/workflow-runs/{run['id']}").json()["status"] == "cancelled"


def test_unknown_workflow_returns_404_without_creating_run():
    with TestClient(app) as client:
        task = create_task(client)

        response = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "missing"},
        )

        assert response.status_code == 404
        assert response.json()["error"]["code"] == "workflow_not_found"
        assert client.get(f"/tasks/{task['id']}/workflow-runs").json() == []


def test_task_cannot_start_a_second_workflow():
    with TestClient(app) as client:
        task = create_task(client)
        first = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        )
        assert first.status_code == 201

        second = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        )

        assert second.status_code == 409
        assert second.json()["error"]["code"] == "invalid_state_transition"
