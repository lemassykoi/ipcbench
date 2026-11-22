import os
import re

TOKEN = "IPC-OK-7391"


def grade(ctx):
    issues, checks = [], {}
    ws = ctx["outdir"]
    p = os.path.join(ws, "result.txt")
    txt = open(p, errors="replace").read() if os.path.isfile(p) else ""
    checks["result_file"] = TOKEN in txt and "TOKEN=" in txt
    if not checks["result_file"]:
        issues.append("result.txt does not contain TOKEN=IPC-OK-7391")
    answer = ctx.get("answer") or ""
    checks["token_in_answer"] = TOKEN in answer
    if not checks["token_in_answer"]:
        issues.append("token missing from final answer")
    calls = ctx.get("tool_calls", [])
    browser = [c for c in calls if c.get("tool", "").startswith("browser_")]
    names = [c.get("tool") for c in browser]
    checks["browser_calls"] = len(browser)
    if len(browser) < 4:
        issues.append(f"only {len(browser)} browser calls (need >=4)")
    for want in ("browser_navigate", "browser_type", "browser_click"):
        checks["call_" + want] = want in names
        if want not in names:
            issues.append(f"missing {want} call")
    shots = [f for f in ctx.get("files", []) if f.startswith("_shots/") and f.endswith(".png")]
    checks["screenshots"] = len(shots)
    if len(shots) < 2:
        issues.append(f"only {len(shots)} screenshots (need >=2)")
    errors = [c for c in calls if c.get("error")]
    checks["tool_errors"] = len(errors)
    return {"pass": not issues, "issues": issues, "checks": checks, "score": sum(checks.values())}
