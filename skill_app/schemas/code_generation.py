from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class PatchFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    content: str
    expected_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


class CodePatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[PatchFile] = Field(min_length=1, max_length=5)
    explanation: str


class CodeGenerationResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    patch: CodePatch
    affected_files: list[str]
    summary: str


class RepairEvidencePack(BaseModel):
    model_config = ConfigDict(extra="forbid")

    previous_path: str
    previous_content: str
    previous_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    validation_errors: list[str] = Field(default_factory=list)
    test_plan: dict[str, Any] = Field(default_factory=dict)
    test_content: str = ""
    test_report: dict[str, Any] = Field(default_factory=dict)
    review_findings: list[dict[str, Any]] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(default_factory=list)
    repair_history: list[str] = Field(default_factory=list)
