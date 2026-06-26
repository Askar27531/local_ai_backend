from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from skill_app.domain.status import RunStatus


class SkillInfo(BaseModel):
    name: str
    description: str
    path: str


class LoadedSkill(BaseModel):
    name: str
    description: str
    path: str
    body: str
    references: dict[str, str] = Field(default_factory=dict)


class SkillModelBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instruction: str = "SKILL.md"
    profile: str | None = None
    output_schema: str


class SkillStageBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill: str
    profile: str | None = None
    output_schema: str


class SkillWorkflowBinding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    version: str = "1"
    max_repairs: int = Field(default=0, ge=0, le=10)


class SkillToolPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)


class SkillDataPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)


class SkillLimits(BaseModel):
    model_config = ConfigDict(extra="forbid")

    max_files: int = Field(default=1, ge=1, le=50)
    max_changed_lines: int = Field(default=400, ge=1, le=10000)
    max_model_calls: int = Field(default=8, ge=1, le=100)
    tool_timeout_seconds: int = Field(default=60, ge=1, le=600)


class SkillValidatorPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pre: list[str] = Field(default_factory=list)
    output: list[str] = Field(default_factory=list)
    post: list[str] = Field(default_factory=list)


class SkillApprovalPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_required: bool = False
    delivery_required: bool = True


class SkillRetryPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    model_parse_attempts: int = Field(default=1, ge=0, le=5)
    tool_attempts: int = Field(default=1, ge=0, le=5)
    repair_attempts: int = Field(default=0, ge=0, le=10)
    backoff_seconds: list[int] = Field(default_factory=lambda: [1, 3], max_length=5)


class SkillManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1]
    name: str = Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=64)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$", max_length=40)
    kind: Literal["workflow", "agent", "deterministic"]
    status: Literal["draft", "active", "deprecated"] = "draft"
    model: SkillModelBinding
    components: list[str] = Field(default_factory=list)
    stages: dict[str, SkillStageBinding] = Field(default_factory=dict)
    workflow: SkillWorkflowBinding | None = None
    agents: dict[str, str] = Field(default_factory=dict)
    tools: SkillToolPolicy = Field(default_factory=SkillToolPolicy)
    data_policy: SkillDataPolicy = Field(default_factory=SkillDataPolicy)
    limits: SkillLimits = Field(default_factory=SkillLimits)
    validators: SkillValidatorPolicy = Field(default_factory=SkillValidatorPolicy)
    approval: SkillApprovalPolicy = Field(default_factory=SkillApprovalPolicy)
    retry: SkillRetryPolicy = Field(default_factory=SkillRetryPolicy)

    @model_validator(mode="after")
    def validate_kind_bindings(self) -> SkillManifest:
        if self.kind == "workflow" and self.workflow is None:
            raise ValueError("Workflow skills require a workflow binding.")
        if self.retry.repair_attempts and self.workflow is None:
            raise ValueError("Repair attempts require a workflow binding.")
        if self.workflow is not None and (
            self.retry.repair_attempts != self.workflow.max_repairs
        ):
            raise ValueError("retry.repair_attempts must equal workflow.max_repairs.")
        available_skills = {self.name, *self.components}
        unknown_stage_skills = sorted(
            {stage.skill for stage in self.stages.values()} - available_skills
        )
        if unknown_stage_skills:
            raise ValueError(
                f"Stage bindings reference undeclared components: {unknown_stage_skills}"
            )
        return self


class LoadedSkillPackage(BaseModel):
    manifest: SkillManifest
    instructions: LoadedSkill
    package_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    instruction_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    component_instructions: dict[str, LoadedSkill] = Field(default_factory=dict)
    component_instruction_sha256s: dict[str, str] = Field(default_factory=dict)


class SkillPackageInfo(BaseModel):
    name: str
    description: str
    version: str
    kind: str
    status: str
    workflow_name: str | None = None
    output_schema: str
    components: list[str] = Field(default_factory=list)
    stages: list[str] = Field(default_factory=list)
    tools: list[str]
    validators: list[str]
    limits: SkillLimits
    package_sha256: str


class SkillStartRequest(BaseModel):
    version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")


class SkillRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    task_id: str
    workflow_run_id: str
    skill_name: str
    skill_version: str
    package_sha256: str
    instruction_sha256: str
    manifest_snapshot: dict
    instruction_snapshots: dict[str, str]
    component_instruction_sha256s: dict[str, str]
    status: RunStatus
    started_at: datetime
    finished_at: datetime | None
