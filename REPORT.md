# ipcbench — real-work agent benchmark
_seed 42 · temp 0 · 2 runs/task · 2026-11-24 14:05_

## Per-task results

| Task | Category | 3090 Strata Qwen3.8-Flash-Next IQ4_XS (1M) | PGX1 GB10 vLLM Qwen3.8-Flash-Next-FP4-Local (1M, :11474) |
|---|---|---|---|
| `blender-ipc-sensor` | complex-tool-use | ⚠️ 83.8 t/s · 29.0r · 34.0 tools | ⚠️ 40.7 t/s · 29.0r · 32.0 tools |
| `fr-devis-docx` | french-business | ✅ 78.3 t/s · 17.5r · 22.0 tools | ✅ 39.1 t/s · 16.0r · 20.5 tools |
| `long-context-log-dig` | long-context-fidelity | ✅ 90.0 t/s · 1.0r · 0 tools | ✅ 51.3 t/s · 1.0r · 0 tools |
| `netgear-port-remap` | long-agentic-coding | ✅ 83.9 t/s · 14.5r · 16.5 tools | ✅ 44.2 t/s · 12.0r · 12.5 tools |
| `online-veille-reglementaire` | simple-tool-use | ✅ 71.9 t/s · 2.0r · 1.0 tools | ✅ 37.2 t/s · 2.0r · 1.0 tools |
| `pfsense-rule-triage` | infra-reasoning | ✅ 86.7 t/s · 2.0r · 2.0 tools | ⚠️ 38.1 t/s · 2.0r · 2.0 tools |
| `playwright-captcha` | hard-browser-use | ❌ 58.4 t/s · 19.0r · 18.0 tools | ✅ 26.4 t/s · 19.0r · 22.5 tools |
| `tool-discipline-ipc` | tool-discipline | ✅ 67.3 t/s · 7.0r · 3.5 tools | ✅ 37.3 t/s · 7.0r · 3.0 tools |

Legend: ✅ both runs pass · ⚠️ mixed · ❌ both fail · ⚠output = empty/malformed output seen.

## Summary

| Target | Pass | Partial | Fail | Runs | Median decode tok/s |
|---|---|---|---|---|---|
| 3090 Strata Qwen3.8-Flash-Next IQ4_XS (1M) | 6 | 1 | 1 | 16 | 80.6 |
| PGX1 GB10 vLLM Qwen3.8-Flash-Next-FP4-Local (1M, :11474) | 6 | 2 | 0 | 16 | 38.4 |

## Method

- Same tasks and settings on every target; thinking at production effort.

- Objective graders only (`tasks/<id>/checks.py`), no LLM judge.

- `online_search` uses the real Tavily MCP; Blender and Playwright use real MCP sessions.

- Details, traces and screenshots are available in the shipped dashboard (`dashboard/`).

