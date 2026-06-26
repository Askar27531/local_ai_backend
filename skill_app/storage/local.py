from __future__ import annotations

import hashlib
from pathlib import Path

from skill_app.domain.errors import ArtifactStorageError, UnsafePathError
from skill_app.storage.base import StoredObject


def safe_logical_name(raw_name: str) -> str:
    stripped = raw_name.strip()
    if "/" in stripped or "\\" in stripped or Path(stripped).name != stripped:
        raise UnsafePathError("Artifact logical_name must be a file name.", {"logical_name": raw_name})
    name = stripped
    if not name or name in {".", ".."}:
        raise UnsafePathError("Artifact logical_name is invalid.", {"logical_name": raw_name})
    sanitized = "".join(char if char.isalnum() or char in "._-" else "_" for char in name).strip("._")
    while "__" in sanitized:
        sanitized = sanitized.replace("__", "_")
    if not sanitized:
        raise UnsafePathError("Artifact logical_name has no safe characters.", {"logical_name": raw_name})
    return sanitized[:240]


class LocalArtifactStorage:
    scheme = "local://"

    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def put_bytes(self, *, task_id: str, artifact_id: str, logical_name: str, content: bytes) -> StoredObject:
        safe_name = safe_logical_name(logical_name)
        relative = Path(task_id) / artifact_id / safe_name
        path = self._safe_path(relative)
        path.parent.mkdir(parents=True, exist_ok=False)
        try:
            path.write_bytes(content)
        except Exception as exc:
            self._cleanup_empty_parents(path)
            raise ArtifactStorageError(
                "Failed to write artifact content.",
                {"artifact_id": artifact_id, "logical_name": logical_name},
            ) from exc
        return StoredObject(
            storage_uri=self.scheme + relative.as_posix(),
            path=path,
            size_bytes=len(content),
            sha256=hashlib.sha256(content).hexdigest(),
        )

    def read_bytes(self, storage_uri: str) -> bytes:
        path = self.resolve_path(storage_uri)
        try:
            return path.read_bytes()
        except FileNotFoundError as exc:
            raise ArtifactStorageError(
                "Artifact content is missing.",
                {"storage_uri": storage_uri},
            ) from exc

    def resolve_path(self, storage_uri: str) -> Path:
        if not storage_uri.startswith(self.scheme):
            raise UnsafePathError("Unsupported artifact storage URI.", {"storage_uri": storage_uri})
        relative = Path(storage_uri.removeprefix(self.scheme))
        return self._safe_path(relative)

    def delete(self, storage_uri: str) -> None:
        path = self.resolve_path(storage_uri)
        if path.exists():
            path.unlink()
        self._cleanup_empty_parents(path)

    def _safe_path(self, relative: Path) -> Path:
        if relative.is_absolute() or ".." in relative.parts:
            raise UnsafePathError("Artifact path escapes storage root.", {"path": str(relative)})
        resolved = (self.root / relative).resolve()
        try:
            resolved.relative_to(self.root)
        except ValueError as exc:
            raise UnsafePathError("Artifact path escapes storage root.", {"path": str(relative)}) from exc
        return resolved

    def _cleanup_empty_parents(self, path: Path) -> None:
        current = path.parent
        while current != self.root:
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent
