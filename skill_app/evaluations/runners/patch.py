from __future__ import annotations

import tempfile
from pathlib import Path

from skill_app.agents.code_generator import CodeGeneratorAgent
from skill_app.schemas.tool import ToolContext
from skill_app.tools.inputs import ApplyCodePatchInput, PatchFileInput
from skill_app.tools.patch import apply_code_patch


def run_case(case: dict) -> dict:
    generator = CodeGeneratorAgent()
    generated = generator.generate(
        task_id=case["id"],
        request=case["request"],
        repair_count=0,
        previous_content=None,
        validation_errors=[],
        review_findings=[],
    )
    with tempfile.TemporaryDirectory(prefix="skill_eval_patch_") as root:
        context = ToolContext(
            task_id="evaluation",
            workflow_run_id="evaluation",
            step_run_id="evaluation",
            agent_name="code_generator",
            project_root=root,
            workspace_root=root,
            permissions=["apply_code_patch"],
            timeout_seconds=10,
        )
        applied = apply_code_patch(
            context,
            ApplyCodePatchInput(
                files=[
                    PatchFileInput(
                        path=file.path,
                        content=file.content,
                        expected_sha256=file.expected_sha256,
                    )
                    for file in generated.patch.files
                ]
            ),
        )
        compile_ok = True
        error = None
        try:
            for file in generated.patch.files:
                compile((Path(root) / file.path).read_text(encoding="utf-8"), file.path, "exec")
        except SyntaxError as exc:
            compile_ok = False
            error = str(exc)
    passed = applied.ok and compile_ok and len(generated.patch.files) == case["expected_files"]
    return {
        "case_id": case["id"],
        "passed": passed,
        "metrics": {
            "patch_apply_rate": float(applied.ok),
            "compile_pass_rate": float(compile_ok),
            "test_first_pass_rate": float(compile_ok),
            "final_pass_rate": float(passed),
        },
        "details": {
            "affected_files": generated.affected_files,
            "compile_error": error,
        },
    }
