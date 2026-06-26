from __future__ import annotations

from types import SimpleNamespace

from skill_app.agents.planner import PlannerAgent, render_plan_markdown, validate_plan
from skill_app.domain.status import RiskLevel
from skill_app.schemas.plan import PlanStep, TaskPlan


def task(request: str, *, risk: str = "low"):
    return SimpleNamespace(request=request, task_type="general", risk_level=risk)


def test_documentation_request_does_not_plan_code_write():
    plan = PlannerAgent().build_baseline_plan(
        task("更新 README 文档和使用说明"),
        ["skill_app/README.md"],
    )
    tools = {tool for step in plan.steps for tool in step.required_tools}
    assert "write_workspace_file" not in tools
    assert "apply_patch_to_workspace" not in tools
    assert all(step.agent != "code_generator" for step in plan.steps)
    assert validate_plan(plan).valid


def test_code_request_has_generation_test_review_dependency_chain():
    plan = PlannerAgent().build_baseline_plan(
        task("实现 FastAPI 接口并补充自动化测试"),
        ["skill_app/main.py"],
    )
    assert [step.key for step in plan.steps] == [
        "generate_candidate",
        "verify_candidate",
        "review_candidate",
    ]
    assert plan.steps[1].depends_on == ["generate_candidate"]
    assert plan.steps[2].depends_on == ["verify_candidate"]
    assert validate_plan(plan).valid


def test_plan_validator_rejects_cycle_unknown_agent_and_denied_tool():
    plan = TaskPlan(
        goal="Implement code",
        task_type="code",
        summary="Invalid plan",
        steps=[
            PlanStep(
                key="first_step",
                title="First",
                agent="planner",
                depends_on=["second_step"],
                required_tools=["write_workspace_file"],
                expected_artifacts=["unknown_artifact"],
                acceptance_criteria=["done"],
            ),
            PlanStep(
                key="second_step",
                title="Second",
                agent="missing_agent",
                depends_on=["first_step"],
                acceptance_criteria=["done"],
            ),
        ],
        global_acceptance_criteria=["done"],
    )
    result = validate_plan(plan)
    assert not result.valid
    assert any("cycle" in error for error in result.errors)
    assert any("not registered" in error for error in result.errors)
    assert any("lacks tool permissions" in error for error in result.errors)
    assert any("artifact types are invalid" in error for error in result.errors)
    assert any("validation or test step" in error for error in result.errors)


def test_high_risk_plan_forces_approval_and_markdown_is_readable():
    plan = PlannerAgent().build_baseline_plan(
        task("实现高风险程序修改", risk="high"),
        ["skill_app/main.py"],
    )
    assert plan.risk_level == RiskLevel.HIGH
    assert plan.requires_plan_approval
    markdown = render_plan_markdown(plan)
    assert "# 实现高风险程序修改" in markdown
    assert "generate_candidate" in markdown
    assert "Global acceptance criteria" in markdown
