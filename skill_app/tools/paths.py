from __future__ import annotations

from pathlib import Path

from skill_app.domain.errors import UnsafePathError

IGNORED_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".skill_app_data",
    "node_modules",
}
IGNORED_SUFFIXES = {
    ".db",
    ".sqlite",
    ".sqlite3",
    ".pyc",
    ".pyo",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".zip",
    ".7z",
    ".exe",
    ".dll",
}


def safe_path(root: Path, raw_path: str, *, must_exist: bool = False) -> Path:
    root = root.resolve()
    candidate = Path(raw_path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise UnsafePathError("Path must be relative and remain inside its root.", {"path": raw_path})
    resolved = (root / candidate).resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise UnsafePathError("Path escapes its allowed root.", {"path": raw_path}) from exc
    if must_exist and not resolved.exists():
        raise UnsafePathError("Path does not exist.", {"path": raw_path})
    if resolved.exists() and resolved.is_symlink():
        target = resolved.resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise UnsafePathError("Symbolic link escapes its allowed root.", {"path": raw_path}) from exc
    return resolved


def is_ignored(path: Path, root: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return any(part in IGNORED_PARTS for part in relative.parts) or path.suffix.lower() in IGNORED_SUFFIXES
