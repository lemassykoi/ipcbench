#!/usr/bin/env python3
"""Unified status dashboard for ipcbench and lukesdevlab.

Read side (GET) is unauthenticated for LAN humans; the write side (POST
/api/run|oneshot|abort) is a Bearer-token API so AI agents can drive benches.
Run with --cert/--key for HTTPS and --token-file for the API token.
"""
import argparse
import glob
import importlib.util
import json
import os
import re
import secrets
import signal
import ssl
import subprocess
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bridges"))
from engine import TARGETS, find_target  # noqa: E402

try:
    from runner import load_tasks  # noqa: E402
except Exception:
    load_tasks = None

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
    total = (status.get("plan_total") if status else None) or len(done) or (1 if not all_done else 1)
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


def _suite_targets(s):
    found = set(s.get("targets", []))
    rd = s.get("results")
    if rd and os.path.isdir(rd):
        for name in os.listdir(rd):
            sub = os.path.join(rd, name)
            if not os.path.isdir(sub) or name.startswith(("_", ".")):
                continue
            if any(os.path.exists(os.path.join(sub, f)) for f in
                   ("metrics.jsonl", "status.json", "GRADES.json")):
                found.add(name)
    return sorted(found)


def build_status():
    targets = []
    for suite, s in SUITES.items():
        for t in _suite_targets(s):
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
    if not s or target not in _suite_targets(s):
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


RUN_LOCK_HINTS = ("runner.py --target", "reempty.py", "oneshot.py", "run_target.sh")
TOKEN_FILE = None


def _token():
    if not TOKEN_FILE:
        return None
    try:
        return open(TOKEN_FILE).read().strip() or None
    except OSError:
        return None


def runner_busy():
    out = []
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            c = open(p, "rb").read().decode("utf-8", "replace").replace("\0", " ")
        except OSError:
            continue
        if any(h in c for h in RUN_LOCK_HINTS) and "status.py" not in c:
            out.append({"pid": int(p.split("/")[2]), "cmd": " ".join(c.split())[:140]})
    return out


def known_task_ids():
    if not load_tasks:
        return set()
    try:
        return {c["id"] for c in load_tasks()}
    except Exception:
        return set()


def joblog_append(rec):
    os.makedirs(os.path.join(ROOT, "results"), exist_ok=True)
    with open(os.path.join(ROOT, "results", ".jobs.jsonl"), "a") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def joblog_tail(n=30):
    p = os.path.join(ROOT, "results", ".jobs.jsonl")
    rows = []
    if os.path.isfile(p):
        for line in open(p, errors="replace"):
            try:
                rows.append(json.loads(line))
            except Exception:
                pass
    return rows[-n:][::-1]


