from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class ReviewFinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    severity: Literal["critical", "high", "medium", "low"]
    category: str
    file: str
    line: int | None = Field(default=None, ge=1)
    title: str
    evidence: str
    suggestion: str
    blocking: bool


class CodeReviewReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    approved: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
    reviewed_files: list[str] = Field(default_factory=list)
    acceptance_criteria_checked: list[str] = Field(default_factory=list)
    test_report_artifact_ids: list[str] = Field(default_factory=list)


class SemanticReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
