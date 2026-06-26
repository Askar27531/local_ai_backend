from __future__ import annotations

import hashlib
from pathlib import Path

from skill_app.domain.errors import ToolExecutionError
from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.tools.inputs import ApplyCodePatchInput
from skill_app.tools.paths import safe_path


def apply_code_patch(context: ToolContext, args: ApplyCodePatchInput) -> ToolResult:
    root = Path(context.workspace_root).resolve()
    changed_lines = sum(len(file.content.splitlines()) for file in args.files)
    if changed_lines > args.max_changed_lines:
        raise ToolExecutionError(
            "Patch exceeds changed-line limit.",
            {"changed_lines": changed_lines, "limit": args.max_changed_lines},
        )
    targets: list[tuple[Path, str, str]] = []
    for file in args.files:
        target = safe_path(root, file.path)
        if file.expected_sha256 is not None:
            if not target.is_file():
                raise ToolExecutionError("Hash-locked patch target does not exist.", {"path": file.path})
            actual = hashlib.sha256(target.read_bytes()).hexdigest()
            if actual != file.expected_sha256:
                raise ToolExecutionError(
                    "Patch target hash changed.",
                    {"path": file.path, "expected": file.expected_sha256, "actual": actual},
                )
        targets.append((target, file.path, file.content))
    written: list[str] = []
    for target, relative, content in targets:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative)
    return ToolResult(
        ok=True,
        output={"affected_files": written, "changed_lines": changed_lines},
        artifacts=written,
    )
