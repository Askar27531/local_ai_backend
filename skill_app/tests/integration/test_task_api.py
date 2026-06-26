from __future__ import annotations

from fastapi.testclient import TestClient

from skill_app.main import app


def task_payload() -> dict[str, str]:
    return {
        "project_id": "demo-game",
        "title": "Create NPC behavior feature",
        "request": "Generate configuration, code, tests, and review artifacts.",
    }


def create_task(client: TestClient) -> dict:
    response = client.post("/tasks", json=task_payload())
    assert response.status_code == 201
    return response.json()


def test_health_and_registries():
    with TestClient(app) as client:
        health = client.get("/health")

        assert health.status_code == 200
        assert health.json()["version"] == "0.22.0"
        skills = client.get("/skills").json()
        assert [skill["name"] for skill in skills] == [
            "art-asset-generator",
            "code-generator",
            "code-reviewer",
            "game-config-generator",
            "game-feature-planning",
            "game-text-writer",
            "lore-reviewer",
            "npc-app-feature",
            "test-generator",
        ]
        packages = client.get("/skill-packages").json()
        assert [package["name"] for package in packages] == [
            "art-asset-generator",
            "code-generator",
            "code-reviewer",
            "game-config-generator",
            "game-feature-planning",
            "game-text-writer",
            "lore-reviewer",
            "npc-app-feature",
            "test-generator",
        ]
        npc_package = next(
            package for package in packages if package["name"] == "npc-app-feature"
        )
        assert npc_package["workflow_name"] == "npc_app_feature"
        assert npc_package["output_schema"] == "npc_app_patch_result"
        tools = client.get("/tools").json()
        assert "read_project_file" in [tool["name"] for tool in tools]
        assert client.get("/workflows").json() == [
            {
                "name": "art_asset",
                "version": "1",
                "description": (
                    "Create and approve a visual brief, generate candidates, "
                    "validate, select, and deliver art."
                ),
            },
            {
                "name": "foundation_smoke",
                "version": "1",
                "description": "Create a demo artifact, pause for approval, resume, and complete.",
            },
                {
                    "name": "game_feature",
                    "version": "1",
                    "description": "Plan, generate, validate, repair, review, approve, and deliver a game feature.",
                },
                {
                    "name": "game_text_production",
                    "version": "1",
                    "description": "Generate, validate, review, repair, approve, and deliver game_docs text.",
                },
                {
                    "name": "npc_app_feature",
                    "version": "1",
                    "description": "Generate, test, review, repair, approve, and deliver a multi-file npc_app patch.",
                },
                {
                    "name": "planner",
                "version": "1",
                "description": "Generate, validate, persist, and optionally approve a structured task plan.",
            },
        ]


def test_create_list_get_task_and_initial_event():
    with TestClient(app) as client:
        task = create_task(client)

        assert task["status"] == "draft"
        assert task["risk_level"] == "low"

        listed = client.get("/tasks")
        assert listed.status_code == 200
        assert [item["id"] for item in listed.json()] == [task["id"]]

        fetched = client.get(f"/tasks/{task['id']}")
        assert fetched.status_code == 200
        assert fetched.json() == task

        events = client.get(f"/tasks/{task['id']}/events").json()
        assert [event["event_type"] for event in events] == ["task_created"]
        assert events[0]["actor_name"] == "local-user"


def test_get_missing_task_returns_structured_404():
    with TestClient(app) as client:
        response = client.get("/tasks/not-found")

        assert response.status_code == 404
        assert response.json() == {
            "error": {
                "code": "entity_not_found",
                "message": "Task not found: not-found",
                "details": {"task_id": "not-found"},
            }
        }


def test_cancel_is_idempotent_and_cancelled_task_cannot_start_workflow():
    with TestClient(app) as client:
        task = create_task(client)

        first = client.post(
            f"/tasks/{task['id']}/cancel",
            json={"actor_name": "producer"},
        )
        assert first.status_code == 200
        assert first.json()["status"] == "cancelled"

        second = client.post(
            f"/tasks/{task['id']}/cancel",
            json={"actor_name": "producer"},
        )
        assert second.status_code == 200
        assert second.json()["status"] == "cancelled"

        workflow = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "foundation_smoke"},
        )
        assert workflow.status_code == 409

        events = client.get(f"/tasks/{task['id']}/events").json()
        assert [event["event_type"] for event in events] == [
            "task_created",
            "task_status_changed",
            "task_cancelled",
        ]


def test_draft_skill_package_cannot_start_and_version_is_pinned():
    with TestClient(app) as client:
        draft_task = create_task(client)
        draft = client.post(
            f"/tasks/{draft_task['id']}/skills/code-generator/start",
            json={"version": "1.0.0"},
        )
        assert draft.status_code == 400
        assert draft.json()["error"]["code"] == "configuration_error"

        version_task = create_task(client)
        mismatch = client.post(
            f"/tasks/{version_task['id']}/skills/npc-app-feature/start",
            json={"version": "2.0.0"},
        )
        assert mismatch.status_code == 400
        assert mismatch.json()["error"]["code"] == "configuration_error"