def spawn(cmd, env_extra=None, tag="job"):
    e = dict(os.environ)
    e.update(env_extra or {})
    rdir = os.path.join(ROOT, "results", ".running")
    os.makedirs(rdir, exist_ok=True)
    out = open(os.path.join(rdir, tag + ".out"), "a")
    p = subprocess.Popen(cmd, cwd=ROOT, env=e, stdout=out,
                         stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    open(os.path.join(rdir, tag + ".pid"), "w").write(str(p.pid))
    return p.pid


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

    def _read_json(self):
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return None
        if n > 65536:
            return None
        if n <= 0:
            return {}
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except Exception:
            return None

    def _authorized(self):
        tok = _token()
        if not tok:
            return False
        given = (self.headers.get("Authorization") or "")
        if not given.startswith("Bearer "):
            return False
        return secrets.compare_digest(given[7:].strip(), tok)

    def do_POST(self):
        u = urlparse(self.path)
        if u.path not in ("/api/run", "/api/oneshot", "/api/abort"):
            self._send(404, b'{"error":"not found"}', "application/json")
            return
        if not _token():
            self._send(503, b'{"error":"api disabled (no token file)"}', "application/json")
            return
        if not self._authorized():
            self._send(401, b'{"error":"missing or bad bearer token"}', "application/json")
            return
        body = self._read_json()
        if body is None or not isinstance(body, dict):
            self._send(400, b'{"error":"invalid json body"}', "application/json")
            return
        try:
            code, obj = self._dispatch_post(u.path, body)
        except Exception as e:
            code, obj = 500, {"error": f"{type(e).__name__}: {str(e)[:200]}"}
        self._send(code, json.dumps(obj, ensure_ascii=False).encode(), "application/json")

    def _dispatch_post(self, path, body):
        if path == "/api/abort":
            killed = []
            for pf in glob.glob(os.path.join(ROOT, "results", ".running", "*.pid")):
                try:
                    pid = int(open(pf).read().strip())
                    os.killpg(os.getpgid(pid), signal.SIGTERM)
                    killed.append(os.path.basename(pf)[:-4])
                except Exception:
                    pass
                try:
                    os.remove(pf)
                except OSError:
                    pass
            for x in runner_busy():
                try:
                    os.killpg(os.getpgid(x["pid"]), signal.SIGTERM)
                    killed.append(str(x["pid"]))
                except Exception:
                    pass
            joblog_append({"ts": time.strftime("%F %T"), "kind": "abort", "killed": killed})
            return 200, {"ok": True, "killed": killed}

        busy = runner_busy()
        if busy:
            return 409, {"error": "a bench run is already in progress", "running": busy[:2]}

        if path == "/api/run":
            target = str(body.get("target") or "")
            if not re.match(r"^[\w.-]+$", target):
                return 400, {"error": "bad target"}
            if target not in TARGETS and find_target(target) is None:
                return 404, {"error": f"unknown target {target}"}
            cmd = ["bash", "scripts/run_target.sh", target]
            try:
                rn = int(body.get("runs", 2))
                if 1 <= rn <= 3:
                    cmd += ["--runs", str(rn)]
            except Exception:
                pass
            only = body.get("only")
            if only:
                ids = [x for x in re.split(r"[,\s]+", str(only)) if x]
                bad = [x for x in ids if x not in known_task_ids()]
                if bad:
                    return 400, {"error": f"unknown task(s): {bad}"}
                cmd += ["--only", ",".join(ids)]
            pid = spawn(cmd, tag=target)
            joblog_append({"ts": time.strftime("%F %T"), "kind": "run",
                           "target": target, "only": only, "pid": pid})
            return 202, {"ok": True, "target": target, "pid": pid}

        if path == "/api/oneshot":
            label = str(body.get("label") or "").strip()
            endpoint = str(body.get("endpoint") or "").strip()
            model = str(body.get("model") or "").strip()
            key = str(body.get("api_key") or "").strip()
            effort = str(body.get("effort") or "high")
            runs = body.get("runs", 2)
            tasks = body.get("tasks", "all")
            if not (label and endpoint and model and key):
                return 400, {"error": "label, endpoint, model, api_key are required"}
            if not re.match(r"^https?://[\w.:@/~%-]+$", endpoint):
                return 400, {"error": "bad endpoint"}
            if effort not in ("xhigh", "high", "no_think", "standard", "default", "custom"):
                return 400, {"error": "bad effort"}
            if effort == "custom" and not body.get("extra_json"):
                return 400, {"error": "effort=custom requires extra_json"}
            try:
                runs = int(runs)
                assert 1 <= runs <= 3
            except Exception:
                return 400, {"error": "runs must be 1..3"}
            if isinstance(tasks, list):
                tasks = ",".join(str(x) for x in tasks)
            tasks = str(tasks or "all").strip()
            if tasks and tasks != "all":
                ids = [x for x in re.split(r"[,\s]+", tasks) if x]
                bad = [x for x in ids if x not in known_task_ids()]
                if bad:
                    return 400, {"error": f"unknown task(s): {bad}"}
                tasks = ",".join(ids)
            else:
                tasks = "all"
            extra_json = body.get("extra_json")
            if extra_json:
                try:
                    assert isinstance(json.loads(str(extra_json)), dict)
                except Exception:
                    return 400, {"error": "extra_json must be a JSON object"}
            import oneshot as O
            slug = O.make_slug(label)
            cmd = [sys.executable, "oneshot.py", "--label", label, "--endpoint", endpoint,
                   "--model", model, "--effort", effort, "--runs", str(runs),
                   "--tasks", tasks, "--slug", slug, "--key-env", "IPC_ONESHOT_KEY"]
            if extra_json:
                cmd += ["--extra-json", str(extra_json)]
            pid = spawn(cmd, env_extra={"IPC_ONESHOT_KEY": key}, tag=slug)
            joblog_append({"ts": time.strftime("%F %T"), "kind": "oneshot", "slug": slug,
                           "label": label, "endpoint": endpoint, "model": model,
                           "effort": effort, "runs": runs, "tasks": tasks, "pid": pid})
            return 202, {"ok": True, "slug": slug, "pid": pid}
        return 404, {"error": "not found"}

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
        elif u.path == "/api/tasks":
            rows = []
            if load_tasks:
                try:
                    rows = [{"id": c["id"], "category": c.get("category"),
                             "mode": c.get("mode"), "tools": c.get("tools") or []}
                            for c in load_tasks()]
                except Exception:
                    pass
            self._send(200, json.dumps(rows).encode(), "application/json")
        elif u.path == "/api/jobs":
            self._send(200, json.dumps({"running": runner_busy(),
                                        "jobs": joblog_tail()}).encode(), "application/json")
        elif u.path == "/api/engines":
            out, seen = [], set()
            for n in sorted(TARGETS):
                out.append({"name": n, "kind": "configured"})
                seen.add(n)
            ad = os.path.join(ROOT, "results", ".adhoc")
            for f in sorted(os.listdir(ad)) if os.path.isdir(ad) else []:
                if not f.endswith(".json"):
                    continue
                n = f[:-5]
                if n in seen:
                    continue
                spec = _json_safe(os.path.join(ad, f)) or {}
                out.append({"name": n, "kind": "adhoc", "endpoint": spec.get("base"),
                            "model": spec.get("model"), "label": spec.get("label")})
                seen.add(n)
            self._send(200, json.dumps(out).encode(), "application/json")
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
    global TOKEN_FILE
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8046)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--cert", default=None, help="PEM cert for HTTPS (needs --key)")
    ap.add_argument("--key", default=None, help="PEM private key for HTTPS")
    ap.add_argument("--token-file", default=None,
                    help="file holding the Bearer token enabling the POST API")
    a = ap.parse_args()
    TOKEN_FILE = a.token_file if a.token_file and os.path.isfile(a.token_file) else None
    srv = ThreadingHTTPServer((a.bind, a.port), Handler)
    scheme = "http"
    if a.cert and a.key:
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(a.cert, a.key)
        srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
        scheme = "https"
    print(f"[ipcbench dashboard] {scheme}://{a.bind}:{a.port} "
          f"(api: {'token-auth' if TOKEN_FILE else 'disabled'})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
