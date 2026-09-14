# Guichao Island AI NPC Backend

A monolithic AI NPC runtime for a Unity exploration RPG (《归潮之岛》 / Guichao Island). It powers in-game character dialogue with story-knowledge gating, Milvus RAG retrieval, long-term memory, and NDJSON streaming output.

> Detailed Chinese startup and operations guide: [README_启动说明.md](README_启动说明.md)

## Features

- **Single Unity dialogue endpoint** — `POST /v1/npc/chat/stream` with NDJSON streaming.
- **Anti-spoiler story gating** — knowledge is filtered by `unlock_level` at retrieval, context-compilation, and answer-guard layers.
- **Milvus RAG** — world-knowledge retrieval plus a versioned long-term-memory collection.
- **Deterministic turn planner** — character-arc stages and dialogue strategies, with an LLM planner reserved for ambiguous or challenge turns.
- **Bearer-token auth** — `POST /auth/register` and `POST /auth/login`.
- **Privacy-safe tracing** — opt-in structured JSON turn traces that never record credentials or dialogue content.
- **CI quality gate** — Ruff, Pytest, and ≥70% coverage on `npc_app`.

## Tech stack

| Layer | Technology |
| --- | --- |
| Runtime | Python 3.12, FastAPI, Uvicorn |
| Database | PostgreSQL via SQLAlchemy + `psycopg` |
| LLM | Ollama (`qwen3:14b`) via LangChain `ChatOllama` |
| Vector store | Milvus via `pymilvus` |
| Embeddings / rerank | `sentence-transformers`, `sentencepiece` |

## Repository layout

```text
npc_app/              # NPC runtime (API, dialogue, retrieval, memory, tests)
  api.py              # health, auth, and the Unity NDJSON dialogue endpoint
  contracts.py        # Unity + auth request contracts
  database.py         # PostgreSQL config and runtime models
  trace.py            # privacy-safe turn trace
  utils.py            # text / LLM-output / JSON helpers
  dialogue/           # orchestrator, intent, planner, context, characters
  services/           # chat, retrieval, memory, prompt construction
  tests/              # unit + acceptance-helper tests
game_docs/            # world lore, characters, quests, and knowledge assets
scripts/              # ingestion, validation, retrieval diagnostics, acceptance
.github/workflows/    # NPC quality gate (CI)
```

## Requirements

- Python 3.12.8
- Ollama with the `qwen3:14b` model
- Milvus on port `19530`
- PostgreSQL for runtime persistence

Python dependencies are pinned in [`requirements.txt`](requirements.txt); development/test tooling is in [`requirements-dev.txt`](requirements-dev.txt).

## Quick start

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements-dev.txt
```

All local configuration lives in the project-root `.env` file (ignored by Git). Set the database, Ollama, model, and Milvus values there, then start the API:

```powershell
uvicorn npc_app.main:app --host 0.0.0.0 --port 8001 --reload --env-file .env
```

Endpoints:

```text
http://127.0.0.1:8001/docs
http://127.0.0.1:8001/health
http://127.0.0.1:8001/ready
```

## Quality

```powershell
python -m pip check
python -m ruff check npc_app
python -m pytest npc_app\tests --cov=npc_app --cov-report=term-missing --cov-fail-under=70
```

See [README_启动说明.md](README_启动说明.md) for the full setup, configuration, API contract, and troubleshooting guide.

## Documentation

- [README_启动说明.md](README_启动说明.md) — Chinese startup and operations guide
- [CHANGELOG.md](CHANGELOG.md) — version history
- [PROJECT_BOUNDARIES.md](PROJECT_BOUNDARIES.md) — product scope and dependency direction
