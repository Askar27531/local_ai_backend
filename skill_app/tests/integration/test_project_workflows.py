from __future__ import annotations

import time

from fastapi.testclient import TestClient

from skill_app.config import get_settings
from skill_app.main import app


def create_task(client: TestClient, *, task_type: str, request: str) -> dict:
    response = client.post(
        "/tasks",
        json={
            "project_id": "guichao-island",
            "title": f"Test {task_type}",
            "request": request,
            "task_type": task_type,
            "risk_level": "low",
            "created_by": "test-suite",
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


def test_npc_app_feature_generates_tests_reviews_and_applies_after_approval():
    with TestClient(app) as client:
        applied_paths: list[str] = []
        task = create_task(
            client,
            task_type="npc_app_feature",
            request="为 npc_app 增加一个隔离的小型状态辅助函数并生成测试。",
        )
        try:
            started = client.post(
                f"/tasks/{task['id']}/start",
                json={"workflow_name": "npc_app_feature", "graph_version": "1"},
            )
            assert started.status_code == 201, started.text
            paused = wait_for_status(client, started.json()["id"], "paused")
            assert paused["status"] == "paused"
            state = paused["state_snapshot"]
            assert state["approval_id"]
            assert state["npc_app_patch"]["affected_files"][0].startswith("npc_app/")
            source_path = (
                get_settings().project_root
                / state["npc_app_patch"]["affected_files"][0]
            )
            assert not source_path.exists()

            artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
            types = {item["artifact_type"] for item in artifacts}
            assert {
                "npc_app_candidate",
                "npc_app_patch_manifest",
                "npc_app_test_plan",
                "npc_app_test_patch",
                "npc_app_test_report",
                "npc_app_review_report",
            } <= types

            approved = client.post(
                f"/approvals/{state['approval_id']}/resolve",
                json={"approved": True, "resolved_by": "maintainer"},
            )
            assert approved.status_code == 200, approved.text
            completed = wait_for_status(client, paused["id"], "succeeded")
            assert completed["status"] == "succeeded"
            manifest = client.get(
                f"/artifacts/{completed['state_snapshot']['delivery_manifest_artifact_id']}/content"
            ).json()
            applied_paths = manifest["applied_paths"]
            assert manifest["source_project_modified"] is True
            assert source_path.exists()
            assert state["npc_app_patch"]["affected_files"][0] in applied_paths
            assert manifest["diff_artifact_id"]
            assert manifest["test_report_artifact_ids"]
            assert manifest["review_report_artifact_ids"]
        finally:
            for relative_path in applied_paths:
                path = get_settings().project_root / relative_path
                if path.exists():
                    path.unlink()


def test_game_text_production_validates_reviews_and_delivers_markdown_candidate():
    with TestClient(app) as client:
        task = create_task(
            client,
            task_type="game_text",
            request="为静潮村增加一条低剧透的清晨集市传闻。",
        )
        started = client.post(
            f"/tasks/{task['id']}/start",
            json={"workflow_name": "game_text_production", "graph_version": "1"},
        )
        assert started.status_code == 201, started.text
        paused = wait_for_status(client, started.json()["id"], "paused")
        state = paused["state_snapshot"]
        assert paused["status"] == "paused"
        assert state["game_text_validation"]["valid"] is True
        assert state["game_text_review"]["approved"] is True
        candidates = state["game_text_candidate"]["candidates"]
        assert candidates
        assert all(item["path"].startswith("game_docs/") for item in candidates)
        source_path = get_settings().project_root / candidates[0]["path"]
        assert not source_path.exists()

        artifacts = client.get(f"/tasks/{task['id']}/artifacts").json()
        types = {item["artifact_type"] for item in artifacts}
        assert {
            "game_text_candidate",
            "game_text_validation_report",
            "lore_review_report",
        } <= types

        approved = client.post(
            f"/approvals/{state['approval_id']}/resolve",
            json={"approved": True, "resolved_by": "narrative-lead"},
        )
        assert approved.status_code == 200, approved.text
        completed = wait_for_status(client, paused["id"], "succeeded")
        manifest = client.get(
            f"/artifacts/{completed['state_snapshot']['delivery_manifest_artifact_id']}/content"
        ).json()
        assert completed["status"] == "succeeded"
        assert manifest["source_project_modified"] is False
        assert manifest["final_candidate_artifact_id"] == state["current_candidate_artifact_id"]
        assert manifest["final_candidate_artifact_ids"] == state["current_candidate_artifact_ids"]
        assert manifest["diff_artifact_id"]


def test_npc_app_skill_package_starts_workflow_and_persists_snapshot():
    with TestClient(app) as client:
        applied_paths: list[str] = []
        task = create_task(
            client,
            task_type="npc_app_feature",
            request="为 npc_app 生成一个隔离的小型状态辅助函数并测试。",
        )
        try:
            started = client.post(
                f"/tasks/{task['id']}/skills/npc-app-feature/start",
                json={"version": "1.0.0"},
            )

            assert started.status_code == 201, started.text
            workflow = wait_for_status(client, started.json()["id"], "paused")
            assert workflow["workflow_name"] == "npc_app_feature"
            assert workflow["status"] == "paused"
            assert workflow["state_snapshot"]["skill_runtime"]["name"] == "npc-app-feature"

            skill_runs = client.get(f"/tasks/{task['id']}/skill-runs").json()
            assert len(skill_runs) == 1
            skill_run = skill_runs[0]
            assert skill_run["status"] == "paused"
            assert skill_run["skill_version"] == "1.0.0"
            assert skill_run["manifest_snapshot"]["limits"]["max_files"] == 5
            assert len(skill_run["package_sha256"]) == 64

            approval_id = workflow["state_snapshot"]["approval_id"]
            resolved = client.post(
                f"/approvals/{approval_id}/resolve",
                json={"approved": True, "resolved_by": "maintainer"},
            )
            assert resolved.status_code == 200, resolved.text
            completed = wait_for_status(client, workflow["id"], "succeeded")
            manifest = client.get(
                f"/artifacts/{completed['state_snapshot']['delivery_manifest_artifact_id']}/content"
            ).json()
            applied_paths = manifest["applied_paths"]
            completed_skill_run = client.get(f"/tasks/{task['id']}/skill-runs").json()[0]
            assert completed_skill_run["status"] == "succeeded"
        finally:
            for relative_path in applied_paths:
                path = get_settings().project_root / relative_path
                if path.exists():
                    path.unlink()


def test_game_text_skill_package_freezes_writer_and_lore_instructions():
    with TestClient(app) as client:
        task = create_task(
            client,
            task_type="game_text",
            request="为静潮村增加一条不包含后期剧透的港口传闻。",
        )
        started = client.post(
            f"/tasks/{task['id']}/skills/game-text-writer/start",
            json={"version": "1.0.0"},
        )

        assert started.status_code == 201, started.text
        workflow = wait_for_status(client, started.json()["id"], "paused")
        assert workflow["workflow_name"] == "game_text_production"
        assert workflow["status"] == "paused"
        runtime = workflow["state_snapshot"]["skill_runtime"]
        assert runtime["name"] == "game-text-writer"
        assert {"game-text-writer", "lore-reviewer"} <= set(runtime["instructions"])
        assert "lore-reviewer" in runtime["component_instruction_sha256s"]

        skill_run = client.get(f"/tasks/{task['id']}/skill-runs").json()[0]
        assert skill_run["status"] == "paused"
        assert {"game-text-writer", "lore-reviewer"} <= set(
            skill_run["instruction_snapshots"]
        )
        assert skill_run["manifest_snapshot"]["stages"]["reviewer"] == {
            "skill": "lore-reviewer",
            "profile": None,
            "output_schema": "game_text_review_result",
        }

        approved = client.post(
            f"/approvals/{workflow['state_snapshot']['approval_id']}/resolve",
            json={"approved": True, "resolved_by": "narrative-lead"},
        )
        assert approved.status_code == 200, approved.text
        wait_for_status(client, workflow["id"], "succeeded")
        completed = client.get(f"/tasks/{task['id']}/skill-runs").json()[0]
        assert completed["status"] == "succeeded"


def test_builtin_workflow_catalog_lists_project_specific_workflows():
    with TestClient(app) as client:
        names = {item["name"] for item in client.get("/workflows").json()}
        assert {"npc_app_feature", "game_text_production"} <= names
