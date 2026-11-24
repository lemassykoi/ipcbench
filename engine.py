#!/usr/bin/env python3
"""Engine layer for ipcbench: targets, streaming chat calls, calibration, resource sampling."""
import json
import os
import subprocess
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
CALIB_PATH = os.path.join(HERE, "calib.json")
OUTPUT_TOKENS = 32768
REQ_TIMEOUT = 5400
RETRIES = 2
FILLER = "The quick brown fox jumps over the lazy dog while the sun sets behind the quiet hill. "

TARGETS = {
    "4070": dict(base="http://10.0.0.49:8013/v1", key_host="10.0.0.49",
                 key_path="~/.config/strata/api-key-local", model="qwen3.8-flash-next-iq2_xs"),
    "3090": dict(base="http://10.0.0.51:8013/v1", key_host="10.0.0.51",
                 key_path="~/.config/strata/api-key", model="qwen3.8-flash-next-unsloth-ud-iq4_xs",
                 extra={"reasoning_effort": "high"}),
    "gb10": dict(base="http://10.0.0.126:30001/v1", key_host=None,
                 key_path=os.path.expanduser("~/.config/qwen38/api-key"), model="qwen3.8-flash-next",
                 extra={"chat_template_kwargs": {"reasoning_effort": "xhigh"}}),
    "pgx1": dict(base="http://10.0.0.126:11474/v1", key_host=None,
                 key_path=os.path.expanduser("~/.config/qwen38/api-key"), model="qwen3.8-flash-next",
                 extra={"chat_template_kwargs": {"reasoning_effort": "xhigh"}}, sample_host="10.0.0.126"),
    "pgx2": dict(base="http://10.0.0.193:11474/v1", key_host=None,
                 key_path=os.path.expanduser("~/.config/qwen38/api-key"), model="qwen3.8-flash-next",
                 extra={"chat_template_kwargs": {"reasoning_effort": "xhigh"}}, sample_host="10.0.0.193"),
    "pair-tf": dict(base="http://10.0.0.193:8020/v1", key_host=None,
                    key_path=os.path.expanduser("~/.config/pair/api-key"), model="glm-5.3-flash",
                    extra={"chat_template_kwargs": {"reasoning_effort": "xhigh"}}, sample_host="10.0.0.193"),
    "pair-qwen": dict(base="http://10.0.0.193:8020/v1", key_host=None,
                      key_path=os.path.expanduser("~/.config/pair/api-key"), model="qwen3.8-flash-next",
                      extra={"chat_template_kwargs": {"reasoning_effort": "xhigh"}}, sample_host="10.0.0.193"),
    "pair-hy4": dict(base="http://10.0.0.193:8091/v1", key_host=None,
                     key_path=os.path.expanduser("~/.config/pair/api-key"), model="hy4-flash",
                     extra={"chat_template_kwargs": {"reasoning_effort": "high"}}, sample_host="10.0.0.193"),
    "pair-glm53": dict(base="http://10.0.0.193:8000/v1", key_host=None,
                       key_path=os.path.expanduser("~/.config/pair/api-key"),
                       model="nvidia/GLM-5.3-Flash-FP4-Local",
                       extra={"chat_template_kwargs": {"reasoning_effort": "high"}}, sample_host="10.0.0.193"),
    "3090-v31-yarn524k": dict(base="http://10.0.0.51:8013/v1", key_host="10.0.0.51",
                              key_path="~/.config/strata/api-key",
                              model="qwen3.8-flash-next-unsloth-ud-iq4_xs",
                              extra={"reasoning_effort": "high"}),
}

