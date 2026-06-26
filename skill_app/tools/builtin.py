from __future__ import annotations

from skill_app.schemas.tool import ToolDefinition
from skill_app.tools.config_validation import validate_npc_behavior_config
from skill_app.tools.filesystem import (
    apply_patch_to_workspace,
    create_unified_diff,
    list_project_files,
    read_file_range,
    read_project_file,
    search_project_text,
    write_workspace_file,
)
from skill_app.tools.image_generation import generate_image_candidates
from skill_app.tools.image_validation import validate_image_file
from skill_app.tools.inputs import (
    ApplyCodePatchInput,
    ApplyPatchInput,
    CreateUnifiedDiffInput,
    GenerateImageCandidatesInput,
    ListProjectFilesInput,
    ReadFileRangeInput,
    ReadProjectFileInput,
    RunPytestInput,
    RunPythonCompileInput,
    SearchProjectTextInput,
    ValidateImageInput,
    ValidateJsonInput,
    ValidateJsonSchemaInput,
    ValidateNPCBehaviorConfigInput,
    ValidateYamlInput,
    WriteWorkspaceFileInput,
)
from skill_app.tools.patch import apply_code_patch
from skill_app.tools.registry import tool_registry
from skill_app.tools.testing import run_pytest, run_python_compile
from skill_app.tools.validation import validate_json, validate_json_schema, validate_yaml


def register_builtin_tools() -> None:
    if tool_registry.list_definitions():
        return
    registrations = [
        (
            ToolDefinition(name="list_project_files", description="List safe project files."),
            ListProjectFilesInput,
            list_project_files,
        ),
        (
            ToolDefinition(name="search_project_text", description="Search UTF-8 project text."),
            SearchProjectTextInput,
            search_project_text,
        ),
        (
            ToolDefinition(name="read_project_file", description="Read a safe project text file."),
            ReadProjectFileInput,
            read_project_file,
        ),
        (
            ToolDefinition(name="read_file_range", description="Read a line range from a project file."),
            ReadFileRangeInput,
            read_file_range,
        ),
        (
            ToolDefinition(
                name="write_workspace_file",
                description="Write a UTF-8 file inside the task workspace.",
                permission="write_workspace",
                side_effect_level="workspace",
                idempotent=False,
            ),
            WriteWorkspaceFileInput,
            write_workspace_file,
        ),
        (
            ToolDefinition(name="create_unified_diff", description="Compare a workspace file with the project source."),
            CreateUnifiedDiffInput,
            create_unified_diff,
        ),
        (
            ToolDefinition(
                name="apply_patch_to_workspace",
                description="Apply an exact text replacement inside a workspace file.",
                permission="write_workspace",
                side_effect_level="workspace",
                idempotent=False,
            ),
            ApplyPatchInput,
            apply_patch_to_workspace,
        ),
        (
            ToolDefinition(
                name="apply_code_patch",
                description="Apply a bounded, hash-aware multi-file patch inside TaskWorkspace.",
                permission="write_workspace",
                side_effect_level="workspace",
                idempotent=False,
            ),
            ApplyCodePatchInput,
            apply_code_patch,
        ),
        (
            ToolDefinition(
                name="run_pytest",
                description="Run pytest with structured arguments inside the workspace.",
                permission="execute_test",
                side_effect_level="process",
                timeout_seconds=180,
            ),
            RunPytestInput,
            run_pytest,
        ),
        (
            ToolDefinition(
                name="run_python_compile",
                description="Compile Python files inside the workspace.",
                permission="execute_test",
                side_effect_level="process",
                timeout_seconds=120,
            ),
            RunPythonCompileInput,
            run_python_compile,
        ),
        (
            ToolDefinition(name="validate_json", description="Validate and parse JSON."),
            ValidateJsonInput,
            validate_json,
        ),
        (
            ToolDefinition(name="validate_yaml", description="Validate and parse YAML."),
            ValidateYamlInput,
            validate_yaml,
        ),
        (
            ToolDefinition(
                name="validate_json_schema",
                description="Validate basic JSON Schema constraints deterministically.",
            ),
            ValidateJsonSchemaInput,
            validate_json_schema,
        ),
        (
            ToolDefinition(
                name="validate_npc_behavior_config",
                description="Validate NPC behavior YAML against schema and business rules.",
            ),
            ValidateNPCBehaviorConfigInput,
            validate_npc_behavior_config,
        ),
        (
            ToolDefinition(
                name="generate_image_candidates",
                description="Generate traceable image candidates inside TaskWorkspace.",
                permission="generate_image",
                side_effect_level="external",
                idempotent=False,
                approval_required=True,
                timeout_seconds=180,
            ),
            GenerateImageCandidatesInput,
            generate_image_candidates,
        ),
        (
            ToolDefinition(
                name="validate_image",
                description="Validate image format, dimensions, and alpha requirements.",
            ),
            ValidateImageInput,
            validate_image_file,
        ),
    ]
    for definition, input_model, handler in registrations:
        tool_registry.register(definition, input_model, handler)
