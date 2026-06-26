from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

TEST_ROOT = Path(tempfile.mkdtemp(prefix="skill_app_tests_"))
TEST_DATABASE_PATH = TEST_ROOT / "test.sqlite3"
os.environ["SKILL_APP_DATABASE_URL"] = f"sqlite:///{TEST_DATABASE_PATH.as_posix()}"
os.environ["SKILL_APP_ARTIFACT_ROOT"] = str(TEST_ROOT / "artifacts")
os.environ["SKILL_APP_WORKSPACE_ROOT"] = str(TEST_ROOT / "workspaces")
os.environ["SKILL_APP_CHECKPOINT_PATH"] = str(TEST_ROOT / "workflow_checkpoints.sqlite3")
os.environ["SKILL_APP_AUTO_CREATE_SCHEMA"] = "true"
os.environ["SKILL_APP_MODEL_PROFILE"] = "local-default"

from skill_app.db.database import Base, engine  # noqa: E402


@pytest.fixture(autouse=True)
def reset_database():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


def pytest_sessionfinish() -> None:
    engine.dispose()
    shutil.rmtree(TEST_ROOT, ignore_errors=True)
