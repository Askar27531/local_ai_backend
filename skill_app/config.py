from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = PROJECT_ROOT / ".skill_app_data"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="SKILL_APP_",
        env_file=".env",
        extra="ignore",
    )

    app_name: str = "Game Production Agent Platform"
    app_version: str = "0.22.0"
    environment: str = "development"
    database_url: str = f"sqlite:///{(DATA_ROOT / 'skill_app.sqlite3').as_posix()}"
    artifact_root: Path = DATA_ROOT / "artifacts"
    workspace_root: Path = DATA_ROOT / "workspaces"
    checkpoint_path: Path = DATA_ROOT / "workflow_checkpoints.sqlite3"
    project_root: Path = PROJECT_ROOT
    model_profile: str = "ollama-qwen3-14b"
    model_profiles_path: Path = PROJECT_ROOT / "skill_app" / "model_profiles" / "default.yaml"
    max_workflow_retries: int = Field(default=2, ge=0, le=10)
    tool_timeout_seconds: int = Field(default=180, ge=1)
    tool_output_chars: int = Field(default=12000, ge=1000)
    max_tool_file_chars: int = Field(default=50000, ge=1000)
    max_artifact_bytes: int = Field(default=20 * 1024 * 1024, ge=1)
    auto_create_schema: bool = True

    def ensure_local_directories(self) -> None:
        self.artifact_root.mkdir(parents=True, exist_ok=True)
        self.workspace_root.mkdir(parents=True, exist_ok=True)
        self.checkpoint_path.parent.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
