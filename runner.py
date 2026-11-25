#!/usr/bin/env python3
"""ipcbench runner: one target at a time, tasks discovered from tasks/."""
import argparse
import functools
import importlib.util
import json
import os
import re
import shutil
import sys
import threading
import time
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from engine import (TARGETS, find_target, get_key, call_with_retry, metric_row,
                    calibrate, Sampler, OUTPUT_TOKENS, NO_THINK)
from mcp_client import ToolBox

SYSTEM = {
    "single": ("Tu es un assistant méticuleux travaillant pour IPC, société de services "
               "informatiques en France : virtualisation (Proxmox), réseaux (10G, VLAN, "
               "pfSense), automatisation (Python, Bash, Ansible), IA locale (llama.cpp, "
               "vLLM) et systèmes Linux (Debian). L'activité alarme/sécurité est "
               "secondaire. Réponds en français, de façon précise et complète. "
               "Suis exactement le format demandé."),
    "scenario": ("Tu travailles pour IPC. Utilise les outils fournis uniquement quand ils sont "
                 "nécessaires, réponds en français, et donne ta réponse finale sans appels d'outil "
                 "supplémentaires dès que tu as assez d'informations."),
    "agent": ("Tu es un agent autonome travaillant dans ton espace de travail. Utilise les outils "
              "pour produire les livrables, puis VÉRIFIE-les réellement (exécution, rendu, inspection). "
              "Corrige les erreurs que tu vois au lieu de les ignorer. Ne pose pas de questions et ne "
              "réponds pas par du texte seul tant que les livrables ne sont pas présents. "
              "Écris un FINAL.md ou une réponse finale concise une fois terminé."),
}


def load_tasks(only=None):
    out = []
    for d in sorted(os.listdir(os.path.join(HERE, "tasks"))):
        td = os.path.join(HERE, "tasks", d)
        p = os.path.join(td, "task.json")
        if not os.path.isfile(p):
            continue
        cfg = json.load(open(p))
        if only and cfg["id"] not in only:
            continue
        cfg["dir"] = td
        out.append(cfg)
    return out


def load_prompt(cfg):
    text = open(os.path.join(cfg["dir"], cfg.get("prompt", "prompt.txt")), errors="replace").read()
    inj = cfg.get("inject")
    if inj:
        p = os.path.join(HERE, inj["fixture"])
        if os.path.isfile(p):
            text += "\n\n" + inj.get("label", "CORPUS:") + "\n" + open(p, errors="replace").read()
    return text


