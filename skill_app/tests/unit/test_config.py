from __future__ import annotations

from skill_app.config import Settings


def test_settings_use_skill_app_environment_prefix(monkeypatch, tmp_path):
    database_path = tmp_path / "settings.sqlite3"
    monkeypatch.setenv("SKILL_APP_DATABASE_URL", f"sqlite:///{database_path.as_posix()}")
    monkeypatch.setenv("SKILL_APP_MAX_WORKFLOW_RETRIES", "4")

    settings = Settings(_env_file=None)

    assert settings.database_url.endswith("settings.sqlite3")
    assert settings.max_workflow_retries == 4


def test_settings_create_local_directories(tmp_path):
    settings = Settings(
        _env_file=None,
        artifact_root=tmp_path / "artifacts",
        workspace_root=tmp_path / "workspaces",
    )

    settings.ensure_local_directories()

    assert settings.artifact_root.is_dir()
    assert settings.workspace_root.is_dir()
