# Replay demo

A static, dependency-free page that presents **real recorded runs** of the NPC runtime. Published to GitHub Pages by [`.github/workflows/pages.yml`](../.github/workflows/pages.yml).

## Layout

| File | Purpose |
| --- | --- |
| `index.html` | Page structure (`zh-CN`), tab panels |
| `app.css` | Styling; dark theme, responsive down to mobile |
| `app.js` | Run/step selection, event-chip animation, typewriter replay, metadata inspector |
| `data.js` | The replay corpus, transcribed from the reports below |

No build step, no bundler, no framework, no runtime dependencies. Opening `index.html` directly from disk works.

## Deep links

The URL hash selects what is shown, so a specific acceptance turn can be linked and shared directly:

```text
#run=0&step=3    # first run, the guarded Subject 07 turn
#run=1&step=5    # second run, the long-term-memory turn
```

`run` is 0-based over the two recorded runs; `step` is 0-based over that run's 11 checks. Out-of-range values are clamped to the valid range.

## Data sources

| View | Source |
| --- | --- |
| 验收回放 — 2026-08-08, `qwen3:latest` | `reports/npc_real_acceptance/report.json` |
| 验收回放 — 2026-08-13, `qwen3:14b` | `reports/npc_real_acceptance_boundaries/report.json` |
| 七个角色声线 | `reports/npc_dialogue_ab/report.json` |

Questions, intent hints, and game state come from `scripts/run_real_npc_acceptance.py`, the script that constructs the actual HTTP requests.

## Regenerating the data

`data.js` is transcribed from the reports and verified against them field by field. After a new run:

1. Re-run the acceptance suite against a live stack:

   ```powershell
   python scripts\run_real_npc_acceptance.py --output reports\npc_real_acceptance
   ```

2. Update `data.js` from the new `report.json`.
3. Cross-check every field against the report before committing.

> The run reports under `reports/` are gitignored as generated artifacts, so their current contents are the source of record for this demo.
