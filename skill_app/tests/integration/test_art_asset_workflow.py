from __future__ import annotations

from fastapi.testclient import TestClient

from skill_app.main import app
from skill_app.schemas.art import GeneratedImage, VisualBrief
from skill_app.tools.image_generation import FakeImageProvider, register_image_provider


class CountingImageProvider:
    def __init__(self) -> None:
        self.calls = 0
        self.delegate = FakeImageProvider()

    def generate(self, brief: VisualBrief, *, seed: int) -> GeneratedImage:
        self.calls += 1
        return self.delegate.generate(brief, seed=seed)


def teardown_function() -> None:
    register_image_provider(FakeImageProvider())


def create_task(client: TestClient, request: str = "制作一个古代钥匙道具图标") -> dict:
    response = client.post(
        "/tasks",
        json={
            "project_id": "art-demo",
            "title": "Art asset",
            "request": request,
            "task_type": "art_asset",
        },
    )
    assert response.status_code == 201
    return response.json()


def start(client: TestClient, task_id: str):
    return client.post(f"/tasks/{task_id}/start", json={"workflow_name": "art_asset"})


def test_art_asset_workflow_requires_brief_approval_before_generation_and_delivers_manifest():
    provider = CountingImageProvider()
    register_image_provider(provider)
    with TestClient(app) as client:
        task = create_task(client)
        first_pause = start(client, task["id"])
        assert first_pause.status_code == 201, first_pause.text
        first_state = first_pause.json()["state_snapshot"]
        assert first_pause.json()["status"] == "paused"
        assert provider.calls == 0
        brief = client.get(f"/artifacts/{first_state['visual_brief_artifact_id']}").json()
        assert brief["artifact_type"] == "visual_brief"
        assert brief["approval_status"] == "pending"

        approved_brief = client.post(
            f"/approvals/{first_state['approval_id']}/resolve",
            json={"approved": True, "resolved_by": "art-director"},
        )
        assert approved_brief.status_code == 200, approved_brief.text
        second_pause = client.get(f"/workflow-runs/{first_pause.json()['id']}").json()
        second_state = second_pause["state_snapshot"]
        assert second_pause["status"] == "paused"
        assert provider.calls == 3
        assert len(second_state["candidate_artifact_ids"]) == 3
        assert second_state["contact_sheet_artifact_id"]
        assert second_state["validation_report_artifact_id"]

        selected_id = second_state["candidate_artifact_ids"][1]
        approved_selection = client.post(
            f"/approvals/{second_state['approval_id']}/resolve",
            json={
                "approved": True,
                "resolved_by": "art-director",
                "comment": f"candidate={selected_id}",
            },
        )
        assert approved_selection.status_code == 200, approved_selection.text
        completed = client.get(f"/workflow-runs/{first_pause.json()['id']}").json()
        assert completed["status"] == "succeeded"
        assert completed["state_snapshot"]["selected_artifact_id"] == selected_id

        manifest_id = completed["state_snapshot"]["delivery_manifest_artifact_id"]
        manifest = client.get(f"/artifacts/{manifest_id}/content").json()
        assert manifest["selected_artifact_id"] == selected_id
        assert manifest["source_project_modified"] is False
        selected = client.get(f"/artifacts/{selected_id}").json()
        assert selected["approval_status"] == "approved"
        assert selected["artifact_metadata"]["model"] == "fake-image-v1"
        assert selected["artifact_metadata"]["prompt"]
        assert selected["artifact_metadata"]["negative_prompt"]
        assert selected["artifact_metadata"]["seed"]
        assert selected["artifact_metadata"]["width"] == 1024
        assert selected["artifact_metadata"]["alpha"] is True

        artifact_types = {
            artifact["artifact_type"]
            for artifact in client.get(f"/tasks/{task['id']}/artifacts").json()
        }
        assert {
            "visual_brief",
            "art_candidate",
            "image_validation_report",
            "contact_sheet",
            "art_delivery_manifest",
        } <= artifact_types
        event_types = [
            event["event_type"]
            for event in client.get(f"/tasks/{task['id']}/events").json()
        ]
        assert "tool_called" in event_types
        assert "tool_completed" in event_types
        assert "image_generation_started" in event_types
        assert "image_generation_completed" in event_types


def test_rejected_visual_brief_never_invokes_provider():
    provider = CountingImageProvider()
    register_image_provider(provider)
    with TestClient(app) as client:
        task = create_task(client)
        paused = start(client, task["id"]).json()
        rejected = client.post(
            f"/approvals/{paused['state_snapshot']['approval_id']}/resolve",
            json={"approved": False, "resolved_by": "art-director", "comment": "Revise composition."},
        )
        assert rejected.status_code == 200
        assert provider.calls == 0
        assert client.get(f"/tasks/{task['id']}").json()["status"] == "cancelled"
        types = [
            artifact["artifact_type"]
            for artifact in client.get(f"/tasks/{task['id']}/artifacts").json()
        ]
        assert types == ["visual_brief"]
