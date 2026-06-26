---
name: test-generator
description: Design and generate focused deterministic pytest coverage for a Python candidate using the request, acceptance criteria, candidate content, and static validation evidence. Use when the generic or npc_app code workflow needs executable semantic tests, compile checks, failure evidence, and explicit uncovered-risk reporting.
---

# Test Generator

## Objective

Prove whether the candidate implements the requested behavior. Test semantics, not merely importability or the presence of names.

## Analyze before writing

- Derive test cases from each acceptance criterion and explicit exclusion.
- Inspect the complete candidate and identify its public entry points, branches, boundaries, and failure behavior.
- Include regression coverage for supplied validation or review failures.
- Prefer observable behavior over implementation details.
- Record important integration behavior that cannot be tested safely as an uncovered risk.

## Build the test plan

Include:

- At least one normal success case.
- Relevant boundary or edge cases.
- Explicitly requested invalid-input or exclusion cases.
- Cross-layer behavior when API, schema, service, or persistence contracts are present.
- A precise list of risks left untested.

Do not claim coverage for behavior that the generated tests do not assert.

## Generate deterministic pytest

1. Use the exact candidate and managed test paths supplied by the platform.
2. Load a sibling candidate with `importlib.util` when ordinary package import is unsuitable.
3. Write at least one function named `test_*`.
4. Make assertions specific enough to fail when the requested behavior is absent.
5. Keep tests independent, deterministic, and below 300 lines.
6. Use pytest fixtures or temporary paths only when needed.

## Output contract

Return one JSON object matching `GeneratedTestSuite` or its workflow-specific subtype:

- `plan.candidate_path`: exactly the supplied candidate path.
- `plan.test_path`: exactly the supplied managed test path.
- `plan.cases`: concrete behaviors asserted by the test code.
- `plan.uncovered_risks`: honest remaining risks.
- `test_content`: complete syntactically valid pytest source.

Do not add prose outside the structured object.

## Prohibited behavior

- Do not use network services, Ollama, Milvus, external databases, shell commands, sleeps, or environment mutation.
- Do not write outside pytest-managed temporary paths.
- Do not import `os`, `subprocess`, `socket`, `requests`, `httpx`, `urllib`, or `shutil`.
- Do not call `open`, `eval`, `exec`, `compile`, `__import__`, or `system`.
- Do not patch away the behavior under test or duplicate the implementation inside the test.
- Do not reduce assertions to make a defective candidate pass.

## Interpret execution evidence

- Treat compilation failure as blocking.
- Treat pytest failure as blocking.
- Preserve stdout, stderr, and static-validation errors for the repair loop.
- Separate an implementation failure from a malformed or unsafe generated test.

## Completion criteria

Finish only when the plan and test source agree, paths match exactly, syntax is valid, tests exercise the request and criteria, safety restrictions are respected, and uncovered risks are explicit.
