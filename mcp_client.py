#!/usr/bin/env python3
"""Real MCP and sandbox tool backends exposed to the model as OpenAI function tools."""
import base64
import json
import os
import queue
import re
import shlex
import shutil
import subprocess
import sys
import threading
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, "bridges"))

PLAYWRIGHT_CMD = ["playwright-mcp", "--browser", "chromium"]
TAVILY_KEY = os.path.expanduser("~/.config/tavily/api-key")
IMAGES_TO_MODEL = os.environ.get("IPC_IMAGES_TO_MODEL", "0") == "1"

FS_TOOLS = [
    {"type": "function", "function": {"name": "write_file", "description": "Write a text file in the workspace (relative path).",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
                     "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "read_file", "description": "Read a workspace text file.",
      "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "list_dir", "description": "List workspace files.",
      "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {"name": "exec", "description": "Run one allowlisted verification command in the workspace: python3 FILE, node FILE, ls, cat FILE, unzip -l FILE, or brave-browser --headless=new ... file://<workspace file>.",
      "parameters": {"type": "object", "properties": {"cmd": {"type": "string"}}, "required": ["cmd"]}}},
]

ONLINE_TOOL = {"type": "function", "function": {
    "name": "online_search",
    "description": "Search the public web. Use for current or external facts only; never for private IPC configuration or customer data.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string"}, "max_results": {"type": "integer", "default": 5},
        "topic": {"type": "string", "enum": ["general", "news"], "default": "general"}},
        "required": ["query"]}}}


def safe_join(ws, path):
    fp = os.path.realpath(os.path.join(ws, path))
    root = os.path.realpath(ws)
    if not fp.startswith(root + os.sep) and fp != root:
        raise ValueError("path escapes workspace")
    return fp


class StdioMCP:
    def __init__(self, command, env=None):
        self.command = command
        self.proc = None
        self.q = queue.Queue()
        self.next_id = 0
        self.env = dict(os.environ)
        self.env.update(env or {})

    def start(self):
        self.proc = subprocess.Popen(self.command, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, bufsize=1, env=self.env)
        threading.Thread(target=self._reader, daemon=True).start()
        self.request("initialize", {"protocolVersion": "2024-11-05",
                                    "capabilities": {},
                                    "clientInfo": {"name": "ipcbench", "version": "1.0"}})
        self.notify("notifications/initialized", {})

    def _reader(self):
        for line in self.proc.stdout:
            try:
                self.q.put(json.loads(line))
            except Exception:
                pass

    def request(self, method, params=None, timeout=180):
        self.next_id += 1
        rid = self.next_id
        msg = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            try:
                m = self.q.get(timeout=max(0.1, deadline - time.time()))
            except queue.Empty:
                break
            if m.get("id") == rid:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})
            self.q.put(m)
        raise TimeoutError(f"MCP {method} timed out")

    def notify(self, method, params=None):
        self.proc.stdin.write(json.dumps({"jsonrpc": "2.0", "method": method,
                                          "params": params or {}}) + "\n")
        self.proc.stdin.flush()

    def tools(self):
        r = self.request("tools/list", {})
        return r.get("tools", [])

    def call(self, name, args):
        r = self.request("tools/call", {"name": name, "arguments": args or {}})
        return self._parts(r)

    @staticmethod
    def _parts(r):
        parts = []
        for c in (r.get("content") or []):
            if c.get("type") == "text":
                parts.append({"type": "text", "text": c.get("text", "")})
            elif c.get("type") == "image":
                parts.append({"type": "image", "data": c.get("data", "")})
        if r.get("isError") and parts and parts[0].get("type") == "text":
            parts[0]["text"] = "ERROR: " + parts[0]["text"]
        return parts or [{"type": "text", "text": "ok"}]

    def close(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=5)
            except Exception:
                pass


