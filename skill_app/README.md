# skill_app

`skill_app` is the clean foundation of the game production Agent platform.

It contains only generic infrastructure:

- task lifecycle
- workflow run records
- skill discovery
- controlled tool registry
- artifact metadata and version records
- human approval records
- evaluation-ready database models
- strict task state transitions
- ordered task event history
- persistent LangGraph workflow checkpoints
- executable, pausable, resumable, and cancellable workflow runs
- real local artifact storage with SHA-256 integrity checks
- automatic per-task artifact versioning
- policy-controlled Tool Runtime with input validation, timeout, audit, and output truncation
- safe project read tools, workspace write tools, deterministic validators, and structured test runners
- managed TaskWorkspace records with isolated `copy_subset` file copies
- workspace-wide unified Diff Artifacts and guarded cleanup
- profile-controlled Model Gateway with injectable providers
- text, structured, and streaming model invocation with ModelCall audit records
- Ollama/Qwen3 structured outputs through JSON Schema with configurable reasoning control
- one-shot structured-output repair and raw-output Artifact retention
- executable Planner Agent with strict TaskPlan schema and deterministic semantic validation
- JSON and Markdown plan Artifacts, staged pending steps, and risk-based plan approval
- end-to-end `game_feature` workflow with isolated generation, validation, repair, review, approval, and delivery
- version-retained candidates and a final delivery Manifest without source-project mutation
- schema-versioned NPC behavior configuration generation with YAML, normalized JSON, validation, explanation, and config diff
- model-driven, bounded, hash-aware Python patch generation with affected-file and change explanations
- generated compile/pytest/FastAPI tests plus evidence-based structured code review
- model-generated semantic pytest plans and tests grounded in requests and acceptance criteria
- model-generated semantic code review merged with non-bypassable deterministic gates
- evidence-grounded, hash-locked automatic repair with retained candidate versions
- project-specific multi-file `npc_app_feature` production workflow
- executable Skill Packages with machine-readable manifests and immutable run snapshots
- Skill-bound workflows, output schemas, validators, limits, retry budgets, and tool allowlists
- Agent Policy and Skill Policy intersection with path-scoped data access
- project-specific `game_text_production` workflow with lore and spoiler gates
- active `game-text-writer` Skill Package with frozen `lore-reviewer` component instructions
- versioned offline evaluation suites with persisted case metrics, regression comparison, and operational aggregation
- approved Visual Briefs, policy-controlled image generation, deterministic image validation, Contact Sheets, and art delivery Manifests

NPC-specific tests, prompts, report summarizers, and free-form tool loops are intentionally excluded. New domain capabilities should be added as isolated skills, tools, agents, and workflows.

## Run

```powershell
uvicorn skill_app.main:app --host 127.0.0.1 --port 8002 --reload
```

The default database is `.skill_app_data/skill_app.sqlite3`. Override it with `SKILL_APP_DATABASE_URL`.

## Engineering checks

```powershell
pytest skill_app/tests
ruff check skill_app
alembic upgrade head
```

Local development can create tables automatically. Set `SKILL_APP_AUTO_CREATE_SCHEMA=false` when schema changes must be managed exclusively through Alembic.

## Task lifecycle endpoints

```text
POST /tasks
GET  /tasks/{task_id}
POST /tasks/{task_id}/start
POST /tasks/{task_id}/skills/{skill_name}/start
GET  /tasks/{task_id}/skill-runs
GET  /skill-packages
POST /tasks/{task_id}/approvals
POST /approvals/{approval_id}/resolve
POST /tasks/{task_id}/cancel
GET  /tasks/{task_id}/events
GET  /workflow-runs/{workflow_run_id}
GET  /workflow-runs/{workflow_run_id}/steps
POST /workflow-runs/{workflow_run_id}/resume
POST /workflow-runs/{workflow_run_id}/cancel
POST /tasks/{task_id}/artifacts/text
POST /tasks/{task_id}/artifacts/upload
GET  /artifacts/{artifact_id}
GET  /artifacts/{artifact_id}/content
POST /tasks/{task_id}/tools/{tool_name}/execute
POST /tasks/{task_id}/workspaces
GET  /tasks/{task_id}/workspaces
GET  /workspaces/{workspace_id}
POST /workspaces/{workspace_id}/files
POST /workspaces/{workspace_id}/diff
POST /workspaces/{workspace_id}/cleanup
GET  /model-profiles
POST /models/invoke/text
GET  /tasks/{task_id}/model-calls
POST /evaluations/runs
GET  /evaluations/runs
GET  /evaluations/runs/{run_id}
GET  /evaluations/datasets
GET  /evaluations/aggregate?group_by=agent|workflow|model_profile
```

