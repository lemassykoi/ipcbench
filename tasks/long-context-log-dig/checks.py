import re


def grade(ctx):
    issues, checks = [], {}
    answer = ctx.get("answer") or ""
    checks["ip"] = "198.51.100.73" in answer
    if not checks["ip"]:
        issues.append("wrong or missing IP (expected 198.51.100.73)")
    m = re.search(r"PAYS\s*[=:]\s*([A-Za-z]{2})", answer)
    checks["country"] = bool(m and m.group(1).upper() == "FR")
    if not checks["country"]:
        issues.append("wrong or missing country code (expected FR)")
    checks["not_empty"] = len(answer.strip()) >= 20
    if not checks["not_empty"]:
        issues.append("empty/short answer")
    return {"pass": not issues, "issues": issues, "checks": checks, "score": sum(checks.values())}
