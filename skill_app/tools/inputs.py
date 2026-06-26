from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from skill_app.schemas.art import VisualBrief


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EmptyInput(ToolInput):
    pass


class ListProjectFilesInput(ToolInput):
    pattern: str = "**/*"
    limit: int = Field(default=500, ge=1, le=5000)


class SearchProjectTextInput(ToolInput):
    query: str = Field(min_length=1, max_length=500)
    pattern: str = "**/*"
    limit: int = Field(default=100, ge=1, le=1000)


class ReadProjectFileInput(ToolInput):
    path: str = Field(min_length=1)
    max_chars: int | None = Field(default=None, ge=1)


class ReadFileRangeInput(ToolInput):
    path: str = Field(min_length=1)
    start_line: int = Field(default=1, ge=1)
    end_line: int = Field(ge=1)


class WriteWorkspaceFileInput(ToolInput):
    path: str = Field(min_length=1)
    content: str
    artifact_type: str = Field(default="workspace_file", min_length=1, max_length=80)


class CreateUnifiedDiffInput(ToolInput):
    path: str = Field(min_length=1)


class ApplyPatchInput(ToolInput):
    path: str = Field(min_length=1)
    old_text: str = Field(min_length=1)
    new_text: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{64}$")


class RunPytestInput(ToolInput):
    paths: list[str] = Field(default_factory=list, max_length=50)
    keywords: str | None = Field(default=None, max_length=200)
    max_failures: int = Field(default=1, ge=1, le=20)


class RunPythonCompileInput(ToolInput):
    paths: list[str] = Field(default_factory=list, max_length=200)


class ValidateJsonInput(ToolInput):
    content: str


class ValidateYamlInput(ToolInput):
    content: str


class ValidateJsonSchemaInput(ToolInput):
    instance: Any
    schema_definition: dict[str, Any]


class ValidateNPCBehaviorConfigInput(ToolInput):
    content: str


class PatchFileInput(ToolInput):
    path: str = Field(min_length=1)
    content: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class ApplyCodePatchInput(ToolInput):
    files: list[PatchFileInput] = Field(min_length=1, max_length=5)
    max_changed_lines: int = Field(default=400, ge=1, le=2000)


class GenerateImageCandidatesInput(ToolInput):
    brief: VisualBrief
    output_directory: str = Field(default="generated/art", min_length=1, max_length=240)


class ValidateImageInput(ToolInput):
    path: str = Field(min_length=1, max_length=500)
    brief: VisualBrief
