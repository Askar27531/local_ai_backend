from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from skill_app.schemas.review import ReviewFinding


class CandidateSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["game_config", "python_patch"]
    relative_path: str
    content: str
    summary: str


class CandidateValidation(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
    checks: list[str] = Field(default_factory=list)
    details: dict[str, Any] = Field(default_factory=dict)


class CandidateReview(BaseModel):
    approved: bool
    summary: str
    findings: list[ReviewFinding] = Field(default_factory=list)
