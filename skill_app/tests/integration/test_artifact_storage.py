from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from skill_app.config import get_settings
from skill_app.db.database import SessionLocal
from skill_app.domain.errors import ArtifactStorageError
from skill_app.main import app
from skill_app.schemas.task import TaskCreate
from skill_app.schemas.workflow import WorkflowRunCreate
from skill_app.services import artifact_service, task_service, workflow_service


def create_running_task() -> str:
    with SessionLocal() as db:
        task = task_service.create_task(
            db,
            TaskCreate(project_id="artifact-demo", title="Artifact test", request="Create artifacts."),
        )
        run = task_service.create_workflow_run(
            db,
            task,
            WorkflowRunCreate(workflow_name="foundation_smoke"),
        )
        workflow_service.start_run(db, task, run)
        return task.id


def test_text_artifact_has_real_content_hash_size_and_automatic_versions():
    task_id = create_running_task()
    with TestClient(app) as client:
        first = client.post(
            f"/tasks/{task_id}/artifacts/text",
            json={
                "artifact_type": "design_config",
                "logical_name": "npc_rules.yaml",
                "content": "version: 1\n",
                "metadata": {"generator": "test"},
            },
        )
        second = client.post(
            f"/tasks/{task_id}/artifacts/text",
            json={
                "artifact_type": "design_config",
                "logical_name": "npc_rules.yaml",
                "content": "version: 2\n",
            },
        )

        assert first.status_code == 201, first.text
        assert second.status_code == 201, second.text
        first_artifact = first.json()
        second_artifact = second.json()
        assert first_artifact["version"] == 1
        assert second_artifact["version"] == 2
        assert first_artifact["size_bytes"] == len(b"version: 1\n")
        assert first_artifact["storage_uri"].startswith("local://")
        assert first_artifact["sha256"] != second_artifact["sha256"]

        content = client.get(f"/artifacts/{second_artifact['id']}/content")
        assert content.status_code == 200
        assert content.content == b"version: 2\n"

        listed = client.get(f"/tasks/{task_id}/artifacts").json()
        assert [item["version"] for item in listed] == [1, 2]


def test_uploaded_artifact_uses_file_content_and_metadata():
    task_id = create_running_task()
    with TestClient(app) as client:
        response = client.post(
            f"/tasks/{task_id}/artifacts/upload",
            data={
                "artifact_type": "image",
                "logical_name": "icon.bin",
                "metadata_json": '{"source":"upload-test"}',
            },
            files={"file": ("source.bin", b"\x00\x01\x02", "application/octet-stream")},
        )

        assert response.status_code == 201, response.text
        artifact = response.json()
        assert artifact["logical_name"] == "icon.bin"
        assert artifact["size_bytes"] == 3
        assert artifact["artifact_metadata"] == {"source": "upload-test"}
        assert client.get(f"/artifacts/{artifact['id']}/content").content == b"\x00\x01\x02"


def test_artifact_api_rejects_unsafe_name_and_invalid_metadata():
    task_id = create_running_task()
    with TestClient(app) as client:
        unsafe = client.post(
            f"/tasks/{task_id}/artifacts/text",
            json={
                "artifact_type": "report",
                "logical_name": "../report.md",
                "content": "unsafe",
            },
        )
        invalid_metadata = client.post(
            f"/tasks/{task_id}/artifacts/upload",
            data={"artifact_type": "report", "metadata_json": "[]"},
            files={"file": ("report.md", b"ok", "text/markdown")},
        )

        assert unsafe.status_code == 400
        assert unsafe.json()["error"]["code"] == "unsafe_path"
        assert invalid_metadata.status_code == 400
        assert invalid_metadata.json()["error"]["code"] == "artifact_validation_error"


def test_artifact_size_limit_is_enforced():
    task_id = create_running_task()
    settings = get_settings()
    original_limit = settings.max_artifact_bytes
    settings.max_artifact_bytes = 2
    try:
        with TestClient(app) as client:
            response = client.post(
                f"/tasks/{task_id}/artifacts/text",
                json={
                    "artifact_type": "report",
                    "logical_name": "too-large.md",
                    "content": "three",
                },
            )
    finally:
        settings.max_artifact_bytes = original_limit

    assert response.status_code == 400
    assert response.json()["error"]["code"] == "artifact_validation_error"


def test_database_failure_removes_written_file(monkeypatch):
    task_id = create_running_task()
    artifact_root = get_settings().artifact_root
    before = set(artifact_root.rglob("*"))

    with SessionLocal() as db:
        task = task_service.get_task(db, task_id)

        def fail_commit():
            raise RuntimeError("database unavailable")

        monkeypatch.setattr(db, "commit", fail_commit)
        with pytest.raises(ArtifactStorageError):
            artifact_service.create_text_artifact(
                db,
                task,
                artifact_type="report",
                logical_name="rollback.md",
                content="must be removed",
            )

    after_files = {path for path in artifact_root.rglob("*") if path.is_file()}
    before_files = {path for path in before if Path(path).is_file()}
    assert after_files == before_files


def test_download_detects_tampered_content():
    task_id = create_running_task()
    with SessionLocal() as db:
        task = task_service.get_task(db, task_id)
        artifact = artifact_service.create_text_artifact(
            db,
            task,
            artifact_type="report",
            logical_name="tamper.md",
            content="original",
        )
        path = artifact_service.resolve_artifact_path(artifact)
        path.write_text("tampered", encoding="utf-8")
        artifact_id = artifact.id

    with TestClient(app) as client:
        response = client.get(f"/artifacts/{artifact_id}/content")

        assert response.status_code == 400
        assert response.json()["error"]["code"] == "artifact_storage_error"
