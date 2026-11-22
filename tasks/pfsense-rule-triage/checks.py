import re


def grade(ctx):
    issues, checks = [], {}
    calls = ctx.get("tool_calls", [])
    reads = [c for c in calls if c.get("tool") == "read_file"]
    paths = " ".join(str(c.get("args", {}).get("path", "")) for c in reads).lower()
    for f in ("rules_snapshot.json", "filterlog_sample.txt"):
        checks["read_" + f] = f in paths
        if not checks["read_" + f]:
            issues.append(f"did not read {f}")
    answer = ctx.get("answer") or ""
    low = answer.lower()
    for key, pats in (
        ("mail_cause", (r"flottant", r"floating", r":25", r"port 25")),
        ("geo_rule", (r"geodeny", r"world", r"pflocker|pfblocker")),
        ("proxy_cause", (r"pfb_ps_v4", r"proxy.{0,20}satellite", r"alias.{0,30}vide")),
        ("fix_order", (r"recr", r"reload", r"verif", r"evaluation")),
        ("reorder_warning", (r"reorder", r"r ordonnancement")),
    ):
        checks[key] = any(re.search(p, low) for p in pats)
        if not checks[key]:
            issues.append(f"missing {key} in answer")
    checks["not_world_open"] = not re.search(r"ouvrir.{0,40}tous les ports|open everything", low)
    if not checks["not_world_open"]:
        issues.append("proposed a blanket opening of ports")
    checks["french"] = bool(re.search(r"\b(r.gle|alias|mail|proxy|v.rifier)\b", low))
    if not checks["french"]:
        issues.append("answer not in French")
    return {"pass": not issues, "issues": issues, "checks": checks, "score": sum(checks.values())}
