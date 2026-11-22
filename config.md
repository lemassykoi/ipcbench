# ipcbench — configuration and scoring spec

ipcbench measures the work an agent actually does for IPC: French deliverables,
office artifacts, IT/network reasoning, long-horizon tool use, and real MCP tools
(`online_search`, Blender, Playwright). It is a fork of the proven lukesdevlab
machinery with new task content and a dashboard that is filled on every step.

## Reproducibility

- One runner process per target, sequence defined by `scripts/run_target.sh`.
- `temperature=0`, `seed=42`, `top_k=20`, `min_p=0`, `repetition_penalty=1`.
- Thinking: `chat_template_kwargs.reasoning_effort="high"` on Hy4, `reasoning_effort="high"`
  at top level on Strata. Output budget 32768 tokens (matches the daily-lane limit;
  thinking shares the budget with the answer).
- Empty/short outputs are retried once with `reasoning_effort="no_think"` and a
  `re: true` marker in `metrics.jsonl` (never silently dropped).
- `calib.json` measures tokens-per-filler for the target tokenizer so injected
  long-context corpora hit their advertised token count.
- Each task runs twice (`_r1`, `_r2`). `GRADES.json` verdict: `pass` = both runs pass,
  `fail` = both fail, `partial` = mixed.

## Modes

| Mode | What the model gets | What is recorded |
|---|---|---|
| `single` | one system + one user message (optionally a large injected corpus) | answer text, TTFT, tok/s, tokens |
| `scenario` | multi-turn tool calling with deterministic local tool executors | every tool call + args + result, turns, recovery |
| `agent` | sandbox (`write_file`, `read_file`, `list_dir`, `exec`) plus task tools, up to `max_rounds` | full trajectory, `_live.json` for the dashboard, artifacts, in-loop check feedback |

## Objective grading (no LLM judge)

Every task directory contains:

- `task.json` — id, category, mode, tool set, `max_rounds`, `max_tokens`, fixture list.
- `prompt.txt` — the exact user prompt.
- `reference.md` — the gold answer / expected values.
- `checks.py` — `grade(ctx) -> dict` with `pass`, `issues`, `checks`, optional `score`.

`ctx` exposes `outdir`, `taskdir`, `answer` (final text), `rounds` (trace), `files`
(workspace files), `metrics` (per-run metrics). Graders are pure functions; no
network access, no model call. Specific checks are documented in each `reference.md`.

Scoring dimensions, mapped to task categories:

1. **Applied + verified**: deliverable exists, parses, and passes the harness' own
   verification (render, contract, tests); no destructive side-effect outside the workspace.
2. **French deliverables**: required fields, exact amounts, correct French (accents,
   formal tone), completeness.
3. **Infra reasoning**: reference diff / defect IDs, correct root cause, fix order.
4. **Domain facts**: specific correct values from the reference.
5. **Tool discipline**: correct tool chosen, restraint when no tool is needed, focus,
   recovery when a tool returns an error.
6. **Long-context fidelity**: exact buried fact, not a nearby distractor.

## Tool backends

- `online_search`: real Tavily MCP over HTTP; each response is snapshotted into the
  trace. Grading checks the *decision to search*, query quality and the final fact —
  not a brittle verbatim snippet (the web changes).
- `blender`: official Blender Lab MCP bridge (existing `blender_bridge.py`). Blender
  must already be running on the desktop before a run; a preflight probe fails fast.
  Scene is reset before each run; `.blend` and renders are saved under the run dir.
- `playwright`: `playwright-mcp --browser chromium` over stdio. The captcha task uses a
  local challenge page; screenshots and the extracted token are graded.
- Sandbox file/exec tools are confined to the run workspace by `safe_join`.

## Result layout

```
results/<target>/
  status.json          # written after every step (dashboard heartbeat)
  metrics.jsonl        # one row per run (append-only; re-runs are marked)
  GRADES.json          # produced by grade.py
  resources.json       # GPU/RAM samples
  transcripts/<task>_rN.out.txt
  traces/<task>_rN.json
  workspaces/<task>_rN/   # agent artifacts, screenshots, .blend
  *.log
```

## Safety

- No live engine, service or config is ever stopped or edited by the bench. Key
  retrieval for the 3090 is a read-only ssh `cat`.
- Fixtures are synthetic and anonymized; this project is public. No API keys,
  client names, internal hostnames or tokens are committed.
- Generated artifacts stay under `results/` / `workspaces/` (gitignored).
