import os
import re
import zipfile
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
AMOUNTS = ["1120.00", "224.00", "1344.00"]


def norm(s):
    s = s.replace("\u00a0", " ").replace("\u202f", " ")
    s = s.replace(" ", "").replace(",", ".")
    return s.lower()


def grade(ctx):
    issues, checks = [], {}
    ws = ctx["outdir"]
    docx = os.path.join(ws, "devis.docx")
    txt = ""
    checks["docx_exists"] = os.path.isfile(docx)
    if not checks["docx_exists"]:
        issues.append("devis.docx missing")
    else:
        try:
            with zipfile.ZipFile(docx) as z:
                x = z.read("word/document.xml")
            root = ET.fromstring(x)
            txt = "\n".join("".join(t.text or "" for t in p.iter(NS + "t"))
                            for p in root.iter(NS + "p"))
        except Exception as e:
            issues.append(f"docx unreadable: {e}")
    n = norm(txt)
    for key, pat in (("number", "d-2026-test-014"), ("client", "sarlateliertest"),
                     ("central", "centrale"), ("detector", "détecteur"),
                     ("install", "installation"), ("validity", "30jours")):
        checks["docx_" + key] = norm(pat) in n
        if not checks["docx_" + key]:
            issues.append(f"docx lacks {key} ({pat})")
    for a in AMOUNTS:
        checks["docx_amount_" + a] = a.replace(".", "") in n or a in n
        if not checks["docx_amount_" + a]:
            issues.append(f"docx lacks amount {a}")
    plain = os.path.join(ws, "devis.txt")
    ptxt = open(plain, errors="replace").read() if os.path.isfile(plain) else ""
    checks["devis_txt"] = bool(ptxt.strip()) and all(a in norm(ptxt) for a in AMOUNTS)
    if not checks["devis_txt"]:
        issues.append("devis.txt missing or amounts wrong")
    calls = ctx.get("tool_calls", [])
    checks["ran_python"] = any(c.get("tool") == "exec" and "generate.py" in
                               str(c.get("args", {}).get("cmd", "")) for c in calls)
    if not checks["ran_python"]:
        issues.append("did not execute generate.py with exec")
    answer = ctx.get("answer") or ""
    checks["french_answer"] = bool(re.search(r"\b(devis|ttc|tva|client)\b", answer.lower()))
    if not checks["french_answer"]:
        issues.append("final answer not in French")
    return {"pass": not issues, "issues": issues, "checks": checks, "score": sum(checks.values())}