Task mutations and their corresponding events are committed in the same database transaction.

The built-in `foundation_smoke` workflow creates a candidate artifact, pauses at an approval gate, and resumes from a SQLite LangGraph checkpoint after approval.

The built-in `planner` workflow scans project structure read-only, loads the
`game-feature-planning` skill, invokes the configured Model Gateway with a strict
TaskPlan schema, validates agents/tools/permissions/dependencies, writes JSON and
Markdown plan Artifacts, and stages the future work as pending StepRun records.
High-risk plans pause at a human approval gate; low-risk plans become ready for
execution immediately. Planner workflow completion leaves the task in `running`
instead of marking the requested production work as delivered.

The built-in `game_feature` workflow can start directly from a draft task or
reuse the latest validated plan from a completed Planner workflow. Its first
version supports one candidate type per task: structured JSON game configuration
or an isolated Python candidate. Validation and review may trigger at most two
repairs. Exhaustion records `manual_takeover_required` and keeps every candidate
version. Approved delivery contains a Workspace diff, validation reports, review
reports, and `delivery-manifest.json`; the source project is never modified.

The built-in `npc_app_feature` workflow targets the actual `npc_app/` backend.
It generates up to five Python files, hash-locks existing targets inside
TaskWorkspace, compiles the complete patch, generates focused pytest coverage,
performs multi-file review, repairs failures up to two times, and pauses for
human approval. Delivery contains a unified diff and
`npc-app-delivery-manifest.json` without modifying formal source files.

The built-in `game_text_production` workflow targets the actual `game_docs/`
package. It generates or revises one Markdown candidate, validates RAG
frontmatter, unlock-level headings, low-level spoiler terms, and developer
language, then reviews canon, chronology, NPC voice, retrieval clarity, and
knowledge boundaries. Delivery contains validation and lore-review reports, a
unified diff, and `game-text-delivery-manifest.json`.

The preferred Skill Runtime entry point freezes both writer and lore-reviewer
instructions, binds their stage-specific schemas, limits access to
`game_docs/**`, applies deterministic text and lore gates, and retains the
two-repair plus approval state machine:

```json
POST /tasks/{task_id}/skills/game-text-writer/start
{"version": "1.0.0"}
```

The built-in `art_asset` workflow supports item icons, UI icons, and concept-art
drafts. It first persists a structured Visual Brief and pauses before invoking
the policy-controlled `generate_image_candidates` Tool. After approval, it
generates multiple candidates, validates PNG dimensions/format/alpha, registers
the originals and Contact Sheet as Artifacts, and pauses again for human
selection. The selected image and `art-delivery-manifest.json` retain model,
prompt, negative prompt, seed, dimensions, MIME, alpha, references, validation,
and approval metadata. Automated tests use a deterministic fake image provider;
production providers can be injected without changing the workflow.

NPC configuration requests are handled by the `game-config-generator` skill.
The editable candidate is `npc_behavior.yaml`, validated against the version 1
contract in `skill_app/config_schemas/npc_behavior_v1.json`. Deterministic checks
cover YAML syntax, schema shape, ranges, enum values, threshold ordering, version,
and NPC group references. A successful run also produces normalized JSON, a
validation report, requirement-traceable design notes, and a configuration diff.
The YAML candidate must pass Artifact Approval before delivery.

Python requests use the `code-generator`, `test-generator`, and `code-reviewer`
skills. Code candidates are limited to five files and 400 changed lines, remain
inside TaskWorkspace, and produce a unified patch plus change explanation.
Existing files can be hash-locked to prevent stale patch application. The Test
Agent writes a test patch, runs Python compilation and focused pytest checks, and
supports FastAPI `TestClient` smoke tests when an app is detected. The Review
Agent correlates acceptance criteria, Workspace diff, test reports, configuration
consistency, and static checks. Blocking findings always include file and line
evidence. Failed tests or blocking findings return to the bounded repair loop and
cannot request Artifact Approval.

