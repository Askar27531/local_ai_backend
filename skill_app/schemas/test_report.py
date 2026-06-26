from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GeneratedTestPlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_path: str
    test_path: str
    cases: list[str] = Field(min_length=1)
    uncovered_risks: list[str] = Field(default_factory=list)


class GeneratedTestSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: GeneratedTestPlan
    test_content: str = Field(min_length=1)


class TestExecutionReport(BaseModel):
    passed: bool
    compile_passed: bool
    pytest_passed: bool
    candidate_path: str
    test_path: str
    stdout: str = ""
    stderr: str = ""
    errors: list[str] = Field(default_factory=list)
    uncovered_risks: list[str] = Field(default_factory=list)
