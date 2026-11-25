# ipcbench

Benchmark suite for the work an agent really does for IPC (French IT services
company — virtualisation, networking, automation, local AI, Linux systems — plus
an alarm/security sub-business): French deliverables, office artifacts,
infrastructure reasoning, long-horizon agentic coding, long-context fidelity, and
**appropriate use of real tools** (online search, Blender, Playwright).

Project root: `~/bench/ipcbench`. Run everything from this box (10.0.0.134).

## Layout

- `engine.py` — target definitions, streaming chat call with tool-call support, calibration.
- `mcp_client.py` — tool backends: `online_search` (Tavily MCP), `blender`, `playwright`, sandbox.
- `bridges/blender_bridge.py` — official Blender Lab MCP bridge (copied from lukesdevlab).
- `runner.py` — runs the selected tasks for one target (`single`, `scenario`, `agent`).
- `reempty.py`, `grade.py`, `report.py` — retry empties, objective grading, markdown report.
- `tasks/<id>/` — `task.json`, `prompt.txt`, `reference.md`, `checks.py`, optional fixtures.
- `fixtures/` — shared synthetic fixtures (diagram, captcha page, logs, rules, devis data).
- `dashboard/` — status dashboard shipped with the project (fork of lukesdevlab's).
- `scripts/` — chain runner and dashboard restart helper.
- `results/`, `transcripts/`, `traces/`, `workspaces/` — generated, gitignored.

## Running a target

```
./scripts/run_target.sh pair-hy4                 # full suite, 2 runs/task
./scripts/run_target.sh pair-hy4 --only netgear-port-remap,playwright-captcha
./scripts/run_target.sh 3090                     # same tasks, 3090 lane
python3 grade.py --target pair-hy4
python3 report.py
```

Run detached (long chains):

```
setsid nohup ./scripts/run_target.sh pair-hy4 > results/pair-hy4/run.log 2>&1 < /dev/null &
```

`run_target.sh` invokes `runner.py` → `reempty.py` → `grade.py` → `report.py`.
Progress is visible in the dashboard (`dashboard/status.py`), which reads both
`ipcbench` and `lukesdevlab` results.

Preflight: Blender must already be open on the desktop before `blender-ipc-sensor`;
Playwright Chromium must be installed for `playwright-captcha`. A failed preflight is
recorded as a task error, not silently skipped.

## Adding a task

1. `mkdir tasks/<id>` and add:
   - `task.json`: `id`, `category`, `mode`, `tools`, `prompt`, `max_rounds`,
     `max_tokens`, `fixtures`, `reference`, `checks`.
   - `prompt.txt`: exact prompt shown to the model.
   - `reference.md`: gold answer and the values the grader checks.
   - `checks.py`: `grade(ctx)` returning `{"pass": bool, "issues": [...], "checks": {...}}`.
2. Put any synthetic input files in `fixtures/` and list them in `task.json`.
3. Add nothing else: runner discovers tasks by scanning `tasks/`.

`ctx` passed to `checks.grade` has `answer`, `rounds`, `files`, `metrics`, `outdir`,
`taskdir`. Keep graders dependency-free (stdlib only) and deterministic.

## Dashboard

`python3 dashboard/status.py --port 8047` for a side-by-side smoke instance.
`scripts/swap_dashboard_8046.sh` backs up the legacy `lukesdevlab/status.py` and
`status.html`, then starts the unified dashboard on port 8046. It is deliberately
not run by `run_target.sh`.

## Scoring

See `config.md`. Verdicts: both runs pass = `pass`, mixed = `partial`, both fail = `fail`.
`report.py` prints the per-task pass/fail + tok/s + turns table and a Hy4↔3090 comparison.