Offline quality regression is available through `/evaluations`. Version 1 ships
40 curated JSONL cases across planning, NPC configuration, code patches, and code
review. Every case metric is stored in `EvaluationResult`, grouped by an
`EvaluationRun` containing app version and dataset SHA-256. Repeated runs compare
metric deltas with the previous successful run and expose exact failed cases.
Operational aggregation is available by Agent, Workflow, and Model Profile.

Artifact files are stored under `.skill_app_data/artifacts/{task_id}/{artifact_id}/`. Clients provide content and metadata; the platform owns the storage URI, SHA-256, byte size, and version.

Tool permissions are defined in `skill_app/policies/default.yaml`. Agents receive no implicit tools: each tool must be explicitly allowed. Writes are restricted to managed `.skill_app_data/workspaces/{task_id}/{workspace_id}/` roots. Cleanup verifies that persisted paths still match this exact layout, and generated Diff Artifacts remain available after workspace removal.

Model profiles are defined in `skill_app/model_profiles/default.yaml`. Callers select a profile name only; provider URLs and credential environment variables are controlled by configuration. The runtime default is `ollama-qwen3-14b`, which targets the local Ollama OpenAI-compatible endpoint and model `qwen3:14b`. It uses OpenAI-compatible JSON Schema structured outputs and disables Qwen3 reasoning for production-schema calls. Local Ollama requests bypass environment HTTP proxies. The `local-default` fake profile remains available for deterministic automated tests.

The Planner, Python Code Generator, Python Test Agent, and Python Review Agent now
use the configured Model Gateway.
Planner output is validated as `TaskPlan`; code output is validated as
`CodeGenerationResult`, constrained to one Python candidate in workflow version 1,
compiled, tested, reviewed, and held for Artifact Approval. Test output is validated
as `GeneratedTestSuite`, checked for managed paths and unsafe imports or calls, then
executed with compile and focused pytest tools. Deterministic baseline generation
remains available when the fake profile is selected.

Review output is validated as a concise semantic review result and merged with
deterministic compilation, test, diff, configuration, TODO, and dynamic-execution
findings. The platform recalculates approval, normalizes evidence to verified
candidate line locations, and never permits model output to remove or weaken a
deterministic blocker.

Automatic code repair feeds the previous candidate, verified SHA-256, generated
test plan and source, full execution report, acceptance criteria, blocking review
findings, and repair history back to the Code Generator. Repairs must keep the
candidate path, change the content, preserve the previous hash lock, and pass a
fresh compile, pytest, and semantic review cycle. Every repaired candidate records
its parent Artifact and evidence Artifact IDs.

Start the Planner Agent through the normal workflow endpoint:

```json
POST /tasks/{task_id}/start
{"workflow_name": "planner", "graph_version": "1"}
```

Start the end-to-end production workflow with:

```json
POST /tasks/{task_id}/start
{"workflow_name": "game_feature", "graph_version": "1"}
```

Start the art asset workflow with:

```json
POST /tasks/{task_id}/start
{"workflow_name": "art_asset", "graph_version": "1"}
```

Approve the Visual Brief first. At the candidate-selection approval, omit the
comment to select the first candidate or use `candidate=<artifact-id>` to select
a specific candidate from the Contact Sheet.

Start the project-specific workflows with:

```json
POST /tasks/{task_id}/start
{"workflow_name": "npc_app_feature", "graph_version": "1"}
```

The preferred entry point for the migrated `npc-app-feature` Skill Package is:

```json
POST /tasks/{task_id}/skills/npc-app-feature/start
{"version": "1.0.0"}
```

This entry point validates `skill.yaml`, snapshots its SHA-256 and instructions,
binds `npc_app_feature@1`, applies Skill limits and validators, intersects tool
permissions, and persists a `SkillRun` through pause, resume, and completion.

```json
POST /tasks/{task_id}/start
{"workflow_name": "game_text_production", "graph_version": "1"}
```
