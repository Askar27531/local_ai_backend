from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

from skill_app.schemas.tool import ToolContext, ToolResult
from skill_app.tools.inputs import RunPytestInput, RunPythonCompileInput
from skill_app.tools.paths import safe_path


def run_python_compile(context: ToolContext, args: RunPythonCompileInput) -> ToolResult:
    workspace_root = Path(context.workspace_root).resolve()
    targets = [safe_path(workspace_root, path, must_exist=True) for path in args.paths]
    if not targets:
        targets = [workspace_root]
    command = [sys.executable, "-m", "compileall", "-q", *[str(path) for path in targets]]
    return _run_command(command, workspace_root, context.timeout_seconds)


def run_pytest(context: ToolContext, args: RunPytestInput) -> ToolResult:
    workspace_root = Path(context.workspace_root).resolve()
    targets = [safe_path(workspace_root, path, must_exist=True) for path in args.paths]
    command = [
        sys.executable,
        "-m",
        "pytest",
        *[str(path) for path in targets],
        f"--maxfail={args.max_failures}",
        "-q",
    ]
    if args.keywords:
        command.extend(["-k", args.keywords])
    return _run_command(command, workspace_root, context.timeout_seconds)


def _run_command(command: list[str], cwd: Path, timeout_seconds: int) -> ToolResult:
    started = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            cwd=cwd,
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            check=False,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as exc:
        return ToolResult(
            ok=False,
            stdout=str(exc.stdout or ""),
            stderr=str(exc.stderr or ""),
            error_type="tool_timeout",
            metrics={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
        )
    return ToolResult(
        ok=completed.returncode == 0,
        output={"command": command[1:]},
        stdout=completed.stdout,
        stderr=completed.stderr,
        exit_code=completed.returncode,
        metrics={"duration_ms": round((time.perf_counter() - started) * 1000, 2)},
        error_type=None if completed.returncode == 0 else "process_failed",
    )
