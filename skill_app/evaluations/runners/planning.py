from __future__ import annotations

from types import SimpleNamespace

from skill_app.agents.planner import PlannerAgent, validate_plan


def run_case(case: dict) -> dict:
    task = SimpleNamespace(
        request=case["request"],
        task_type=case["task_type"],
        risk_level="low",
    )
    plan = PlannerAgent().build_baseline_plan(task, [])
    validation = validate_plan(plan)
    agents = {step.agent for step in plan.steps}
    required = set(case["required_agents"])
    forbidden = set(case["forbidden_agents"])
    dependencies = {(step.key, dependency) for step in plan.steps for dependency in step.depends_on}
    expected_dependencies = {tuple(item) for item in case["required_dependencies"]}
    recall = len(required & agents) / len(required) if required else 1.0
    irrelevant = len(forbidden & agents) / len(agents) if agents else 0.0
    dependency_score = (
        len(expected_dependencies & dependencies) / len(expected_dependencies)
        if expected_dependencies
        else 1.0
    )
    passed = validation.valid and recall == 1 and irrelevant == 0 and dependency_score == 1
    return {
        "case_id": case["id"],
        "passed": passed,
        "metrics": {
            "plan_structure_valid": float(validation.valid),
            "required_step_recall": recall,
            "irrelevant_step_rate": irrelevant,
            "dependency_graph_correct": dependency_score,
        },
        "details": {
            "agents": sorted(agents),
            "validation_errors": validation.errors,
            "dependencies": sorted([list(item) for item in dependencies]),
        },
    }
