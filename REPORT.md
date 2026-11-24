# ipcbench — real-work agent benchmark
_seed 42 · temp 0 · 2 runs/task · 2026-11-24 06:16_

## Per-task results

| Task | Category | 3090 Strata Qwen3.8-Flash-Next IQ4_XS (1M) | PGX1 GB10 vLLM Qwen3.8-Flash-Next-FP4-Local (1M, :11474) |
|---|---|---|---|
| `blender-ipc-sensor` | complex-tool-use | ❌ 82.1 t/s · 28.0r · 32.5 tools · ⚠output | ❌ 18.6 t/s · 28r · 30 tools · ⚠output |
| `fr-devis-docx` | french-business | ❌ 73.7 t/s · 20.0r · 26.0 tools · ⚠output | ❌ 43.7 t/s · 20.0r · 23.5 tools · ⚠output |
| `long-context-log-dig` | long-context-fidelity | ✅ 90.0 t/s · 1.0r · 0 tools | ✅ 51.3 t/s · 1.0r · 0 tools |
| `netgear-port-remap` | long-agentic-coding | ✅ 83.9 t/s · 14.5r · 16.5 tools | ✅ 44.2 t/s · 12.0r · 12.5 tools |
| `online-veille-reglementaire` | simple-tool-use | ✅ 71.9 t/s · 2.0r · 1.0 tools | ✅ 37.2 t/s · 2.0r · 1.0 tools |
| `pfsense-rule-triage` | infra-reasoning | ✅ 86.7 t/s · 2.0r · 2.0 tools | ⚠️ 38.1 t/s · 2.0r · 2.0 tools |
| `playwright-captcha` | hard-browser-use | ❌ 51.8 t/s · 18.0r · 18.0 tools · ⚠output | ❌ 21.8 t/s · 18.0r · 22.5 tools · ⚠output |
| `tool-discipline-ipc` | tool-discipline | ✅ 67.3 t/s · 7.0r · 3.5 tools | ✅ 37.3 t/s · 7.0r · 3.0 tools |

Legend: ✅ both runs pass · ⚠️ mixed · ❌ both fail · ⚠output = empty/malformed output seen.

## Summary

| Target | Pass | Partial | Fail | Runs | Median decode tok/s |
|---|---|---|---|---|---|
| 3090 Strata Qwen3.8-Flash-Next IQ4_XS (1M) | 5 | 0 | 3 | 16 | 80.6 |
| PGX1 GB10 vLLM Qwen3.8-Flash-Next-FP4-Local (1M, :11474) | 4 | 1 | 3 | 16 | 38.2 |

## Method

- Same tasks and settings on every target; thinking at production effort.

- Objective graders only (`tasks/<id>/checks.py`), no LLM judge.

- `online_search` uses the real Tavily MCP; Blender and Playwright use real MCP sessions.

- Details, traces and screenshots are available in the shipped dashboard (`dashboard/`).

