from __future__ import annotations

import hashlib

import pytest

from skill_app.domain.errors import UnsafePathError
from skill_app.storage.local import LocalArtifactStorage, safe_logical_name


def test_local_storage_writes_reads_and_deletes_content(tmp_path):
    storage = LocalArtifactStorage(tmp_path)
    content = b"artifact-content"

    stored = storage.put_bytes(
        task_id="task-1",
        artifact_id="artifact-1",
        logical_name="report.txt",
        content=content,
    )

    assert stored.storage_uri == "local://task-1/artifact-1/report.txt"
    assert stored.size_bytes == len(content)
    assert stored.sha256 == hashlib.sha256(content).hexdigest()
    assert storage.read_bytes(stored.storage_uri) == content

    storage.delete(stored.storage_uri)
    assert not stored.path.exists()


@pytest.mark.parametrize(
    "logical_name",
    ["../secret.txt", "folder/file.txt", r"folder\file.txt", ".", "..", ""],
)
def test_safe_logical_name_rejects_path_or_empty_name(logical_name):
    with pytest.raises(UnsafePathError):
        safe_logical_name(logical_name)


def test_safe_logical_name_normalizes_unsafe_characters():
    assert safe_logical_name("NPC 配置 #1.yaml") == "NPC_配置_1.yaml"


def test_storage_rejects_uri_outside_local_scheme(tmp_path):
    storage = LocalArtifactStorage(tmp_path)

    with pytest.raises(UnsafePathError):
        storage.resolve_path("file:///tmp/report.txt")