class RemoteMCP:
    def __init__(self, url, headers):
        self.url = url
        self.headers = headers
        self.session = None
        self.next_id = 0

    def request(self, method, params=None, timeout=90, notify=False):
        self.next_id += 1
        rid = self.next_id
        body = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            body["id"] = rid
        headers = dict(self.headers)
        headers.update({"Content-Type": "application/json",
                        "Accept": "application/json, text/event-stream"})
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(self.url, data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            self.session = r.headers.get("Mcp-Session-Id") or self.session
            raw = r.read().decode("utf-8", "replace")
        if notify or not raw.strip():
            return {}
        for line in raw.splitlines():
            if line.startswith("data:"):
                try:
                    m = json.loads(line[5:].strip())
                except Exception:
                    continue
                if m.get("id") == rid:
                    if "error" in m:
                        raise RuntimeError(f"{method}: {m['error']}")
                    return m.get("result", {})
        try:
            m = json.loads(raw)
            if m.get("id") == rid:
                return m.get("result", {})
        except Exception:
            pass
        return {}

    def start(self):
        self.request("initialize", {"protocolVersion": "2024-11-05", "capabilities": {},
                                    "clientInfo": {"name": "ipcbench", "version": "1.0"}})
        self.request("notifications/initialized", {}, notify=True)

    def tools(self):
        return self.request("tools/list", {}).get("tools", [])

    def call(self, name, args):
        return StdioMCP._parts(self.request("tools/call", {"name": name, "arguments": args or {}}))

    def close(self):
        pass


class OnlineSearch:
    def __init__(self):
        self.mcp = None
        self.tool_name = None
        self.error = None
        key = open(TAVILY_KEY).read().strip() if os.path.exists(TAVILY_KEY) else ""
        try:
            self.mcp = RemoteMCP("https://mcp.tavily.com/mcp/",
                                 {"Authorization": f"Bearer {key}"})
            self.mcp.start()
            names = [t["name"] for t in self.mcp.tools()]
            self.tool_name = next((n for n in names if "search" in n.lower()), None)
            if not self.tool_name:
                raise RuntimeError(f"no search tool in {names}")
        except Exception as e:
            self.error = str(e)
            self.mcp = None

    def search(self, query, max_results=5, topic="general"):
        if self.mcp:
            args = {"query": query, "max_results": max_results}
            try:
                parts = self.mcp.call(self.tool_name, args)
                return "\n".join(p.get("text", "") for p in parts)[:8000]
            except Exception as e:
                self.error = str(e)
        key = open(TAVILY_KEY).read().strip() if os.path.exists(TAVILY_KEY) else ""
        body = {"api_key": key, "query": query, "max_results": max_results,
                "topic": topic, "search_depth": "basic"}
        req = urllib.request.Request("https://api.tavily.com/search",
                                     data=json.dumps(body).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=90) as r:
            data = json.loads(r.read())
        return json.dumps({"results": [{"title": x.get("title"), "url": x.get("url"),
                                        "content": x.get("content")} for x in data.get("results", [])]},
                          ensure_ascii=False)[:8000]


class ToolBox:
    def __init__(self, kinds, workspace, save_dir=None, images=False):
        self.kinds = set(kinds or [])
        self.workspace = workspace
        self.images = images
        self.save_dir = save_dir or os.path.join(workspace, "_shots")
        self.tools = []
        self.calls = []
        self.playwright = None
        self.blender = None
        self.online = None
        self.shot_n = 0
        self.online_tool_name = "online_search"
        os.makedirs(self.workspace, exist_ok=True)

    def start(self):
        if "fs" in self.kinds or "fs_read" in self.kinds:
            self.tools.extend(FS_TOOLS if "fs" in self.kinds else FS_TOOLS[1:3])
        if "online_search" in self.kinds:
            self.online = OnlineSearch()
            self.tools.append(ONLINE_TOOL)
        if "blender" in self.kinds:
            from blender_bridge import BlenderBridge
            self.blender = BlenderBridge(save_dir=self.save_dir)
            self.blender.start()
            if not self.blender.tools:
                raise RuntimeError("Blender MCP bridge has no tools (is Blender open on the desktop?)")
            self.tools.extend(self.blender.openai_tools())
            self.blender.reset_scene()
        if "playwright" in self.kinds:
            self.playwright = StdioMCP(PLAYWRIGHT_CMD)
            self.playwright.start()
            names = [t["name"] for t in self.playwright.tools()]
            wanted = [t for t in self.playwright.tools()
                      if t["name"] in names and not t["name"].startswith("_")]
            self.tools.extend(wanted)
        return self.tools

    def _save_image(self, b64, tag):
        os.makedirs(self.save_dir, exist_ok=True)
        self.shot_n += 1
        p = os.path.join(self.save_dir, f"{int(time.time())}_{self.shot_n:03d}_{tag}.png")
        try:
            open(p, "wb").write(base64.b64decode(b64))
            return p
        except Exception:
            return None

    def _parts(self, text, image_b64=None, tag="shot"):
        text = str(text)[:8000]
        parts = [{"type": "text", "text": text}]
        if image_b64:
            saved = self._save_image(image_b64, tag)
            if saved:
                parts[0]["text"] += f" [saved: {saved}]"
            if self.images or IMAGES_TO_MODEL:
                parts.append({"type": "image_url",
                              "image_url": {"url": f"data:image/png;base64,{image_b64}"}})
        return parts

    def _fs(self, name, args):
        if name == "write_file" and "fs" not in self.kinds:
            return "DENIED: read-only workspace"
        if name == "write_file":
            fp = safe_join(self.workspace, args["path"])
            os.makedirs(os.path.dirname(fp), exist_ok=True)
            open(fp, "w").write(args["content"])
            return f"wrote {args['path']} ({len(args['content'])} chars)"
        if name == "read_file":
            return open(safe_join(self.workspace, args["path"]), errors="replace").read()[:12000]
        if name == "list_dir":
            return "\n".join(sorted(os.listdir(self.workspace)))[:4000]
        if name == "exec":
            cmd = args.get("cmd", "").strip().strip("`")
            if re.search(r"[;|>&]", cmd):
                return "DENIED: one plain allowlisted command (no ; | > &)"
            root = os.path.realpath(self.workspace)
            ok = (re.match(r"^python3 \S+.*$", cmd) or re.match(r"^node \S+.*$", cmd)
                  or cmd in ("ls", "ls -la") or re.match(r"^cat \S+$", cmd)
                  or re.match(r"^unzip -l \S+$", cmd)
                  or (cmd.startswith("brave-browser --headless") and "file://" in cmd))
            if not ok:
                return "DENIED: command not in allowlist"
            if cmd.startswith("cat ") or cmd.startswith("python3 ") or cmd.startswith("node "):
                target = cmd.split(None, 1)[1].split()[0]
                if target.startswith("/") and not os.path.realpath(target).startswith(root):
                    return "DENIED: path escapes workspace"
            try:
                argv = shlex.split(cmd)
                r = subprocess.run(argv, cwd=self.workspace, capture_output=True,
                                   text=True, timeout=120)
                out = r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr else "")
                return f"exit={r.returncode}\n{out[:6000]}"
            except subprocess.TimeoutExpired:
                return "TIMEOUT after 120s"
            except Exception as e:
                return f"ERROR: {e}"
        return "unknown fs tool"

    def dispatch(self, name, args):
        t0 = time.time()
        err = None
        image_b64 = None
        try:
            if name in ("write_file", "read_file", "list_dir", "exec"):
                parts = self._parts(self._fs(name, args))
            elif name == "online_search":
                parts = self._parts(self.online.search(**args))
            elif self.blender and name in {t["function"]["name"] for t in self.blender.openai_tools()}:
                raw = self.blender.call(name, args)
                texts = [p.get("text", "") for p in raw if p.get("type") == "text"]
                image_b64 = next((p.get("image_url", {}).get("url", "").split(",", 1)[-1]
                                  for p in raw if p.get("type") == "image_url"), None)
                parts = self._parts("\n".join(texts), image_b64, name)
            elif self.playwright:
                raw = self.playwright.call(name, args)
                texts = [p.get("text", "") for p in raw if p.get("type") == "text"]
                text = "\n".join(texts)
                image_b64 = next((p.get("data") for p in raw if p.get("type") == "image"), None)
                if not image_b64 and name == "browser_take_screenshot":
                    for mm in re.finditer(r"\]\(\./([^)\s]+\.(?:png|jpg|jpeg|webp))\)", text, re.I):
                        src = os.path.join(os.getcwd(), mm.group(1))
                        if not os.path.isfile(src):
                            continue
                        os.makedirs(self.save_dir, exist_ok=True)
                        self.shot_n += 1
                        ext = os.path.splitext(src)[1]
                        dst = os.path.join(self.save_dir, f"{int(time.time())}_{self.shot_n:03d}_{name}{ext}")
                        shutil.copyfile(src, dst)
                        image_b64 = base64.b64encode(open(dst, "rb").read()).decode("ascii")
                        break
                parts = self._parts(text, None, name)
                if image_b64 and (self.images or IMAGES_TO_MODEL):
                    parts.append({"type": "image_url",
                                  "image_url": {"url": f"data:image/png;base64,{image_b64}"}})
            else:
                raise RuntimeError(f"unknown tool {name}")
        except Exception as e:
            err = f"{type(e).__name__}: {str(e)[:400]}"
            parts = self._parts("ERROR: " + err)
        text = parts[0].get("text", "")
        if not err and (re.search(r'"status"\s*:\s*"error"', text) or text.startswith("ERROR:")):
            err = text.splitlines()[0][:300]
        rec = {"tool": name, "args": args, "result": text[:1200],
               "error": err, "dt_s": round(time.time() - t0, 2), "ts": time.strftime("%F %T")}
        self.calls.append(rec)
        return parts, rec

    def close(self):
        if self.playwright:
            self.playwright.close()
        if self.blender:
            try:
                self.blender.close()
            except Exception:
                pass
