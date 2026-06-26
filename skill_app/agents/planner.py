from __future__ import annotations

import json
from pathlib import Path

from skill_app.agents.base import AgentContext
from skill_app.config import get_settings
from skill_app.domain.errors import PlanValidationError
from skill_app.domain.status import RiskLevel
from skill_app.schemas.model import ModelMessage
from skill_app.schemas.plan import PlanStep, PlanValidationResult, TaskPlan
from skill_app.services import model_gateway, policy_service
from skill_app.services.skill_loader import discover_skills, load_skill
from skill_app.tools.builtin import register_builtin_tools
from skill_app.tools.paths import is_ignored
from skill_app.tools.registry import tool_registry

ALLOWED_ARTIFACT_TYPES = frozenset(
    {
        "design_config",
        "documentation",
        "review_report",
        "test_report",
        "validation_report",
        "workspace_diff",
        "workspace_file",
    }
)
CODE_HINTS = (
    "代码",
    "code",
    "程序",
    "接口",
    "bug",
    "python",
    "fastapi",
    "实现",
    "重构",
    "测试",
)
DOCUMENT_HINTS = ("文档", "说明", "readme", "报告", "注释")


class PlannerAgent:
    name = "planner"
    skill_name = "game-feature-planning"
    prompt_version = "planner-v1"

    def execute(self, context: AgentContext) -> TaskPlan:
        context_pack = self.build_context_pack(context.task.request)
        baseline = self.build_baseline_plan(context.task, context_pack["relevant_paths"])
        skill = load_skill(self.skill_name)
        prompt = _load_prompt()
        messages = [
            ModelMessage(
                role="system",
                content=(
                    f"{prompt}\n\nPlanning skill:\n{skill.body}\n\n"
                    f"Project context:\n{json.dumps(context_pack, ensure_ascii=False)}"
                ),
            ),
            ModelMessage(
                role="user",
                content=baseline.model_dump_json(),
            ),
        ]
        plan = model_gateway.invoke_structured(
            context.db,
            task_id=context.task.id,
            step_run_id=context.step_run.id,
            profile_name=get_settings().model_profile,
            messages=messages,
            response_model=TaskPlan,
            prompt_version=self.prompt_version,
        )
        result = validate_plan(plan)
        if not result.valid:
            raise PlanValidationError(
                "Planner produced an invalid execution plan.",
                {"errors": result.errors},
            )
        return plan

    def build_context_pack(self, request: str) -> dict[str, object]:
        root = get_settings().project_root.resolve()
        files: list[str] = []
        terms = [term.casefold() for term in request.replace("/", " ").split() if len(term) >= 3]
        scored: list[tuple[int, str]] = []
        for path in root.rglob("*"):
            if not path.is_file() or is_ignored(path, root):
                continue
            relative = path.relative_to(root).as_posix()
            files.append(relative)
            score = sum(term in relative.casefold() for term in terms)
            if score:
                scored.append((score, relative))
            if len(files) >= 500:
                break
        relevant = [path for _, path in sorted(scored, reverse=True)[:30]]
        if not relevant:
            relevant = sorted(path for path in files if path.startswith("skill_app/"))[:30]
        return {
            "project_root_name": root.name,
            "file_count_sampled": len(files),
            "relevant_paths": relevant,
            "available_agents": sorted(policy_service.load_policy().agents),
            "available_skills": sorted(skill.name for skill in discover_skills()),
        }

    def build_baseline_plan(self, task, relevant_paths: list[str]) -> TaskPlan:
        request = task.request.casefold()
        is_code = any(hint in request for hint in CODE_HINTS)
        is_document_only = any(hint in request for hint in DOCUMENT_HINTS) and not is_code
        if is_document_only:
            steps = [
                PlanStep(
                    key="analyze_documentation",
                    title="分析文档范围和现有内容",
                    agent="review_agent",
                    required_tools=["list_project_files", "search_project_text", "read_project_file"],
                    expected_artifacts=["documentation"],
                    acceptance_criteria=["文档修改范围明确且不包含程序代码写入。"],
                )
            ]
        else:
            steps = [
                PlanStep(
                    key="generate_candidate",
                    title="生成候选实现",
                    agent="code_generator",
                    required_tools=[
                        "list_project_files",
                        "search_project_text",
                        "read_project_file",
                        "write_workspace_file",
                    ],
                    expected_artifacts=["workspace_file", "workspace_diff"],
                    acceptance_criteria=["候选修改只写入 TaskWorkspace。"],
                    risk_level=RiskLevel.MEDIUM,
                ),
                PlanStep(
                    key="verify_candidate",
                    title="执行自动化验证",
                    agent="test_agent",
                    depends_on=["generate_candidate"],
                    required_tools=["run_python_compile", "run_pytest"],
                    expected_artifacts=["test_report"],
                    acceptance_criteria=["定向测试和基础编译检查通过。"],
                ),
                PlanStep(
                    key="review_candidate",
                    title="审查变更与风险",
                    agent="review_agent",
                    depends_on=["verify_candidate"],
                    required_tools=["read_project_file", "create_unified_diff"],
                    expected_artifacts=["review_report"],
                    acceptance_criteria=["审查结论包含证据和阻断问题。"],
                ),
            ]
        risk = RiskLevel(task.risk_level)
        return TaskPlan(
            goal=task.request,
            task_type=task.task_type,
            summary="将用户需求拆分为隔离生成、验证和审查步骤。",
            affected_areas=["documentation"] if is_document_only else ["code", "tests", "review"],
            relevant_paths=relevant_paths,
            steps=steps,
            global_acceptance_criteria=[
                "源项目保持不变。",
                "所有产物可追溯到任务和步骤。",
            ],
            risk_level=risk,
            requires_plan_approval=risk != RiskLevel.LOW,
        )