def load_checks(cfg):
    p = os.path.join(cfg["dir"], cfg.get("checks", "checks.py"))
    spec = importlib.util.spec_from_file_location("checks_" + cfg["id"].replace("-", "_"), p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def strip_images(msgs):
    for m in msgs:
        if m.get("role") == "tool" and isinstance(m.get("content"), list):
            m["content"] = [p for p in m["content"] if p.get("type") == "text"] or \
                           [{"type": "text", "text": "(screenshot unavailable)"}]


def chat(t, key, msgs, max_tokens, tools=None, extra=None):
    try:
        return call_with_retry(t["base"], key, t["model"], msgs, max_tokens,
                               tools=tools, extra=extra)
    except Exception as e:
        if any(isinstance(m.get("content"), list) for m in msgs if m.get("role") == "tool"):
            strip_images(msgs)
            return call_with_retry(t["base"], key, t["model"], msgs, max_tokens,
                                   tools=tools, extra=extra)
        raise


def prepare_ws(outdir, cfg, name):
    ws = os.path.join(outdir, "workspaces", name)
    os.makedirs(ws, exist_ok=True)
    for src, dst in cfg.get("fixtures", []):
        s = os.path.join(HERE, src)
        d = os.path.join(ws, dst)
        if os.path.isfile(s):
            os.makedirs(os.path.dirname(d), exist_ok=True)
            shutil.copyfile(s, d)
    return ws


def start_server(ws):
    handler = functools.partial(SimpleHTTPRequestHandler, directory=ws)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{httpd.server_address[1]}"


def files_in(ws):
    out = []
    for root, dirs, fs in os.walk(ws):
        dirs[:] = [d for d in dirs if not d.startswith(".")]
        for f in fs:
            out.append(os.path.relpath(os.path.join(root, f), ws))
    return sorted(out)


def make_ctx(outdir, cfg, answer, rounds, calls, ws, answers=None):
    return {"outdir": ws, "taskdir": cfg["dir"], "answer": answer,
            "answers": answers or [], "rounds": rounds, "tool_calls": calls,
            "files": files_in(ws), "metrics": {}, "config": cfg}


def parse_args(tc):
    try:
        return json.loads(tc["function"].get("arguments") or "{}")
    except Exception:
        return {}


def dispatch(toolbox, cfg, calls, name, args):
    forced = False
    limit = (cfg.get("fail_tool_after") or {}).get(name)
    if limit is not None:
        done = sum(1 for c in calls if c["tool"] == name and not c.get("forced_error"))
        if done >= limit:
            rec = {"tool": name, "args": args,
                   "result": "ERROR: online_search service unavailable (simulated failure).",
                   "error": "forced failure", "forced_error": True,
                   "dt_s": 0, "ts": time.strftime("%F %T")}
            toolbox.calls.append(rec)
            calls.append(rec)
            return [{"type": "text", "text": rec["result"]}], rec
    parts, rec = toolbox.dispatch(name, args)
    calls.append(rec)
    return parts, rec


def run_single(t, key, cfg, prompt, outdir, name, extra):
    t0 = time.time()
    msgs = [{"role": "system", "content": SYSTEM["single"]},
            {"role": "user", "content": prompt}]
    result = chat(t, key, msgs, cfg["max_tokens"], extra=extra)
    answer = result["content"]
    rec = metric_row(name, cfg["mode"], result)
    rec["turns"] = 1
    rec["tool_calls"] = 0
    rec["total_s"] = round(time.time() - t0, 2)
    trace = {"target": t.get("_name"), "test": cfg["id"], "mode": "single",
             "rounds": [{"round": 0, "content": answer[:1200],
                         "reasoning_chars": len(result["reasoning"]), "dt_s": result["total"]}],
             "tool_calls": [], "answers": [answer]}
    return answer, rec, trace, result


def run_scenario(t, key, cfg, prompt, outdir, name, extra):
    t0 = time.time()
    ws = prepare_ws(outdir, cfg, name)
    turns_path = os.path.join(cfg["dir"], cfg["turns"]) if cfg.get("turns") else None
    turns = json.load(open(turns_path)) if turns_path and os.path.isfile(turns_path) else None
    if not turns:
        turns = [prompt]
    toolbox = ToolBox(cfg.get("tools", []), ws, images=cfg.get("images", False))
    toolbox.start()
    rounds, calls, answers, usage = [], [], [], []
    msgs = [{"role": "system", "content": SYSTEM["scenario"]},
            {"role": "user", "content": turns[0]}]
    final_result = None
    try:
        for ti, turn in enumerate(turns, 1):
            if ti > 1:
                msgs.append({"role": "user", "content": turn})
            answered = False
            for _ in range(max(1, cfg.get("max_rounds", 6))):
                if len(rounds) >= cfg.get("max_rounds", 6):
                    break
                m = chat(t, key, msgs, cfg["max_tokens"], tools=toolbox.tools, extra=extra)
                final_result = m
                usage.append(m.get("usage") or {})
                tcs = m.get("tool_calls") or []
                rounds.append({"round": len(rounds), "turn": ti,
                               "content": (m.get("content") or "")[:1200],
                               "reasoning_chars": len(m.get("reasoning") or ""),
                               "tools": [[x["function"]["name"], parse_args(x)] for x in tcs],
                               "dt_s": m.get("total")})
                if tcs:
                    msgs.append({"role": "assistant", "content": m.get("content"),
                                 "tool_calls": tcs})
                    for tc in tcs:
                        fn = tc["function"]["name"]
                        args = parse_args(tc)
                        parts, _ = dispatch(toolbox, cfg, calls, fn, args)
                        msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                     "name": fn, "content": parts[0]["text"]})
                    continue
                answers.append(m.get("content") or "")
                msgs.append({"role": "assistant", "content": m.get("content")})
                answered = True
                break
            if not answered and ti == len(turns):
                answers.append("")
    finally:
        toolbox.close()
    answer = answers[-1] if answers else ""
    rec = metric_row(name, cfg["mode"], final_result or {"content": answer})
    rec["prompt_tokens"] = sum(u.get("prompt_tokens", 0) for u in usage) or rec["prompt_tokens"]
    rec["completion_tokens"] = sum(u.get("completion_tokens", 0) for u in usage) or rec["completion_tokens"]
    rec["turns"] = len(rounds)
    rec["tool_calls"] = len(calls)
    rec["user_turns"] = len(answers)
    rec["total_s"] = round(time.time() - t0, 2)
    ct = rec["completion_tokens"] or 0
    if ct > 1:
        rec["decode_tps"] = round(ct / max(rec["total_s"] - 0.2, 0.2), 2)
    ctx = make_ctx(outdir, cfg, answer, rounds, calls, ws, answers)
    if cfg.get("_verify_now"):
        try:
            rec["preview_grade"] = load_checks(cfg).grade(ctx)
        except Exception as e:
            rec["preview_grade"] = {"error": str(e)[:200]}
    trace = {"target": t.get("_name"), "test": cfg["id"], "mode": "scenario",
             "rounds": rounds, "tool_calls": calls, "answers": answers}
    return answer, rec, trace, ctx


