#!/usr/bin/env python3
"""Objective grading: reads metrics/traces/workspaces, writes GRADES.json."""
import argparse
import importlib.util
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from runner import load_tasks


def files_in(ws):
    out = []
    for root, dirs, fs in os.walk(ws):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in fs:
            out.append(os.path.relpath(os.path.join(root, f), ws))
    return sorted(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True)
    a = ap.parse_args()
    outdir = os.path.join(HERE, "results", a.target)
    tasks = {c["id"]: c for c in load_tasks()}
    rows = {}
    p = os.path.join(outdir, "metrics.jsonl")
    if os.path.isfile(p):
        for line in open(p):
            try:
                r = json.loads(line)
                rows[r["test"]] = r
            except Exception:
                pass
    grades = {"target": a.target, "suite": "ipcbench", "tests": {}}
    for tid, cfg in tasks.items():
        checks_p = os.path.join(cfg["dir"], cfg.get("checks", "checks.py"))
        spec = importlib.util.spec_from_file_location("grade_" + tid.replace("-", "_"), checks_p)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        runs = []
        for r in sorted(k for k in rows if k.startswith(tid + "_r")):
            rec = rows[r]
            tp = os.path.join(outdir, "traces", r + ".json")
            trace = json.load(open(tp)) if os.path.isfile(tp) else {}
            answer_file = os.path.join(outdir, "transcripts", r + ".out.txt")
            answer = open(answer_file, errors="replace").read() if os.path.isfile(answer_file) else ""
            ws = os.path.join(outdir, "workspaces", r)
            final_md = os.path.join(ws, "FINAL.md")
            used_final_md = False
            if not answer.strip() and os.path.isfile(final_md):
                answer = open(final_md, errors="replace").read()
                used_final_md = True
            ctx = {"outdir": ws, "taskdir": cfg["dir"], "answer": answer,
                   "answers": trace.get("answers", [answer]),
                   "rounds": trace.get("rounds", []),
                   "tool_calls": trace.get("tool_calls", []),
                   "files": files_in(ws) if os.path.isdir(ws) else [],
                   "metrics": rec, "config": cfg}
            if rec.get("error") or trace.get("error"):
                g = {"pass": False, "issues": [rec.get("error") or trace.get("error")],
                     "checks": {}}
            else:
                try:
                    g = mod.grade(ctx)
                except Exception as e:
                    g = {"pass": False, "issues": [f"grader error: {e}"], "checks": {}}
            runs.append({"run": r, "pass": g.get("pass"), "issues": g.get("issues", []),
                         "checks": g.get("checks", {}), "re": bool(rec.get("re")),
                         "degenerate": bool(rec.get("degenerate")) and not used_final_md,
                         "decode_tps": rec.get("decode_tps"), "turns": rec.get("turns"),
                         "tool_calls": rec.get("tool_calls")})
        if not runs:
            continue
        vals = [x["pass"] for x in runs]
        verdict = "pass" if all(v is True for v in vals) else (
            "fail" if all(v is False for v in vals) else "partial")
        grades["tests"][tid] = {"runs": runs, "verdict": verdict}
    summary = {"pass": 0, "partial": 0, "fail": 0}
    for g in grades["tests"].values():
        summary[g["verdict"]] += 1
    grades["summary"] = summary
    json.dump(grades, open(os.path.join(outdir, "GRADES.json"), "w"), indent=1, ensure_ascii=False)
    print(a.target, summary)


if __name__ == "__main__":
    main()
