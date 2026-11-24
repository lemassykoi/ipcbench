#!/usr/bin/env python3
"""Retry empty/short single-mode outputs with reasoning_effort=no_think."""
import argparse
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import TARGETS, get_key, call_with_retry, metric_row, NO_THINK
from runner import load_tasks, load_prompt, SYSTEM

ap = argparse.ArgumentParser()
ap.add_argument("--target", required=True, choices=sorted(TARGETS))
a = ap.parse_args()
t = dict(TARGETS[a.target])
t["_name"] = a.target
key = get_key(t)
outdir = os.path.join(HERE, "results", a.target)
tasks = {c["id"]: c for c in load_tasks()}
rows = [json.loads(l) for l in open(os.path.join(outdir, "metrics.jsonl"))]
last = {}
for r in rows:
    last[r["test"]] = r
need = [k for k, r in last.items()
        if not r.get("error") and not r.get("re") and r.get("kind") == "single"
        and (r.get("content_chars", 0) == 0 or r.get("content_chars", 0) < 20)]
print(a.target, "reempty", len(need), flush=True)
for name in need:
    tid = last[name].get("task") or name.rsplit("_r", 1)[0]
    cfg = tasks.get(tid)
    if not cfg:
        continue
    extra = dict(t.get("extra") or {})
    base_extra = dict(t.get("extra") or {})
    msgs = [{"role": "system", "content": SYSTEM["single"]},
            {"role": "user", "content": load_prompt(cfg)}]
    # PGX1/gb10 vLLM Qwen reject reasoning_effort no_think/none, so try no_think
    # first (lukesdevlab behaviour) and fall back to the lane's normal reasoning.
    rec = {}
    for attempt_extra, mode in (({**base_extra, **NO_THINK}, "no_think"), (base_extra, "reasoning")):
        try:
            res = call_with_retry(t["base"], key, t["model"], msgs, cfg["max_tokens"],
                                  extra=attempt_extra, retries=0)
            rec = metric_row(name, cfg["mode"], res)
            rec["test"] = name
            rec["task"] = tid
            rec["re"] = True
            rec["re_mode"] = mode
            rec["turns"] = 1
            rec["tool_calls"] = 0
            answer = res["content"]
            trace = {"target": a.target, "test": tid, "mode": "single", "re": True,
                     "re_mode": mode, "rounds": [{"round": 0, "content": answer[:1200]}],
                     "answers": [answer], "tool_calls": []}
            json.dump(trace, open(os.path.join(outdir, "traces", name + ".json"), "w"),
                      indent=1, ensure_ascii=False)
            open(os.path.join(outdir, "transcripts", name + ".out.txt"), "w").write(answer)
            break
        except Exception as e:
            rec = {"test": name, "task": tid, "re": True, "re_mode": mode, "error": str(e)[:200]}
    rec["ts"] = time.strftime("%F %T")
    with open(os.path.join(outdir, "metrics.jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(a.target, name, "re-run", rec.get("content_chars"), flush=True)
print(a.target, "REEMPTY_DONE", flush=True)