LABEL = {
    "pair-hy4": "PAIR 2xGB10 vLLM Hy4-flash NVFP4 TP2 (1M)",
    "3090": "3090 Strata Qwen3.8-Flash-Next IQ4_XS (1M)",
    "4070": "4070Ti Strata IQ2_XS",
    "gb10": "GB10 vLLM Qwen3.8-Flash-Next",
    "pgx1": "PGX1 GB10 vLLM Qwen3.8-Flash-Next-FP4-Local (1M, :11474)",
    "pgx2": "PGX2 PAIR vLLM Qwen3.8-Flash-Next-NVFP4 (1M, :11474)",
    "pair-tf": "PAIR TensorFold Qwen3.8-Flash-Next NVFP4",
    "pair-qwen": "PAIR vLLM Qwen3.8-Flash-Next NVFP4",
    "pair-glm53": "PAIR vLLM GLM-5.3-Flash-FP4",
    "3090-v31-yarn524k": "3090 Strata Qwen3.8-Flash-Next yarn-524k",
}

NO_THINK = {"chat_template_kwargs": {"reasoning_effort": "no_think"},
            "reasoning_effort": "no_think"}


def get_key(target):
    if target["key_host"]:
        r = subprocess.run(["ssh", f"clement@{target['key_host']}", f"cat {target['key_path']}"],
                           capture_output=True, text=True, timeout=20)
        return r.stdout.strip()
    return open(os.path.expanduser(target["key_path"])).read().strip()


def filler_unit(i):
    return f"{i:08d} {FILLER}"


def calibrate(base, key, model, tgt_name, extra=None):
    cal = json.load(open(CALIB_PATH)) if os.path.exists(CALIB_PATH) else {}
    c = cal.get(tgt_name)
    if c and c.get("model") == model:
        return c["tpu"], c["overhead"]

    def probe(n):
        body = {"model": model, "temperature": 0.0, "seed": 42, "max_tokens": 1,
                "messages": [{"role": "system", "content": "Answer concisely."},
                             {"role": "user", "content": " ".join(filler_unit(i) for i in range(n)) +
                              "\nReply with exactly the single token: OK"}]}
        if extra:
            body.update(extra)
        req = urllib.request.Request(base.rstrip("/") + "/chat/completions",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json",
                                              "Authorization": f"Bearer {key}"})
        with urllib.request.urlopen(req, timeout=300) as r:
            return json.loads(r.read())["usage"]["prompt_tokens"]

    p1, p2 = probe(20), probe(200)
    tpu = max(1.0, (p2 - p1) / 180.0)
    overhead = p2 - tpu * 200
    cal[tgt_name] = {"model": model, "tpu": round(tpu, 3), "overhead": round(overhead, 2),
                     "ts": time.strftime("%F %T")}
    json.dump(cal, open(CALIB_PATH, "w"), indent=1)
    return tpu, overhead


def pad_to(tokens, tpu, overhead, start):
    n = max(2, round((tokens - overhead) / tpu))
    return " ".join(filler_unit(start + i) for i in range(n))


class Sampler(threading.Thread):
    def __init__(self, host):
        super().__init__(daemon=True)
        self.host = host
        self.stop_flag = threading.Event()
        self.samples = []

    def run(self):
        cmd = ("nvidia-smi --query-gpu=utilization.gpu,memory.used --format=csv,noheader,nounits 2>/dev/null | head -1; "
               "free -m | awk '/^Mem:/{print $3, $7}'")
        while not self.stop_flag.is_set():
            try:
                r = subprocess.run(["ssh", f"clement@{self.host}", cmd],
                                   capture_output=True, text=True, timeout=12)
                lines = [x for x in r.stdout.strip().splitlines() if x.strip()]
                s = {"t": time.time()}
                if lines and "," in lines[0]:
                    gpu, mem = [x.strip() for x in lines[0].split(",")[:2]]
                    v = int(mem) if mem.isdigit() else None
                    s["gpu_util"], s["vram_mib"] = int(gpu), (v if v else None)
                if len(lines) > 1:
                    used, avail = lines[1].split()[:2]
                    s["ram_used_mib"], s["ram_avail_mib"] = int(used), int(avail)
                self.samples.append(s)
            except Exception:
                pass
            self.stop_flag.wait(5)


