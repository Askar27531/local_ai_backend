# Replay demo

A static, dependency-free page that replays **real recorded runs** of the NPC runtime. Published to GitHub Pages by [`.github/workflows/pages.yml`](../.github/workflows/pages.yml).

## Why replay instead of a live demo

A live demo of this backend would need a public host running Ollama with a 14B model, a Milvus server, and PostgreSQL — roughly 10 GB+ of RAM and no free tier. Replay keeps the demo honest and permanently available: the page shows exactly what the real stack produced, and costs nothing to host.

The tradeoff is explicit: **the page cannot answer questions you type.** Interaction is zero; fidelity is one.

## Layout

| File | Purpose |
| --- | --- |
| `index.html` | Page structure (`zh-CN`), tab panels |
| `app.css` | Styling; dark theme, responsive down to mobile |
| `app.js` | Run/step selection, event-chip animation, typewriter replay, metadata inspector |
| `data.js` | The replay corpus — every value copied verbatim from the reports below |

No build step, no bundler, no framework, no runtime dependencies. Opening `index.html` directly from disk works.

## Data sources

Three views are backed by real reports:

| View | Source |
| --- | --- |
| 验收回放 — 2026-08-08, `qwen3:latest`, 11/11 | `reports/npc_real_acceptance/report.json` |
| 验收回放 — 2026-08-13, `qwen3:14b`, 10/11 | `reports/npc_real_acceptance_boundaries/report.json` |
| 七个角色声线 | `reports/npc_dialogue_ab/report.json` |

Questions, intent hints, and game state come from `scripts/run_real_npc_acceptance.py`, which constructs the actual HTTP requests.

## Honesty rules

The page follows these rules deliberately, and they are visible in the UI:

1. **No invented values.** On the failed turn the report recorded no retrieval metrics (the assertion fired before details were returned), so the UI shows `未记录` rather than `0`.
2. **No invented questions.** The A/B report records only `scenario_id`, never the question text, so the voice samples show the scenario id and the real answer only.
3. **No invented sources.** The reports record how many knowledge chunks were retrieved, not which files, so the UI shows counts only.
4. **No hidden failures.** The 2026-08-13 run passed 10/11 because long-term memory recall failed a strict assertion. That failure ships with the demo, along with the assertion text and an explanation.

## Regenerating the data

`data.js` is hand-transcribed from the reports and verified against them. To re-verify after a new run:

1. Re-run the acceptance suite against a live stack:

   ```powershell
   python scripts\run_real_npc_acceptance.py --output reports\npc_real_acceptance
   ```

2. Update `data.js` from the new `report.json`.
3. Cross-check every field against the report before committing.

> Note: the run reports under `reports/` are gitignored as generated artifacts, so their current contents are the source of record for this demo. Regenerating them changes what the demo must claim.
