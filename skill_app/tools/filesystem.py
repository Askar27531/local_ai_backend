from __future__ import annotations

import difflib
import hashlib
from pathlib import Path

from skill_app.config import get_settings
from skill_app.domain.errors import ToolExecutionError
from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.tools.inputs import (
    ApplyPatchInput,
    CreateUnifiedDiffInput,
    ListProjectFilesInput,
    ReadFileRangeInput,
    ReadProjectFileInput,
    SearchProjectTextInput,
    WriteWorkspaceFileInput,
)
from skill_app.tools.paths import is_ignored, safe_path


def _project_root(context: ToolContext) -> Path:
    return Path(context.project_root).resolve()


def _workspace_root(context: ToolContext) -> Path:
    root = Path(context.workspace_root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


def list_project_files(context: ToolContext, args: ListProjectFilesInput) -> ToolResult:
    root = _project_root(context)
    files: list[str] = []
    for path in root.glob(args.pattern):
        if not path.is_file() or is_ignored(path, root):
            continue
        files.append(path.relative_to(root).as_posix())
        if len(files) >= args.limit:
            break
    return ToolResult(ok=True, output={"files": sorted(files), "count": len(files)})


def search_project_text(context: ToolContext, args: SearchProjectTextInput) -> ToolResult:
    root = _project_root(context)
    matches: list[dict[str, object]] = []
    for path in root.glob(args.pattern):
        if not path.is_file() or is_ignored(path, root):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            if args.query.casefold() not in line.casefold():
                continue
            matches.append(
                {
                    "path": path.relative_to(root).as_posix(),
                    "line": line_number,
                    "text": line[:500],
                }
            )
            if len(matches) >= args.limit:
                return ToolResult(ok=True, output={"matches": matches, "count": len(matches)})
    return ToolResult(ok=True, output={"matches": matches, "count": len(matches)})


def read_project_file(context: ToolContext, args: ReadProjectFileInput) -> ToolResult:
    root = _project_root(context)
    path = safe_path(root, args.path, must_exist=True)
    if not path.is_file() or is_ignored(path, root):
        raise ToolExecutionError("Project path is not a readable text file.", {"path": args.path})
    text = path.read_text(encoding="utf-8")
    max_chars = args.max_chars or get_settings().max_tool_file_chars
    truncated = len(text) > max_chars
    return ToolResult(
        ok=True,
        output={
            "path": args.path,
            "content": text[:max_chars],
            "truncated": truncated,
            "total_chars": len(text),
        },
    )


def read_file_range(context: ToolContext, args: ReadFileRangeInput) -> ToolResult:
    if args.end_line < args.start_line:
        raise ToolExecutionError(
            "end_line must be greater than or equal to start_line.",
            {"start_line": args.start_line, "end_line": args.end_line},
        )
    root = _project_root(context)
    path = safe_path(root, args.path, must_exist=True)
    lines = path.read_text(encoding="utf-8").splitlines()
    selected = lines[args.start_line - 1 : args.end_line]
    return ToolResult(
        ok=True,
        output={
            "path": args.path,
            "start_line": args.start_line,
            "end_line": min(args.end_line, len(lines)),
            "content": "\n".join(selected),
        },
    )


def write_workspace_file(context: ToolContext, args: WriteWorkspaceFileInput) -> ToolResult:
    root = _workspace_root(context)
    path = safe_path(root, args.path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(args.content, encoding="utf-8")
    relative = path.relative_to(root).as_posix()
    return ToolResult(
        ok=True,
        output={"path": relative, "size_bytes": len(args.content.encode("utf-8"))},
        artifacts=[relative],
    )


def create_unified_diff(context: ToolContext, args: CreateUnifiedDiffInput) -> ToolResult:
    project_root = _project_root(context)
    workspace_root = _workspace_root(context)
    source = safe_path(project_root, args.path)
    candidate = safe_path(workspace_root, args.path, must_exist=True)
    source_text = source.read_text(encoding="utf-8") if source.exists() else ""
    candidate_text = candidate.read_text(encoding="utf-8")
    diff = "".join(
        difflib.unified_diff(
            source_text.splitlines(keepends=True),
            candidate_text.splitlines(keepends=True),
            fromfile=f"a/{args.path}",
            tofile=f"b/{args.path}",
        )
    )
    return ToolResult(ok=True, output={"path": args.path, "diff": diff, "changed": bool(diff)})


def apply_patch_to_workspace(context: ToolContext, args: ApplyPatchInput) -> ToolResult:
    root = _workspace_root(context)
    path = safe_path(root, args.path, must_exist=True)
    current = path.read_text(encoding="utf-8")
    if args.expected_sha256 is not None:
        actual = hashlib.sha256(current.encode("utf-8")).hexdigest()
        if actual != args.expected_sha256:
            raise ToolExecutionError(
                "Workspace file hash changed before patch application.",
                {"path": args.path, "expected": args.expected_sha256, "actual": actual},
            )
    occurrences = current.count(args.old_text)
    if occurrences != 1:
        raise ToolExecutionError(
            "old_text must match exactly once.",
            {"path": args.path, "matches": occurrences},
        )
    updated = current.replace(args.old_text, args.new_text, 1)
    path.write_text(updated, encoding="utf-8")
    return ToolResult(
        ok=True,
        output={
            "path": args.path,
            "sha256": hashlib.sha256(updated.encode("utf-8")).hexdigest(),
        },
        artifacts=[path.relative_to(root).as_posix()],
    )
