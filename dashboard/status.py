#!/usr/bin/env python3
"""Unified status dashboard for ipcbench and lukesdevlab (all read-only)."""
import argparse
import glob
import importlib.util
import json
import os
import re
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bridges"))
from engine import TARGETS  # noqa: E402

TAIL_WHITELIST = re.compile(r"^[\w./-]+\.(log|nohup|txt|jsonl|json)$")
RUNNING_HINTS = ("runner.py", "agentrun.py", "agency.py", "reempty.py", "grade.py", "status.py")
SUITES = {
    "ipcbench": {"dir": ROOT, "results": os.path.join(ROOT, "results"),
                 "targets": list(TARGETS)},
}
try:
    legacy = os.path.join(os.path.dirname(ROOT), "lukesdevlab")
    spec = importlib.util.spec_from_file_location("legacy_runner", os.path.join(legacy, "runner.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    SUITES["lukesdevlab"] = {"dir": legacy, "results": os.path.join(legacy, "results"),
                              "targets": list(m.TARGETS)}
except Exception:
    pass


def _mtime(p):
    try:
        return os.path.getmtime(p)
    except OSError:
        return None


def _json_safe(p):
    try:
        with open(p) as f:
            return json.load(f)
    except Exception:
        return None


def _tail(path, n=40):
    try:
        with open(path, errors="replace") as f:
            return f.read().splitlines()[-n:]
    except OSError:
        return []


def _rows(d):
    out = []
    p = os.path.join(d, "metrics.jsonl")
    if os.path.isfile(p):
        for line in open(p, errors="replace"):
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _ipc_tasks(d, grades, rows):
    out = {}
    for name, g in (grades or {}).get("tests", {}).items():
        rr = [r for r in rows if r.get("task") == name and not r.get("error")]
        out[name] = {"verdict": g.get("verdict"),
                     "runs": len(g.get("runs", [])),
                     "decode_tps": next((r.get("decode_tps") for r in reversed(rr)
                                         if r.get("decode_tps")), None),
                     "turns": next((r.get("turns") for r in reversed(rr)), None),
                     "tools": next((r.get("tool_calls") for r in reversed(rr)), None),
                     "issues": [x for r in g.get("runs", []) for x in r.get("issues", [])][:3]}
    return out


def target_state(suite, target):
    s = SUITES[suite]
    d = os.path.join(s["results"], target)
    st = {"suite": suite, "target": target, "exists": os.path.isdir(d),
          "stage_done": {}, "current": None, "progress": None, "eta_s": None,
          "errors": [], "last_error": None, "recent": [], "last_tps": None,
          "verdicts": None, "summary": None, "agency": None, "gpu": None, "ram": None,
          "tasks": {}, "live": [], "heartbeat_age_s": None, "recency_ts": None}
    if not st["exists"]:
        return st
    status = _json_safe(os.path.join(d, "status.json"))
    rows = _rows(d)
    done = status.get("done", []) if status else []
    cur = status.get("current") if status else None
    all_done = bool(done) and cur == "ALL DONE"
    total = status.get("plan_total") or len(done) or (1 if not all_done else 1)
    st["current"] = cur
    st["progress"] = {"done": len(done), "total": total, "all_done": all_done}
    hb = _mtime(os.path.join(d, "status.json"))
    if hb:
        st["heartbeat_age_s"] = round(time.time() - hb)
    errs = [r for r in rows if r.get("error")]
    st["errors"] = [{"test": r.get("test"), "error": r["error"][:200]} for r in errs[-10:]]
    st["last_error"] = st["errors"][-1] if st["errors"] else None
    st["recent"] = [{"test": r.get("test"), "kind": r.get("kind"), "s": r.get("total_s"),
                     "decode_tps": r.get("decode_tps"), "ctok": r.get("completion_tokens"),
                     "error": (r.get("error") or "")[:80] or None, "ts": r.get("ts")}
                    for r in rows[-10:]]
    tps = [r["decode_tps"] for r in rows if r.get("decode_tps")]
    st["last_tps"] = tps[-1] if tps else None
    grades = _json_safe(os.path.join(d, "GRADES.json"))
    if grades:
        st["verdicts"] = {k: v.get("verdict") for k, v in grades.get("tests", {}).items()}
        st["summary"] = grades.get("summary")
        if suite == "ipcbench":
            st["tasks"] = _ipc_tasks(d, grades, rows)
    agency = _json_safe(os.path.join(d, "agency.json"))
    if agency:
        st["agency"] = {"score": agency.get("summary"),
                        "fails": [x["prompt"][:60] for x in agency.get("scenarios", [])
                                  if not x.get("pass")][:5]}
    res = _json_safe(os.path.join(d, "resources.json")) or []
    if res:
        gpu = [x.get("gpu_util") for x in res if x.get("gpu_util") is not None]
        vram = [x.get("vram_mib") for x in res if x.get("vram_mib") is not None]
        ram = [x.get("ram_used_mib") for x in res if x.get("ram_used_mib") is not None]
        st["gpu"] = {"last": gpu[-1] if gpu else None,
                     "peak_vram_mib": max(vram) if vram else None, "series": gpu[-60:]}
        st["ram"] = {"peak_mib": max(ram) if ram else None, "series": ram[-60:]}
    if suite == "ipcbench":
        wsroot = os.path.join(d, "workspaces")
        for w in sorted(os.listdir(wsroot)) if os.path.isdir(wsroot) else []:
            p = os.path.join(wsroot, w, "_live.json")
            live = _json_safe(p)
            if live:
                live["name"] = w
                st["live"].append(live)
        st["stage_done"] = {"runner": all_done, "reempty": any(
            os.path.exists(os.path.join(d, f)) for f in ("reempty.log", "reempty2.log")),
            "grade": os.path.isfile(os.path.join(d, "GRADES.json")),
            "report": os.path.isfile(os.path.join(d, "report.log"))}
    else:
        st["stage_done"] = {"runner": all_done, "reempty": os.path.isfile(os.path.join(d, "reempty.log")),
                            "grade": os.path.isfile(os.path.join(d, "GRADES.json")),
                            "agency": os.path.isfile(os.path.join(d, "agency.json")),
                            "battery": os.path.isdir(os.path.join(s["dir"], "agentwork", target))}
    refs = [_mtime(p) for p in (os.path.join(d, "status.json"), os.path.join(d, "metrics.jsonl"))]
    st["recency_ts"] = max([x for x in refs if x], default=None)
    return st


def _probe_blender():
    try:
        return importlib.util.find_spec("blender_bridge") and __import__("blender_bridge").BlenderBridge.probe()
    except Exception as e:
        return {"reachable": False, "error": str(e)[:120]}


def _probe_playwright():
    found = []
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            c = open(p, "rb").read().decode("utf-8", "replace")
        except OSError:
            continue
        if "playwright-mcp" in c:
            found.append(int(p.split("/")[2]))
    return {"reachable": bool(found), "pids": found[:5]}


def build_status():
    targets = []
    for suite, s in SUITES.items():
        for t in s["targets"]:
            targets.append(target_state(suite, t))
    targets.sort(key=lambda x: x.get("recency_ts") or 0, reverse=True)
    procs = []
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            c = open(p, "rb").read().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        if any(h in c for h in RUNNING_HINTS) and "status.py" not in c:
            procs.append({"pid": int(p.split("/")[2]), "cmd": " ".join(c.split())[:160]})
    logs = {}
    for base in (ROOT, SUITES["lukesdevlab"]["dir"] if "lukesdevlab" in SUITES else ROOT):
        for f in glob.glob(os.path.join(base, "*.log")) + glob.glob(os.path.join(base, "results", "*", "*.log")):
            mt = _mtime(f)
            if mt and time.time() - mt < 86400 * 3:
                rel = os.path.relpath(f, ROOT)
                logs[rel] = {"mtime": mt, "age_s": round(time.time() - mt)}
    return {"ts": time.time(), "ts_str": time.strftime("%F %T"),
            "blender": _probe_blender(), "playwright": _probe_playwright(),
            "targets": targets, "logs": logs, "procs": procs}


def detail_state(suite, target, test=None):
    s = SUITES.get(suite)
    if not s or target not in s["targets"]:
        return {"error": f"unknown target {suite}/{target}"}
    d = os.path.join(s["results"], target)
    out = {"suite": suite, "target": target, "test": test, "kind": "target"}
    if not test:
        out["state"] = target_state(suite, target)
        return out
    grades = _json_safe(os.path.join(d, "GRADES.json")) or {}
    g = grades.get("tests", {}).get(test)
    rows = [r for r in _rows(d) if r.get("task") == test or r.get("test", "").startswith(test + "_r")]
    keep = ("test", "total_s", "ttft_s", "decode_tps", "prefill_tps", "prefill_tps_est",
            "completion_tokens", "prompt_tokens", "content_chars", "reasoning_chars",
            "turns", "tool_calls", "error", "ts", "re", "degenerate")
    out["metrics"] = [{k: r.get(k) for k in keep} for r in rows]
    out["runs"] = g.get("runs", []) if g else []
    ws = os.path.join(d, "workspaces")
    if os.path.isdir(ws):
        for name in sorted(os.listdir(ws)):
            if not name.startswith(test + "_r"):
                continue
            tp = os.path.join(d, "traces", name + ".json")
            tr = _json_safe(tp) or {}
            wsd = os.path.join(ws, name)
            shots = []
            for root, dirs, fs in os.walk(wsd):
                for f in fs:
                    if f.endswith(".png"):
                        shots.append(os.path.relpath(os.path.join(root, f), ROOT))
            out["kind"] = "ipcbench"
            out.setdefault("runs_trace", []).append({"name": name, "trace": tr,
                                                      "shots": shots[:20],
                                                      "files": [os.path.relpath(os.path.join(r, f), wsd)
                                                                for r, ds, fs in os.walk(wsd)
                                                                for f in fs][:60]})
    if out.get("runs") or out.get("runs_trace"):
        out["kind"] = "ipcbench"
    return out


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _guard(self, rel):
        real = os.path.realpath(os.path.join(ROOT, rel))
        if not real.startswith(os.path.realpath(ROOT) + os.sep):
            self._send(403, b"forbidden", "text/plain")
            return None
        return real

    def do_GET(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if u.path == "/":
            self._send(200, open(os.path.join(HERE, "status.html"), "rb").read(),
                       "text/html; charset=utf-8")
        elif u.path == "/api/status.json":
            self._send(200, json.dumps(build_status()).encode(), "application/json")
        elif u.path == "/api/blender":
            self._send(200, json.dumps(_probe_blender()).encode(), "application/json")
        elif u.path == "/api/detail":
            suite = (q.get("suite") or ["ipcbench"])[0]
            target = (q.get("target") or [""])[0]
            test = (q.get("test") or [None])[0]
            if test and not re.match(r"^[\w-]+$", test):
                self._send(403, b"forbidden", "text/plain")
                return
            self._send(200, json.dumps(detail_state(suite, target, test)).encode(), "application/json")
        elif u.path == "/api/tail":
            rel = (q.get("file") or [""])[0]
            if not TAIL_WHITELIST.match(rel):
                self._send(403, b"forbidden", "text/plain")
                return
            real = self._guard(rel)
            if real:
                n = min(int((q.get("n") or ["40"])[0]), 400)
                self._send(200, json.dumps({"file": rel, "lines": _tail(real, n)}).encode(),
                           "application/json")
        elif u.path == "/media":
            rel = (q.get("file") or [""])[0]
            real = self._guard(rel)
            if not real:
                return
            ctype = {".png": "image/png", ".jpg": "image/jpeg", ".html": "text/html",
                     ".txt": "text/plain", ".json": "application/json", ".glb": "model/gltf-binary"}
            ext = os.path.splitext(real)[1]
            if ext not in ctype or not os.path.isfile(real):
                self._send(404, b"not found", "text/plain")
                return
            self._send(200, open(real, "rb").read(), ctype[ext])
        else:
            self._send(404, b"not found", "text/plain")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8046)
    ap.add_argument("--bind", default="0.0.0.0")
    a = ap.parse_args()
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    print(f"[ipcbench dashboard] http://{a.bind}:{a.port}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