def open_toolbox(kinds, ws, images, tname):
    """Start the toolbox, retrying if a live tool (Blender add-on) is not yet
    reachable — the model's own bpy can transiently drop the :9876 server."""
    last = None
    for attempt in range(5):
        toolbox = ToolBox(kinds, ws, images=images)
        try:
            toolbox.start()
            return toolbox
        except Exception as e:
            toolbox.close()
            last = e
            if "blender" not in (kinds or []):
                raise
            print(f"[{tname}] blender preflight retry {attempt + 1}: {str(e)[:90]}", flush=True)
            time.sleep(6)
    raise last


def run_agent(t, key, cfg, prompt, outdir, name, extra):
    t0 = time.time()
    ws = prepare_ws(outdir, cfg, name)
    httpd = None
    serve_url = None
    if cfg.get("serve"):
        httpd, serve_url = start_server(ws)
        prompt = prompt.replace("{SERVE_URL}", serve_url)
    toolbox = open_toolbox(cfg.get("tools", []), ws, cfg.get("images", False),
                           f"{t.get('_name')}/{cfg['id']}")
    checks = load_checks(cfg)
    rounds, calls, usage = [], [], []
    msgs = [{"role": "system", "content": SYSTEM["agent"]},
            {"role": "user", "content": prompt + f"\n\nTon workspace est {ws}."}]
    final_result = None
    answer = ""
    nudges = 0
    max_rounds = cfg.get("max_rounds", 24)
    live = os.path.join(ws, "_live.json")

    def live_write(phase):
        d = {"test": cfg["id"], "target": t.get("_name"), "round": len(rounds),
             "max_rounds": max_rounds, "phase": phase, "ts": time.time(),
             "tool_calls": len(calls), "files": len(files_in(ws)), "pid": os.getpid()}
        try:
            json.dump(d, open(live, "w"))
        except OSError:
            pass

    try:
        live_write("start")
        for i in range(max_rounds):
            m = chat(t, key, msgs, cfg["max_tokens"], tools=toolbox.tools, extra=extra)
            final_result = m
            usage.append(m.get("usage") or {})
            tcs = m.get("tool_calls") or []
            answer = m.get("content") or ""
            rounds.append({"round": i, "content": answer[:1200],
                           "reasoning_chars": len(m.get("reasoning") or ""),
                           "tools": [[x["function"]["name"], parse_args(x)] for x in tcs],
                           "results": [], "dt_s": m.get("total"), "usage": m.get("usage")})
            print(f"[{t.get('_name')}/{cfg['id']}] r{i}: {len(tcs)} tools", flush=True)
            if tcs:
                msgs.append({"role": "assistant", "content": answer, "tool_calls": tcs})
                for tc in tcs:
                    fn = tc["function"]["name"]
                    args = parse_args(tc)
                    parts, rec = dispatch(toolbox, cfg, calls, fn, args)
                    rounds[-1]["results"].append(rec["result"][:800])
                    content = parts if cfg.get("images") and len(parts) > 1 else parts[0]["text"]
                    msgs.append({"role": "tool", "tool_call_id": tc.get("id", ""),
                                 "name": fn, "content": content})
                live_write("tools_done")
                remaining = max_rounds - (i + 1)
                if remaining == 5:
                    try:
                        early = checks.grade(make_ctx(outdir, cfg, answer, rounds, calls, ws))
                        miss = "; ".join((early.get("issues") or [])[:6])[:500] or "aucun"
                    except Exception:
                        miss = "aucun"
                    msgs.append({"role": "user", "content":
                        f"Plus que {remaining} tours: Termine les livrables RESTANTS maintenant "
                        "et pr\u00e9pare ta r\u00e9ponse finale. Arr\u00eate les ajustements cosm\u00e9tiques. "
                        "V\u00e9rification automatique — livrables manquants: " + miss +
                        ". \u00c9cris notamment FINAL.md listant chaque livrable demand\u00e9 avec son chemin."})
                    rounds[-1]["budget_nudge"] = remaining
                    rounds[-1]["budget_nudge_missing"] = miss
                continue
            ctx = make_ctx(outdir, cfg, answer, rounds, calls, ws)
            try:
                grade = checks.grade(ctx)
            except Exception as e:
                grade = {"pass": None, "issues": [f"grader error: {e}"], "checks": {}}
            rounds[-1]["grade"] = grade
            if grade.get("pass") or nudges >= 2:
                break
            nudges += 1
            issues = "; ".join((grade.get("issues") or [])[:5])[:900]
            msgs.append({"role": "user", "content":
                         "VÉRIFICATION AUTOMATIQUE: échec — " + issues +
                         "\nCorrige les livrables dans le workspace, vérifie-les, puis réessaie. "
                          "Ne réponds pas par du texte seul."})
            live_write("nudge")
        if not (answer or "").strip():
            msgs.append({"role": "user", "content":
                "TERMINE MAINTENANT — n'appelle AUCUN outil. \u00c9cris en fran\u00e7ais ta r\u00e9ponse finale: "
                "confirme les livrables produits et leurs chemins, puis recopie la valeur exacte demand\u00e9e."})
            m2 = chat(t, key, msgs, 2048, tools=None, extra=extra)
            answer = (m2.get("content") or "").strip()
            usage.append(m2.get("usage") or {})
            final_result = m2
            rounds.append({"round": len(rounds), "content": answer[:1200],
                           "reasoning_chars": len(m2.get("reasoning") or ""),
                           "tools": [], "results": [], "final_nudge": True, "dt_s": m2.get("total")})
            print(f"[{t.get('_name')}/{cfg['id']}] final-text nudge -> {len(answer)} chars", flush=True)
    finally:
        ctx = make_ctx(outdir, cfg, answer, rounds, calls, ws)
        try:
            final_grade = checks.grade(ctx)
        except Exception as e:
            final_grade = {"pass": None, "issues": [f"grader error: {e}"], "checks": {}}
        try:
            os.remove(live)
        except OSError:
            pass
        toolbox.close()
        if httpd:
            httpd.shutdown()
            httpd.server_close()
    rec = metric_row(name, cfg["mode"], final_result or {"content": answer})
    rec["prompt_tokens"] = sum(u.get("prompt_tokens", 0) for u in usage) or rec["prompt_tokens"]
    rec["completion_tokens"] = sum(u.get("completion_tokens", 0) for u in usage) or rec["completion_tokens"]
    rec["turns"] = len(rounds)
    rec["tool_calls"] = len(calls)
    rec["retries"] = sum(1 for c in calls if c.get("error") and not c.get("forced_error"))
    rec["self_recovered"] = any(c.get("error") for c in calls) and final_grade.get("pass") is True
    rec["nudges"] = nudges
    rec["final_nudge"] = any(r.get("final_nudge") for r in rounds)
    rec["total_s"] = round(time.time() - t0, 2)
    ct = rec["completion_tokens"] or 0
    if ct > 1:
        rec["decode_tps"] = round(ct / max(rec["total_s"] - 0.2, 0.2), 2)
    rec["preview_grade"] = final_grade
    trace = {"target": t.get("_name"), "test": cfg["id"], "mode": "agent",
             "rounds": rounds, "tool_calls": calls, "answers": [answer],
             "final_grade": final_grade, "workspace_files": files_in(ws),
             "serve_url": serve_url}
    return answer, rec, trace, ctx


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", required=True,
                    help="configured TARGETS name or a one-shot slug under results/.adhoc/")
    ap.add_argument("--only", default=None, help="comma-separated task ids")
    ap.add_argument("--runs", type=int, default=2)
    ap.add_argument("--out", default=os.path.join(HERE, "results"))
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    tasks = load_tasks(only)
    t = find_target(a.target)
    if t is None:
        sys.exit(f"unknown target {a.target!r}: not in TARGETS and no results/.adhoc/{a.target}.json")
    t["_name"] = a.target
    key = get_key(t)
    outdir = os.path.join(a.out, a.target)
    for sub in ("transcripts", "traces", "workspaces"):
        os.makedirs(os.path.join(outdir, sub), exist_ok=True)
    rows_path = os.path.join(outdir, "metrics.jsonl")
    status_path = os.path.join(outdir, "status.json")
    plan = [(cfg, r) for cfg in tasks for r in range(1, a.runs + 1)]
    started = time.time()

    def status(done, cur):
        json.dump({"target": a.target, "suite": "ipcbench", "current": cur,
                   "done": done, "plan_total": len(plan), "started_ts": started,
                   "ts": time.strftime("%F %T")}, open(status_path, "w"))

    smp_host = t.get("sample_host") or t.get("key_host")
    smp = Sampler(smp_host) if smp_host else None
    if smp:
        smp.start()

    def dump_res():
        if smp:
            json.dump(smp.samples, open(os.path.join(outdir, "resources.json"), "w"))

    status([], "START")
    done = []
    with open(rows_path, "a") as rows:
        for cfg, r in plan:
            name = f"{cfg['id']}_r{r}"
            status(done, name)
            dump_res()
            prompt = load_prompt(cfg)
            extra = dict(t.get("extra") or {})
            try:
                if cfg["mode"] == "single":
                    answer, rec, trace, _ = run_single(t, key, cfg, prompt, outdir, name, extra)
                elif cfg["mode"] == "scenario":
                    answer, rec, trace, _ = run_scenario(t, key, cfg, prompt, outdir, name, extra)
                else:
                    answer, rec, trace, _ = run_agent(t, key, cfg, prompt, outdir, name, extra)
                rec["test"] = name
                rec["task"] = cfg["id"]
                rec["verdict"] = (rec.get("preview_grade") or {}).get("pass")
                if rec.get("verdict") is None:
                    rec["verdict"] = "ungraded"
                rec["degenerate"] = (not (answer or "").strip() or
                                     bool(re.search(r"<tool_call>|\[TOOL_CALLS?\]", answer)))
            except Exception as e:
                rec = {"test": name, "task": cfg["id"], "error": str(e)[:300],
                       "ts": time.strftime("%F %T"), "verdict": "error", "degenerate": True}
                trace = {"target": a.target, "test": cfg["id"], "error": str(e)[:300]}
                answer = ""
            rec["ts"] = time.strftime("%F %T")
            rows.write(json.dumps(rec, ensure_ascii=False) + "\n")
            rows.flush()
            with open(os.path.join(outdir, "transcripts", name + ".out.txt"), "w") as f:
                f.write(answer)
            json.dump(trace, open(os.path.join(outdir, "traces", name + ".json"), "w"),
                      indent=1, ensure_ascii=False)
            done.append(name)
            print(f"[{a.target}] {name}: {rec.get('verdict')} "
                  f"{rec.get('decode_tps')} tok/s {rec.get('turns')} turns", flush=True)
    if smp:
        smp.stop_flag.set()
        dump_res()
    status(done, "ALL DONE")
    print(f"[runner:{a.target}] finished {len(done)} runs")


if __name__ == "__main__":
    main()
