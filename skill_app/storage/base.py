from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class StoredObject:
    storage_uri: str
    path: Path
    size_bytes: int
    sha256: str


class ArtifactStorage(Protocol):
    def put_bytes(self, *, task_id: str, artifact_id: str, logical_name: str, content: bytes) -> StoredObject: ...

    def read_bytes(self, storage_uri: str) -> bytes: ...

    def resolve_path(self, storage_uri: str) -> Path: ...

    def delete(self, storage_uri: str) -> None: ...
