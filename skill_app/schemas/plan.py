from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skill_app.domain.status import RiskLevel


class PlanStep(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,79}$")
    title: str = Field(min_length=1, max_length=240)
    agent: str = Field(min_length=1, max_length=120)
    skill: str | None = Field(default=None, max_length=120)
    depends_on: list[str] = Field(default_factory=list)
    required_tools: list[str] = Field(default_factory=list)
    expected_artifacts: list[str] = Field(default_factory=list)
    acceptance_criteria: list[str] = Field(min_length=1)
    risk_level: RiskLevel = RiskLevel.LOW


class TaskPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1)
    task_type: str = Field(min_length=1, max_length=80)
    summary: str = Field(min_length=1)
    affected_areas: list[str] = Field(default_factory=list)
    relevant_paths: list[str] = Field(default_factory=list)
    steps: list[PlanStep] = Field(min_length=1)
    global_acceptance_criteria: list[str] = Field(min_length=1)
    risk_level: RiskLevel = RiskLevel.LOW
    requires_plan_approval: bool = False

    @model_validator(mode="after")
    def require_approval_for_high_risk(self) -> TaskPlan:
        if self.risk_level == RiskLevel.HIGH or any(
            step.risk_level == RiskLevel.HIGH for step in self.steps
        ):
            self.requires_plan_approval = True
        return self


class PlanValidationResult(BaseModel):
    valid: bool
    errors: list[str] = Field(default_factory=list)
