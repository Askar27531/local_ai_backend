from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RepeatedQuestionRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    window_seconds: int = Field(ge=1, le=3600)
    base_increment: int = Field(ge=1, le=100)


class NPCBehaviorRules(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repeated_question: RepeatedQuestionRule


class NPCBehaviorThresholds(BaseModel):
    model_config = ConfigDict(extra="forbid")

    warning: int = Field(ge=0, le=99)
    refuse: int = Field(ge=1, le=100)

    @model_validator(mode="after")
    def warning_must_precede_refuse(self) -> NPCBehaviorThresholds:
        if self.warning >= self.refuse:
            raise ValueError("warning threshold must be lower than refuse threshold")
        return self


class NPCOverride(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: Literal["default", "lenient", "strict"] = "default"
    enabled: bool = True
    threshold_multiplier: float = Field(default=1, ge=0.1, le=3)


class NPCBehaviorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    feature: Literal["npc_behavior"]
    rules: NPCBehaviorRules
    thresholds: NPCBehaviorThresholds
    npc_overrides: dict[str, NPCOverride] = Field(default_factory=dict)
    npc_groups: dict[str, list[str]] = Field(default_factory=dict)


class GameConfigValidationReport(BaseModel):
    valid: bool
    schema_version: int | None = None
    checks: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    normalized_config: dict | None = None
