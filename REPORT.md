# ipcbench — real-work agent benchmark
_seed 42 · temp 0 · 2 runs/task · 2026-11-23 13:23_

## Per-task results

| Task | Category | 3090 Strata Qwen3.8-Flash-Next IQ4_XS (1M) |
|---|---|---|
| `blender-ipc-sensor` | complex-tool-use | ❌ 82.1 t/s · 28.0r · 32.5 tools · ⚠output |
| `fr-devis-docx` | french-business | ⚠️ 88.8 t/s · 20.0r · 21.0 tools · ⚠output |
| `long-context-log-dig` | long-context-fidelity | ✅ 90.0 t/s · 1.0r · 0 tools |
| `netgear-port-remap` | long-agentic-coding | ✅ 83.9 t/s · 14.5r · 16.5 tools |
| `online-veille-reglementaire` | simple-tool-use | ✅ 71.9 t/s · 2.0r · 1.0 tools |
| `pfsense-rule-triage` | infra-reasoning | ✅ 86.7 t/s · 2.0r · 2.0 tools |
| `playwright-captcha` | hard-browser-use | ❌ 66.4 t/s · 18.0r · 19.0 tools · ⚠output |
| `tool-discipline-ipc` | tool-discipline | ✅ 67.3 t/s · 7.0r · 3.5 tools |

Legend: ✅ both runs pass · ⚠️ mixed · ❌ both fail · ⚠output = empty/malformed output seen.

## Summary

| Target | Pass | Partial | Fail | Runs | Median decode tok/s |
|---|---|---|---|---|---|
| 3090 Strata Qwen3.8-Flash-Next IQ4_XS (1M) | 5 | 1 | 2 | 16 | 82.5 |

## Method

- Same tasks and settings on every target; thinking at production effort.

- Objective graders only (`tasks/<id>/checks.py`), no LLM judge.

- `online_search` uses the real Tavily MCP; Blender and Playwright use real MCP sessions.

- Details, traces and screenshots are available in the shipped dashboard (`dashboard/`).

