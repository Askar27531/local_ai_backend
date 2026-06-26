---
name: npc-app-feature
description: Implement or repair bounded multi-file Python changes specifically under npc_app using the existing FastAPI, schema, database, service, NPC-context, and test architecture. Use when a request changes npc_app backend behavior and requires complete files, hash-aware replacement, focused pytest, cross-layer review, repair evidence, workspace isolation, and human approval.
---

# NPC App Feature

## Objective

Implement the requested `npc_app` backend behavior as a coherent, minimal patch across the layers that genuinely need to change.

## Read project context

- Inspect the relevant existing `npc_app` modules and tests before generating code.
- Follow established naming, typing, dependency injection, database access, schema, and FastAPI patterns.
- Trace request flow across route, schema, service, model, and persistence layers when applicable.
- Preserve public APIs and stored-data compatibility unless the request explicitly changes them.
- Do not invent broad player, quest, inventory, history, or NPC systems that were not requested.

## Generate the patch

1. Select the fewest files needed for a complete change.
2. Return complete Python file contents, not snippets or diffs.
3. Keep every path under `npc_app/`.
4. Keep affected-file order identical to patch-file order.
5. Include the current supplied SHA-256 when replacing an existing file.
6. Keep behavior deterministic and testable without live external services.
7. Preserve unrelated functions, imports, routes, schemas, and side effects.

## Cross-layer consistency

When a layer is changed, verify all consumers:

- FastAPI routes use the correct request/response schemas and status behavior.
- Pydantic schemas match service and model fields.
- Services preserve thread, player, NPC, and knowledge-boundary isolation.
- Database models and queries remain compatible with current session patterns.
- Error handling produces intentional, stable outcomes.
- Async and sync boundaries follow the existing application architecture.

## Output contract

Return only one `NpcAppPatchResult`:

- `patch.files`: one to five complete `.py` files.
- `patch.explanation`: implementation rationale and compatibility notes.
- `affected_files`: exactly all patch paths in the same order.
- `summary`: concise behavior-level result.

Keep total candidate content below 1200 lines and do not add fields outside the schema.

## Safety and exclusions

- Write only to TaskWorkspace; never modify source files directly.
- Do not write outside `npc_app/`.
- Do not edit SQLite files, caches, `game_docs`, `skill_app`, secrets, or environment configuration.
- Do not use shell execution, unsafe dynamic evaluation, uncontrolled network calls, or import-time external connections.
- Do not hide incomplete behavior behind TODOs, stubs, broad exception swallowing, or fake success values.

## Repair procedure

- Consume the previous complete patch, hashes, failed tests, compile output, review findings, acceptance criteria, and repair history.
- Preserve paths and unchanged working behavior.
- Update expected hashes from the previous candidate version.
- Change at least one file and resolve every blocking item at its root cause.
- Keep the repair focused; do not replace a small defect with a broad rewrite.

## Completion criteria

Finish only when paths and hashes are valid, all changed files form a coherent backend change, compile and focused pytest can run locally, cross-layer contracts are consistent, review has actionable evidence, and delivery still requires artifact approval.
