import os
import re
import subprocess


def grade(ctx):
    issues, checks = [], {}
    ws = ctx["outdir"]
    p = os.path.join(ws, "diagram.html")
    txt = open(p, errors="replace").read() if os.path.isfile(p) else ""
    compact = re.sub(r"\s+", "", txt)
    want = [
        ("uplink_s3", r"fromPort:['\"]sfp-3['\"]"),
        ("uplink_p7", r"toPort:['\"]ng-7['\"]"),
        ("r720_p3", r"fromPort:['\"]ng-3['\"]"),
        ("dmz_r720", r"fromPort:['\"]dell720-p4['\"]"),
        ("dmz_pfs_p2", r"toPort:['\"]pfs-p2['\"]"),
    ]
    for name, pat in want:
        checks[name] = bool(re.search(pat, compact))
        if not checks[name]:
            issues.append(f"missing {name}: {pat}")
    checks["no_ng8_used"] = "fromPort:'ng-8'" not in compact and 'fromPort:"ng-8"' not in compact
    if not checks["no_ng8_used"]:
        issues.append("ng-8 still used as a connection source")
    dmz = re.findall(r"\{[^{}]*dell730-p4[^{}]*\}", compact)
    checks["dmz_r730_purple"] = any("#aa44ff" in x.lower() for x in dmz)
    if not checks["dmz_r730_purple"]:
        issues.append("R730 DMZ link is not purple")
    checks["verify_file"] = os.path.isfile(os.path.join(ws, "VERIFY.md"))
    if not checks["verify_file"]:
        issues.append("VERIFY.md missing")
    dom = ""
    try:
        r = subprocess.run(["brave-browser", "--headless=new", "--disable-gpu", "--no-sandbox",
                            "--virtual-time-budget=5000", "--dump-dom", f"file://{p}"],
                           capture_output=True, text=True, timeout=45)
        dom = r.stdout
    except Exception as e:
        issues.append(f"headless render failed: {e}")
    paths = dom.count("<path")
    checks["render_paths"] = paths
    if paths < 4:
        issues.append(f"only {paths} <path> elements in rendered DOM (need >=4)")
    calls = ctx.get("tool_calls", [])
    checks["used_exec"] = any(c.get("tool") == "exec" for c in calls)
    if not checks["used_exec"]:
        issues.append("never used exec to verify the render")
    return {"pass": not issues, "issues": issues, "checks": checks, "score": sum(checks.values())}