def call(base, key, model, messages, max_tokens, tools=None, extra=None,
         stream_for_ttft=True):
    body = {"model": model, "messages": messages, "temperature": 0.0, "seed": 42,
            "max_tokens": max_tokens, "top_k": 20, "min_p": 0.0,
            "repetition_penalty": 1.0, "stream": stream_for_ttft,
            "stream_options": {"include_usage": True}}
    if tools:
        body["tools"] = tools
        body["tool_choice"] = "auto"
    if extra:
        body.update(extra)
    data = json.dumps(body).encode()
    req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=data,
                                 headers={"Content-Type": "application/json",
                                          "Authorization": f"Bearer {key}"})
    t0 = time.time()
    ttft = None
    content, reasoning = [], []
    usage, timings = {}, {}
    calls, finish = {}, None
    with urllib.request.urlopen(req, timeout=REQ_TIMEOUT) as resp:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("data:"):
                continue
            try:
                j = json.loads(line[5:].strip())
            except Exception:
                continue
            if j.get("usage"):
                usage = j["usage"]
            if j.get("timings"):
                timings = j["timings"]
            for ch in j.get("choices", []):
                d = ch.get("delta") or ch.get("message") or {}
                rc = d.get("reasoning_content") or d.get("reasoning")
                if d.get("content"):
                    if ttft is None:
                        ttft = time.time() - t0
                    content.append(d["content"])
                if rc:
                    if ttft is None:
                        ttft = time.time() - t0
                    reasoning.append(rc)
                for tc in (d.get("tool_calls") or []):
                    i = tc.get("index", 0)
                    e = calls.setdefault(i, {"id": "", "type": "function",
                                             "function": {"name": "", "arguments": ""}})
                    if tc.get("id"):
                        e["id"] = tc["id"]
                    if tc.get("type"):
                        e["type"] = tc["type"]
                    fn = tc.get("function") or {}
                    if fn.get("name"):
                        e["function"]["name"] += fn["name"]
                    if fn.get("arguments"):
                        e["function"]["arguments"] += fn["arguments"]
                if ch.get("finish_reason"):
                    finish = ch["finish_reason"]
    return {"content": "".join(content), "reasoning": "".join(reasoning),
            "usage": usage, "timings": timings, "ttft": ttft,
            "total": time.time() - t0, "tool_calls": [calls[k] for k in sorted(calls)],
            "finish_reason": finish}


def call_with_retry(base, key, model, messages, max_tokens, tools=None, extra=None,
                    retries=RETRIES):
    last = None
    for i in range(retries + 1):
        try:
            r = call(base, key, model, messages, max_tokens, tools=tools, extra=extra)
            r["retries"] = i
            return r
        except Exception as e:
            last = e
            time.sleep(6)
    raise RuntimeError(f"request failed after {retries + 1} attempts: {last}")


def metric_row(test, kind, result):
    usage = result.get("usage") or {}
    timings = result.get("timings") or {}
    ttft = result.get("ttft")
    total = result.get("total", 0.0)
    rec = {"test": test, "kind": kind, "total_s": round(total, 2),
           "ttft_s": round(ttft, 3) if ttft else None,
           "completion_tokens": usage.get("completion_tokens"),
           "prompt_tokens": usage.get("prompt_tokens"),
           "timings": timings, "content_chars": len(result.get("content") or ""),
           "reasoning_chars": len(result.get("reasoning") or ""),
           "retries": result.get("retries", 0), "ts": time.strftime("%F %T")}
    cn = rec["completion_tokens"] or 0
    if ttft and total > ttft and cn > 1:
        rec["decode_tps"] = round((cn - 1) / (total - ttft), 2)
    pn = rec["prompt_tokens"] or 0
    if pn:
        if timings.get("prompt_ms"):
            rec["prefill_tps"] = round(pn / (timings["prompt_ms"] / 1000), 1)
        elif ttft:
            rec["prefill_tps_est"] = round(pn / max(ttft, 0.001), 1)
    return rec
