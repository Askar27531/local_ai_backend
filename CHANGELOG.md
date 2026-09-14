# Changelog

## Unreleased

- Consolidated the NPC runtime into 30 Python files with flat API, contract, database, trace, and utility modules while preserving the Unity HTTP and NDJSON contracts.
- Retired the offline evaluation package, datasets, A/B runner, and real-answer quality script; retained core pytest coverage and the real HTTP acceptance runner.
- Consolidated repeated context budgeting, JSON parsing, static stream events, chat persistence, and single-use memory helpers.
- Added strict Unity/auth request-domain validation, UUID thread IDs, NPC allowlisting, bounded game-state lists, parameterized Milvus filters, explicit memory-record validation, and corrected the deployed memory-vector dimension to 512.
- Split long-term-memory Milvus lifecycle management from runtime traffic: an explicit initializer creates the versioned collection, startup validates and loads it, readiness checks its schema, and retrieval/upsert paths no longer create infrastructure.
- Tightened early-game Lira retrieval to clinical evidence, added a deterministic guard for unsupported location advice, and documented explicit evidence-first medical dialogue constraints.
- Reduced local `qwen3:14b` latency by using deterministic planning for ordinary turns, reserving the LLM planner for ambiguous/challenge turns, disabling reasoning output, and bounding dialogue/planner generation to 256/128 tokens.
- Added a real `qwen3:14b` answer-quality evaluation covering six NPC voices, story safety, groundedness, multi-turn memory, relevance, conciseness, and latency with auditable JSON/Markdown output.
- Changed the default Ollama dialogue model from `qwen3:latest` to `qwen3:14b` across runtime configuration and local startup documentation.
- Added a real HTTP NPC acceptance runner with multi-turn Chinese dialogue scenarios, strict NDJSON assertions, cross-NPC isolation checks, and JSON/Markdown reports.
- Added a bounded, structured NPC Turn Planner with deterministic character-arc stages, dialogue strategies, LLM planning for complex turns, and policy-safe fallback behavior.
- Integrated turn plans into retrieval and prompt construction without allowing plans to relax existing story gates or low-risk fast paths.
- Added a turn-plan-driven Dynamic Context Compiler with per-strategy budgets, explainable selection manifests, and strategy-aware memory ranking.
- Kept baseline context behavior available when no turn plan is supplied and kept context manifests internal by default so the Unity protocol does not expose story sources.
- Split dialogue-derived `player_claim` memories from Unity-confirmed `player_fact` memories; only structured `presented_items` can create confirmed facts.
- Added opt-in JSON NPC turn traces covering planning, retrieval, selected context identifiers, prompt size, generation latency, answer guards, and failure stages without recording credentials or dialogue content.
- Added 30 deterministic cognitive and multi-turn evaluation cases with CI gates for turn planning, dynamic context, memory semantics, and character-stage trajectories.
- Added a 15-scenario, 20-turn real Ollama/Milvus baseline-versus-candidate A/B runner with reproducible JSON and Markdown reports.
- Closed a story-safety gap by applying the final sensitive-answer guard to locked `ask_sensitive_truth` turns as well as indirect sensitive outputs.
- Removed the legacy `/npc/chat/stream` endpoint; Unity now uses `/v1/npc/chat/stream` exclusively.
- Removed backend HTTP end-to-end chat scripts. Unity protocol and NDJSON integration tests now belong to the Unity project.
- Split health, authentication, and Unity NPC routes into focused `npc_app/api/` modules; `main.py` now only assembles the application.
- Removed the unused debug-UI endpoints `/auth/me`, `/threads`, and `/threads/{thread_id}/records` with their response schemas and service helpers.
- Reduced the Demo database to five minimal tables and removed unused status, display, audit, provenance, timestamp, duplicated ownership, and ORM relationship fields.
- Moved vector memories to the minimal `npc_long_term_memories_v2` collection.
- Made PostgreSQL via `psycopg` the only Runtime database, with strict startup validation and no fallback database path.
- Consolidated local authentication, model, Milvus, memory, and database settings in the ignored `.env` file and removed the example configuration copy.
- Changed explicit Unity thread handling to reject mismatched `thread_id` and `npc_id` values with HTTP 409 instead of silently selecting or creating another thread.
- Routed the optional LLM Planner through `ChatOllama.with_structured_output(json_schema)`: Ollama constrains generation by a Pydantic-derived JSON Schema and Pydantic re-validates the parsed result, replacing hand-rolled JSON extraction and per-field coercion; deterministic rule fallback and the only-tighten merge semantics are unchanged.

All notable changes to the AI NPC product mainline are recorded here.

## [Unreleased]

No changes yet.

## [0.3.0] - 2026-08-05

### Added

- Added the typed `DialogueOrchestrator`, centralized intent/policy/story rules, and a structured Unity `/v1/npc/chat/stream` contract.
- Added shared offline dialogue gates and real Milvus conditional-retrieval evaluation.

### Changed

- Replaced the fixed LangGraph pipeline with a direct, testable single-turn runtime.
- Consolidated character dialogue, context budgeting, text helpers, retrieval filtering, and prompt construction.
- Reduced runtime dependencies and scoped CI, coverage, and documentation to the NPC product mainline.

### Removed

- Removed obsolete `app`/`skill_app` remnants, unused Checkpointer code, dead service wrappers, and duplicated NPC routing modules.

### Verification

- Ruff, 21 Pytest cases, and 23 offline evaluation cases pass.
- Real Milvus conditional retrieval passes with 100% Recall@5 and zero access violations.

## [0.2.0] - 2026-08-03

### Added

- Declared `npc_app` as the sole NPC product mainline and documented repository dependency boundaries.
- Added the Python 3.12.8 version marker and documented local environment configuration.
- Added `/ready` checks for the database, configured Ollama model, Milvus, and the world-knowledge collection.
- Added dependency-health regression tests and actionable readiness failure hints.
- Added this changelog and a single runtime version source.

### Changed

- Pinned previously unbounded runtime and engineering dependencies to the verified local versions.
- Expanded the CI lint gate from evaluation code to the complete `npc_app` package.
- Updated startup, quality-gate, project-boundary, and dependency troubleshooting documentation.
- Applied the configured Ruff rules across `npc_app`.

### Repository hygiene

- Excluded coverage outputs, caches, temporary probes, downloaded local models, and generated NPC evaluation reports.
- Preserved all pre-existing uncommitted work; no user changes were deleted, reverted, or committed.

### Known release blocker

- A `v0.2.0` Git tag has not been created because the repository already contains a large uncommitted refactor that must be reviewed and committed as an intentional baseline first.
