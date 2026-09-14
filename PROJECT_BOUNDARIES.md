# Project boundaries

## Product mainline

`npc_app/` is the only production product mainline for the Guichao Island AI NPC backend. New runtime features, production fixes, API changes, quality gates, and deployment work must target this package.

The legacy generic assistant and development-agent application have been moved out of this repository. This repository must not add imports, scripts, dependencies, reports, or runtime configuration for them again.

## Dependency direction

```text
Unity client -> npc_app API -> dialogue/retrieval/memory -> database, Ollama, Milvus
```

## Release scope

NPC runtime releases include `npc_app/`, required game knowledge assets, ingestion and HTTP acceptance scripts, migrations, Unity protocol documentation, and direct runtime dependencies. Development-only automation belongs outside this repository.