def validate_plan(plan: TaskPlan) -> PlanValidationResult:
    register_builtin_tools()
    errors: list[str] = []
    keys = [step.key for step in plan.steps]
    key_set = set(keys)
    if len(keys) != len(key_set):
        errors.append("Step key must be unique.")
    available_agents = set(policy_service.load_policy().agents)
    available_tools = {definition.name for definition in tool_registry.list_definitions()}
    available_skills = {skill.name for skill in discover_skills()}
    for step in plan.steps:
        missing_dependencies = sorted(set(step.depends_on) - key_set)
        if missing_dependencies:
            errors.append(f"{step.key}: missing dependencies {missing_dependencies}.")
        if step.agent not in available_agents:
            errors.append(f"{step.key}: agent is not registered: {step.agent}.")
        missing_tools = sorted(set(step.required_tools) - available_tools)
        if missing_tools:
            errors.append(f"{step.key}: tools are not registered: {missing_tools}.")
        allowed = set(policy_service.permissions_for_agent(step.agent))
        denied_tools = sorted(set(step.required_tools) - allowed)
        if denied_tools:
            errors.append(f"{step.key}: agent lacks tool permissions: {denied_tools}.")
        if step.skill is not None and step.skill not in available_skills:
            errors.append(f"{step.key}: skill is not registered: {step.skill}.")
        invalid_artifacts = sorted(set(step.expected_artifacts) - ALLOWED_ARTIFACT_TYPES)
        if invalid_artifacts:
            errors.append(f"{step.key}: artifact types are invalid: {invalid_artifacts}.")
    if _has_cycle(plan.steps):
        errors.append("Plan dependency graph contains a cycle.")
    if plan.risk_level == RiskLevel.HIGH and not plan.requires_plan_approval:
        errors.append("High-risk plan must require approval.")
    if any(step.risk_level == RiskLevel.HIGH for step in plan.steps) and not plan.requires_plan_approval:
        errors.append("High-risk step must require plan approval.")
    if _looks_like_code_plan(plan) and not any(
        step.agent == "test_agent" or any(tool in {"run_pytest", "run_python_compile"} for tool in step.required_tools)
        for step in plan.steps
    ):
        errors.append("Code plan must include a validation or test step.")
    return PlanValidationResult(valid=not errors, errors=errors)


def render_plan_markdown(plan: TaskPlan) -> str:
    lines = [
        f"# {plan.goal}",
        "",
        plan.summary,
        "",
        f"- Task type: `{plan.task_type}`",
        f"- Risk: `{plan.risk_level.value}`",
        f"- Requires approval: `{str(plan.requires_plan_approval).lower()}`",
        "",
        "## Steps",
        "",
    ]
    for index, step in enumerate(plan.steps, start=1):
        dependencies = ", ".join(step.depends_on) or "none"
        lines.extend(
            [
                f"### {index}. {step.title}",
                "",
                f"- Key: `{step.key}`",
                f"- Agent: `{step.agent}`",
                f"- Depends on: {dependencies}",
                f"- Risk: `{step.risk_level.value}`",
                f"- Tools: {', '.join(step.required_tools) or 'none'}",
                f"- Expected artifacts: {', '.join(step.expected_artifacts) or 'none'}",
                "",
                "Acceptance criteria:",
                "",
                *[f"- {criterion}" for criterion in step.acceptance_criteria],
                "",
            ]
        )
    lines.extend(["## Global acceptance criteria", "", *[f"- {item}" for item in plan.global_acceptance_criteria]])
    return "\n".join(lines).rstrip() + "\n"


def _has_cycle(steps: list[PlanStep]) -> bool:
    graph = {step.key: step.depends_on for step in steps}
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> bool:
        if key in visiting:
            return True
        if key in visited:
            return False
        visiting.add(key)
        if any(dependency in graph and visit(dependency) for dependency in graph[key]):
            return True
        visiting.remove(key)
        visited.add(key)
        return False

    return any(visit(key) for key in graph)


def _looks_like_code_plan(plan: TaskPlan) -> bool:
    content = " ".join([plan.goal, plan.summary, *plan.affected_areas]).casefold()
    return any(hint in content for hint in CODE_HINTS) or any(
        step.agent == "code_generator" for step in plan.steps
    )


def _load_prompt() -> str:
    return (Path(__file__).resolve().parents[1] / "prompts" / "planner" / "v1.md").read_text(encoding="utf-8")
