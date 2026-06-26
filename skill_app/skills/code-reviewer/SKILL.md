---
name: code-reviewer
description: Perform an evidence-based semantic review of generated Python candidates or npc_app patches against the request, acceptance criteria, diff, generated tests, execution report, configuration, and deterministic findings. Use before approval and during repair routing to identify exact blocking defects without weakening machine checks.
---

# Code Reviewer

## Objective

Decide whether the candidate is safe and complete enough to proceed to artifact approval. Review independently while preserving every deterministic failure.

## Review evidence

- Read the request and every acceptance criterion.
- Inspect complete changed files and the workspace diff.
- Compare generated tests with the behavior they claim to cover.
- Use compile and pytest results as facts; never invent successful execution.
- Retain deterministic findings and add model findings only when they provide distinct evidence.
- For multi-file `npc_app` work, check contracts across API, schema, database, and service layers.

## Review dimensions

Check:

- Requirement coverage and semantic correctness.
- Boundary conditions, invalid inputs, and error handling.
- Backward compatibility and preservation of unrelated behavior.
- API/schema/service/config consistency.
- Security, path safety, unsafe execution, secret exposure, and hidden side effects.
- State isolation, thread or NPC separation, and knowledge-boundary behavior when relevant.
- Completeness: no TODOs, stubs, dead placeholders, or metadata-only implementations.
- Test quality: meaningful assertions, missing branches, and false-positive tests.
- Maintainability issues only when they create a concrete product risk.

## Finding contract

Return findings with:

- `severity`: `critical`, `high`, `medium`, or `low`.
- `category`: a stable concise category.
- `file`: an actual candidate file.
- `line`: an exact valid candidate line for every critical, high, or medium finding.
- `title`: a specific defect statement.
- `evidence`: observed evidence containing `file:line` when a line is present.
- `suggestion`: a targeted repair direction without rewriting the whole solution.
- `blocking`: `true` for critical, high, and medium; `false` for low.

Do not cite files or line numbers outside the candidate.

## Approval rules

Block approval for:

- Compilation or pytest failure.
- Unmet acceptance behavior.
- Critical, high, or medium findings.
- Configuration or cross-layer contract mismatch.
- Source-project write attempts or path escape.
- Unsafe dynamic execution or material security defects.
- Missing implementation hidden behind placeholders.

Use low severity only for a real non-blocking improvement. Do not manufacture findings to fill a report.

## Output contract

For the generic workflow, return only `SemanticReviewResult` with `summary` and `findings`; the platform computes approval.

For the npc_app workflow, return only `NpcAppReviewResult`. Keep deterministic findings intact and avoid duplicates that add no new evidence.

## Completion criteria

Finish only when every criterion has been considered, execution evidence has been respected, blocking findings are precisely actionable, and approval is not granted in the presence of unresolved blocking evidence.
