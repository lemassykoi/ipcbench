#!/usr/bin/env python3
"""Build REPORT.md from GRADES.json + metrics.jsonl (per-task pass/fail, tok/s, turns)."""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import LABEL
from runner import load_tasks

TASKS = {c["id"]: c for c in load_tasks()}


def med(vals):
    vals = [v for v in vals if isinstance(v, (int, float))]
    return round(statistics.median(vals), 1) if vals else None


def load(target):
    d = os.path.join(HERE, "results", target)
    rows = {}
    p = os.path.join(d, "metrics.jsonl")
    if os.path.isfile(p):
        for line in open(p):
            try:
                r = json.loads(line)
                rows[r["test"]] = r
            except Exception:
                pass
    g = os.path.join(d, "GRADES.json")
    grades = json.load(open(g)) if os.path.isfile(g) else None
    return rows, grades


def verdict(g):
    if not g:
        return "—"
    return {"pass": "✅", "partial": "⚠️", "fail": "❌"}.get(g.get("verdict"), "—")


def main():
    targets = sorted({d for d in os.listdir(os.path.join(HERE, "results"))
                      if os.path.isfile(os.path.join(HERE, "results", d, "metrics.jsonl"))})
    L = ["# ipcbench — real-work agent benchmark",
         f"_seed 42 · temp 0 · 2 runs/task · {time.strftime('%F %H:%M')}_\n",
         "## Per-task results\n",
         "| Task | Category | " + " | ".join(LABEL.get(t, t) for t in targets) + " |",
         "|---|---|" + "---|" * len(targets)]
    data = {t: load(t) for t in targets}
    for tid, cfg in TASKS.items():
        cells = []
        for t in targets:
            rows, grades = data[t]
            g = grades["tests"].get(tid) if grades else None
            c = verdict(g)
            runs = g["runs"] if g else []
            tps = med([r["decode_tps"] for r in runs])
            turns = med([r["turns"] for r in runs])
            tools = med([r["tool_calls"] for r in runs])
            bad = any(r.get("degenerate") for r in runs)
            cells.append(f"{c} {tps or '—'} t/s · {turns or 0}r · {tools or 0} tools"
                         + (" · ⚠output" if bad else ""))
        L.append(f"| `{tid}` | {cfg['category']} | " + " | ".join(cells) + " |")
    L += ["\nLegend: ✅ both runs pass · ⚠️ mixed · ❌ both fail · ⚠output = empty/malformed output seen.",
          "\n## Summary\n",
          "| Target | Pass | Partial | Fail | Runs | Median decode tok/s |",
          "|---|---|---|---|---|---|"]
    for t in targets:
        rows, grades = data[t]
        s = grades.get("summary", {}) if grades else {}
        tps = med([r.get("decode_tps") for r in rows.values()])
        L.append(f"| {LABEL.get(t, t)} | {s.get('pass', 0)} | {s.get('partial', 0)} | "
                 f"{s.get('fail', 0)} | {len(rows)} | {tps or '—'} |")
    L += ["\n## Method\n",
          "- Same tasks and settings on every target; thinking at production effort.\n",
          "- Objective graders only (`tasks/<id>/checks.py`), no LLM judge.\n",
          "- `online_search` uses the real Tavily MCP; Blender and Playwright use real MCP sessions.\n",
          "- Details, traces and screenshots are available in the shipped dashboard (`dashboard/`).\n"]
    open(os.path.join(HERE, "REPORT.md"), "w").write("\n".join(L) + "\n")
    print("\n".join(L))


if __name__ == "__main__":
    main()
