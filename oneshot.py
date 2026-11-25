#!/usr/bin/env python3
"""One-shot bench series against an arbitrary OpenAI-compatible endpoint.

Writes an ad-hoc target spec under results/.adhoc/<slug>.json (the API key is
NEVER stored — it is read from the environment variable named by --key-env)
and runs the standard chain: runner -> reempty -> grade -> report.
The run lands in results/<slug>/ so it shows up as a column in the dashboard
and REPORT.md like any configured target.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# Engine-specific thinking-parameter presets (the body keys vLLM/Strata lanes expect)
# plus the OpenAI-standard form and a raw-JSON escape hatch.
EFFORT = {
    "xhigh": {"chat_template_kwargs": {"reasoning_effort": "xhigh"}},
    "high": {"chat_template_kwargs": {"reasoning_effort": "high"}},
    "no_think": {"chat_template_kwargs": {"reasoning_effort": "no_think"},
                 "reasoning_effort": "no_think"},
    "standard": {"reasoning_effort": "high"},
    "default": {},
    "custom": {},
}


def make_slug(label):
    base = re.sub(r"[^a-z0-9]+", "-", (label or "").lower()).strip("-")[:40]
    stamp = time.strftime("%m%d-%H%M")
    return f"{base}-{stamp}" if base else f"oneshot-{stamp}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True, help="human name for this one-shot series")
    ap.add_argument("--endpoint", required=True, help="OpenAI-compatible base URL (…/v1)")
    ap.add_argument("--model", required=True)
    ap.add_argument("--key-env", default="IPC_ONESHOT_KEY",
                    help="environment variable holding the API key")
    ap.add_argument("--effort", choices=sorted(EFFORT), default="high")
    ap.add_argument("--extra-json", default=None,
                    help="raw JSON object merged into every request body")
    ap.add_argument("--tasks", default="all", help="comma-separated task ids or 'all'")
    ap.add_argument("--runs", type=int, default=2, choices=(1, 2, 3))
    ap.add_argument("--sample-host", default=None,
                    help="optional ssh host for GPU/RAM sampling")
    ap.add_argument("--slug", default=None, help="fix the results dir name (API-provided)")
    a = ap.parse_args()

    if not re.match(r"^https?://[\w.:@/\[\]-]+$", a.endpoint):
        sys.exit("invalid endpoint (must be http(s)://…, no spaces or shell metachars)")
    if not os.environ.get(a.key_env, "").strip():
        sys.exit(f"missing API key in env {a.key_env}")
    extra = dict(EFFORT[a.effort])
    if a.extra_json:
        try:
            obj = json.loads(a.extra_json)
            assert isinstance(obj, dict)
        except Exception as e:
            sys.exit(f"--extra-json must be a JSON object: {e}")
        extra.update(obj)

    slug = a.slug or make_slug(a.label)
    if not re.match(r"^[\w.-]+$", slug):
        sys.exit("invalid slug")
    adir = os.path.join(HERE, "results", ".adhoc")
    os.makedirs(adir, exist_ok=True)
    spec = {"base": a.endpoint, "model": a.model, "key_env": a.key_env,
            "extra": extra, "sample_host": a.sample_host,
            "label": f"{a.label} — {a.model} (one-shot {time.strftime('%F %T')})",
            "ts": time.strftime("%F %T")}
    json.dump(spec, open(os.path.join(adir, slug + ".json"), "w"), indent=1)

    d = os.path.join(HERE, "results", slug)
    for sub in ("transcripts", "traces", "workspaces"):
        os.makedirs(os.path.join(d, sub), exist_ok=True)
    runner_cmd = [sys.executable, "runner.py", "--target", slug, "--runs", str(a.runs)]
    if a.tasks and a.tasks != "all":
        runner_cmd += ["--only", a.tasks]
    steps = [("runner", runner_cmd),
             ("reempty", [sys.executable, "reempty.py", "--target", slug]),
             ("grade", [sys.executable, "grade.py", "--target", slug]),
             ("report", [sys.executable, "report.py"])]
    failed = None
    for name, cmd in steps:
        with open(os.path.join(d, name + ".log"), "w") as lg:
            r = subprocess.run(cmd, cwd=HERE, stdout=lg, stderr=subprocess.STDOUT)
        if r.returncode:
            failed = name
            break

    json.dump({"slug": slug, "label": a.label, "endpoint": a.endpoint, "model": a.model,
               "effort": a.effort, "runs": a.runs, "tasks": a.tasks, "failed_step": failed,
               "ts": time.strftime("%F %T")},
              open(os.path.join(d, "oneshot.json"), "w"), indent=1)
    open(os.path.join(d, ".complete"), "w").write(
        f"RUN_COMPLETE {slug} {time.strftime('%F_%T')}\n")
    print(slug, "ONESHOT_DONE" if not failed else f"ONESHOT_FAILED@{failed}", flush=True)
    return 0 if not failed else 1


if __name__ == "__main__":
    sys.exit(main())
