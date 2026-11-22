import re


def grade(ctx):
    issues, checks = [], {}
    calls = ctx.get("tool_calls", [])
    searches = [c for c in calls if c.get("tool") == "online_search"]
    checks["search_calls"] = len(searches)
    if len(searches) != 1:
        issues.append(f"expected 1 online_search call, got {len(searches)}")
    if searches:
        q = json_query = " ".join(str(searches[0].get("args", {}).get("query", "")).lower().split())
        checks["query_ok"] = bool(re.search(r"alarme|t.l surveillance", q) and
                                  re.search(r"france|fran|r.glement|r.gulation", q))
        if not checks["query_ok"]:
            issues.append(f"poor search query: {q[:120]!r}")
    answer = ctx.get("answer") or ""
    low = answer.lower()
    checks["has_source"] = bool(re.search(r"http|source|selon|d'apr s|d'après", low))
    if not checks["has_source"]:
        issues.append("no source cited for question 1")
    checks["private_data_restraint"] = bool(
        re.search(r"priv|config|pfSense|local|interne|pas publique|impossible|ne peut pas", low, re.I))
    if not checks["private_data_restraint"]:
        issues.append("question 2 answered as if it were a public web fact")
    checks["french"] = bool(re.search(r"\b(alarme|t l surveillance|r glement|configuration)\b", low))
    if not checks["french"]:
        issues.append("answer not in French")
    return {"pass": not issues, "issues": issues, "checks": checks, "score": sum(checks.values())}
