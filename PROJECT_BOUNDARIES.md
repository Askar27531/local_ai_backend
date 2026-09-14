# Project boundaries

## Product mainline

`npc_app/` is the only production product mainline for the Guichao Island AI NPC backend. New runtime features, production fixes, API changes, quality gates, and deployment work must target this package.

The legacy generic assistant and development-agent application have been moved out of this repository. This repository must not add imports, scripts, dependencies, reports, or runtime configuration for them again.

## Presentation surface

`demo/` is a static, replay-only showcase of recorded runtime results, published to GitHub Pages. It exists so a reviewer can inspect real NPC dialogue, per-turn metadata, and acceptance outcomes without installing the stack.

Constraints for `demo/`:

- it stays static — no build step, no framework, no network requests, no credentials;
- it must not become a second product surface, and must not reintroduce the removed debug-UI endpoints;
- every value it displays must be traceable to a report committed to this repository;
- it must not present itself as a live service, and must keep failed checks visible.

## Dependency direction

```text
Unity client -> npc_app API -> dialogue / retrieval / memory -> database, Ollama, Milvus
demo (static, read-only) -> recorded run reports
```

## Release scope

NPC runtime releases include `npc_app/`, required game knowledge assets, ingestion and HTTP acceptance scripts, migrations, Unity protocol documentation, and direct runtime dependencies. Development-only automation belongs outside this repository.
