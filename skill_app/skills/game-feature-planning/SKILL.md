---
name: game-feature-planning
description: Convert a game-production engineering or documentation request into a minimal valid TaskPlan using only registered agents, skills, tools, artifact types, and project paths. Use when work must be decomposed into an acyclic, risk-aware, workspace-isolated plan with explicit validation and approval gates.
---

# Game Feature Planning

## Objective

Produce the smallest executable plan that can satisfy the request using capabilities actually present in the project context.

## Analyze the request

- Identify the requested outcome, affected areas, explicit constraints, and omissions that must not be invented.
- Distinguish documentation-only, configuration, generic code, npc_app, game-text, and art-asset work.
- Select relevant supplied project paths; do not fabricate paths.
- Reuse registered specialized workflows when their scope matches.
- Assign risk based on write scope, external tools, approvals, and possible product impact.

## Construct the plan

1. Give every step a unique lowercase `snake_case` key.
2. Select only registered agents, skills, and tools.
3. Grant each step only the tools required for that step.
4. Make dependencies explicit and acyclic.
5. Order dependencies so their artifacts exist before consumers run.
6. Define concrete expected artifacts using allowed artifact types.
7. Write measurable acceptance criteria for every step.
8. Add global criteria for source isolation and artifact traceability.

## Required workflow properties

- Keep source files read-only during planning.
- Route all candidate writes through TaskWorkspace.
- Include deterministic validation or tests for code work.
- Include review after validation when code can affect behavior.
- Do not introduce code-writing steps for documentation-only work.
- Require plan approval for high-risk plans or steps.
- Do not use an image provider before Visual Brief approval.
- Do not treat artifact approval as permission to modify the source project.

## Output contract

Return only one JSON object matching `TaskPlan`:

- `goal`: the requested outcome.
- `task_type`: the supplied task type.
- `summary`: concise execution strategy.
- `affected_areas`: actual project concerns.
- `relevant_paths`: existing or context-supplied paths.
- `steps`: one or more valid `PlanStep` objects.
- `global_acceptance_criteria`: measurable workflow-wide conditions.
- `risk_level`: `low`, `medium`, or `high`.
- `requires_plan_approval`: `true` whenever plan or step risk is high.

Each step must include at least one acceptance criterion. Do not add fields outside the schema.

## Reject invalid plans

Do not return a plan containing missing dependencies, dependency cycles, unregistered capabilities, denied tools, invalid artifact types, source-project writes, unvalidated code generation, or a high-risk path without approval.

## Completion criteria

Finish only when the plan is executable by the current registry, minimally scoped, dependency-valid, risk-correct, testable, and explicit about approvals and produced evidence.
