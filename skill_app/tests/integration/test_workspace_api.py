from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from skill_app.config import get_settings
from skill_app.db.database import SessionLocal
from skill_app.db.models import TaskWorkspace
from skill_app.main import app
from skill_app.schemas.task import TaskCreate
from skill_app.schemas.workflow import WorkflowRunCreate
from skill_app.services import step_service, task_service, workflow_service


def create_running_task() -> tuple[str, str, str]:
    with SessionLocal() as db:
        task = task_service.create_task(
            db,
            TaskCreate(project_id="workspace-demo", title="Workspace", request="Edit isolated files."),
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
            step_key="workspace_step",
            agent_name="code_generator",
        )
        return task.id, run.id, step.id


def test_workspace_copy_diff_artifact_and_cleanup_are_isolated():
    task_id, _, _ = create_running_task()
    source = get_settings().project_root / "skill_app" / "README.md"
    original = source.read_bytes()
    with TestClient(app) as client:
        created = client.post(
            f"/tasks/{task_id}/workspaces",
            json={"source_root": ".", "strategy": "copy_subset"},
        )
        assert created.status_code == 201, created.text
        workspace = created.json()
        workspace_root = Path(workspace["workspace_root"])
        assert workspace_root.is_relative_to(get_settings().workspace_root.resolve())

        copied = client.post(
            f"/workspaces/{workspace['id']}/files",
            json={"paths": ["skill_app/README.md"]},
        )
        assert copied.status_code == 200, copied.text
        candidate = workspace_root / "skill_app" / "README.md"
        candidate.write_text("isolated candidate\n", encoding="utf-8")
        assert source.read_bytes() == original

        diff = client.post(f"/workspaces/{workspace['id']}/diff")
        assert diff.status_code == 200, diff.text
        body = diff.json()
        assert body["changed"]
        assert body["changed_paths"] == ["skill_app/README.md"]
        assert "isolated candidate" in body["diff"]
        artifact_id = body["artifact_id"]
        artifact_content = client.get(f"/artifacts/{artifact_id}/content")
        assert artifact_content.status_code == 200
        assert b"isolated candidate" in artifact_content.content

        cleaned = client.post(f"/workspaces/{workspace['id']}/cleanup")
        assert cleaned.status_code == 200
        assert cleaned.json()["status"] == "cleaned"
        assert not workspace_root.exists()
        assert client.get(f"/artifacts/{artifact_id}/content").status_code == 200


def test_workspaces_are_task_isolated_and_only_one_can_be_active():
    first_task, _, _ = create_running_task()
    second_task, _, _ = create_running_task()
    with TestClient(app) as client:
        first = client.post(f"/tasks/{first_task}/workspaces", json={})
        second = client.post(f"/tasks/{second_task}/workspaces", json={})
        assert first.status_code == 201
        assert second.status_code == 201
        assert first.json()["workspace_root"] != second.json()["workspace_root"]

        duplicate = client.post(f"/tasks/{first_task}/workspaces", json={})
        assert duplicate.status_code == 409
        assert duplicate.json()["error"]["code"] == "invalid_state_transition"


def test_workspace_rejects_source_escape_and_ignored_paths():
    task_id, _, _ = create_running_task()
    with TestClient(app) as client:
        escaped = client.post(
            f"/tasks/{task_id}/workspaces",
            json={"source_root": ".."},
        )
        assert escaped.status_code == 400
        assert escaped.json()["error"]["code"] == "unsafe_path"

        workspace = client.post(f"/tasks/{task_id}/workspaces", json={}).json()
        ignored = client.post(
            f"/workspaces/{workspace['id']}/files",
            json={"paths": [".skill_app_data/skill_app.sqlite3"]},
        )
        assert ignored.status_code == 400
        assert ignored.json()["error"]["code"] == "unsafe_path"


def test_cleanup_refuses_a_tampered_workspace_path():
    task_id, _, _ = create_running_task()
    with TestClient(app) as client:
        workspace = client.post(f"/tasks/{task_id}/workspaces", json={}).json()
        original_root = Path(workspace["workspace_root"])
        with SessionLocal() as db:
            record = db.get(TaskWorkspace, workspace["id"])
            assert record is not None
            record.workspace_root = str(get_settings().workspace_root.resolve())
            db.commit()

        response = client.post(f"/workspaces/{workspace['id']}/cleanup")
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "unsafe_path"
        assert original_root.exists()


def test_workspace_diff_detects_new_and_deleted_files():
    task_id, _, _ = create_running_task()
    with TestClient(app) as client:
        workspace = client.post(f"/tasks/{task_id}/workspaces", json={}).json()
        copied = client.post(
            f"/workspaces/{workspace['id']}/files",
            json={"paths": ["skill_app/README.md"]},
        )
        assert copied.status_code == 200
        root = Path(workspace["workspace_root"])
        (root / "skill_app" / "README.md").unlink()
        (root / "new.txt").write_text("new file\n", encoding="utf-8")

        diff = client.post(f"/workspaces/{workspace['id']}/diff").json()
        assert diff["changed_paths"] == ["new.txt", "skill_app/README.md"]
        assert "--- a/new.txt" in diff["diff"]
        assert "+++ b/skill_app/README.md" in diff["diff"]
