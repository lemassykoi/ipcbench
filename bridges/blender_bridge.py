#!/usr/bin/env python3
"""Bridge between the bench's OpenAI-tools agent loop and a live Blender session.

Two transports, chosen at start():
1. "mcp" (preferred): spawns the official Blender Lab MCP server
   (/home/clement/opt/blender_mcp/mcp via uv, stdio JSON-RPC) and exposes its full
   tool set (execute_blender_code, get_objects_summary, screenshots, api docs, ...)
   as OpenAI function tools. Screenshot results become image content parts.
2. "tcp" (fallback): talks the add-on socket protocol directly (JSON + NUL over
   TCP, default localhost:9876) with a curated subset of the same tool names,
   using the MCP repo's *_toolcode.py templates. No image dependency on the SDK.

Also provides the health probe used by status.py and scene reset / .blend
snapshot helpers used by agentrun.py.

Usage (agent):  bridge = BlenderBridge().start()
                tools = bridge.openai_tools()
                parts = bridge.call(name, arguments)   # OpenAI content parts
                bridge.close()
Usage (probe):  BlenderBridge.probe() -> dict (cached 5s)
"""
import base64
import glob
import json
import os
import socket
import subprocess
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MCP_DIR = os.environ.get("BLENDER_MCP_DIR", "/home/clement/opt/blender_mcp/mcp")
BLENDER_BIN = os.environ.get("BLENDER_PATH", "/home/clement/opt/blender/blender")
ADDON_HOST = os.environ.get("BLENDER_MCP_HOST", "127.0.0.1")
ADDON_PORT = int(os.environ.get("BLENDER_MCP_PORT", "9876"))
PROTO_VERSION = "2024-11-05"
TOOL_CALL_TIMEOUT = 300

# Curated tools for tcp fallback: (name, description, schema, toolcode_stem, param_names)
FALLBACK_TOOLS = [
    ("execute_blender_code",
     "Execute Python code in the connected Blender instance (full bpy access). "
     "To return data, assign a JSON-serialisable dict to a variable named `result`.",
     {"type": "object", "properties": {"code": {"type": "string"}}, "required": ["code"]},
     None, None),
    ("get_objects_summary",
     "Return a tree of collections and their objects (names, types, parents, visibility).",
     {"type": "object", "properties": {}, "required": []},
     "get_objects_summary", None),
    ("get_object_detail_summary",
     "Return a structured summary of one object: type, transforms, parent, children, "
     "modifiers, materials, visibility, dimensions.",
     {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
     "get_object_detail_summary", ["name"]),
    ("jump_to_view3d_object_by_name",
     "Frame the 3D viewport on the named object (select + view).",
     {"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
     "jump_to_view3d_object_by_name", ["name"]),
    ("get_screenshot_of_area_as_image",
     "Take a screenshot of a single Blender area (e.g. area_ui_type='VIEW_3D') and "
     "return it as a PNG image.",
     {"type": "object",
      "properties": {"area_ui_type": {"type": "string", "enum": ["VIEW_3D", "IMAGE_EDITOR", "OUTLINER"]},
                     "size_limit_in_bytes": {"type": "integer", "default": 0}},
      "required": ["area_ui_type"]},
     "get_screenshot_of_area_as_image", ["area_ui_type", "size_limit_in_bytes"]),
]

_probe_cache = {"ts": 0.0, "data": None}
_probe_lock = threading.Lock()


def _tcp_send(code, host=None, port=None, timeout=TOOL_CALL_TIMEOUT):
    """Add-on socket protocol: JSON {type:'execute',code} + NUL, response + NUL."""
    host = host or ADDON_HOST
    port = port or ADDON_PORT
    req = json.dumps({"type": "execute", "code": code, "strict_json": False}) + "\0"
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(timeout)
        s.connect((host, port))
        s.sendall(req.encode("utf-8"))
        buf = bytearray()
        while b"\0" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf.extend(chunk)
    if not buf:
        raise ConnectionError("empty response from Blender add-on")
    return json.loads(bytes(buf).split(b"\0")[0])


def _toolcode(stem, params):
    """Build add-on code from the MCP repo's toolcode template (no blmcp import)."""
    path = os.path.join(MCP_DIR, "blmcp", "tools", stem + "_toolcode.py")
    src = open(path).read()
    return src + f"\n_rv = main({params!r})\n" \
        "if callable(_rv):\n    check_is_finished = _rv\n    result = {}\nelse:\n    result = _rv._asdict()\n"


def _health_code():
    return ("import bpy, sys\nresult = {'objects': len(bpy.data.objects), "
            "'version': bpy.app.version_string, 'file': bpy.data.filepath or None, "
            "'py': sys.version.split()[0]}")


class BlenderBridge:
    def __init__(self, prefer_mcp=True, save_dir=None, verbose=True):
        self.mode = None          # "mcp" | "tcp"
        self.tools = []           # MCP tools/list raw
        self.save_dir = save_dir
        self.verbose = verbose
        self._prefer_mcp = prefer_mcp
        self._proc = None         # MCP server subprocess
        self._next_id = 1
        self._shot_n = 0

    def log(self, *a):
        if self.verbose:
            print("[bridge]", *a, flush=True)

    # ---------- MCP stdio client ----------
    def _mcp_request(self, method, params=None, timeout=60):
        rid = self._next_id
        self._next_id += 1
        msg = {"jsonrpc": "2.0", "id": rid, "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()
        deadline = time.time() + timeout
        while time.time() < deadline:
            line = self._proc.stdout.readline()
            if not line:
                raise ConnectionError(f"MCP server closed (method={method})")
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue
            if j.get("id") == rid and ("result" in j or "error" in j):
                if "error" in j:
                    raise RuntimeError(f"MCP error {method}: {j['error']}")
                return j["result"]
            if j.get("id") is not None and j.get("method"):  # server->client request
                if j.get("method") == "ping":
                    self._mcp_respond(j["id"], {})
                else:
                    self._mcp_respond(j["id"], error={"code": -32601, "message": "not supported"})
            # notifications ignored
        raise TimeoutError(f"MCP timeout {method}")

    def _mcp_notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _mcp_respond(self, rid, result=None, error=None):
        msg = {"jsonrpc": "2.0", "id": rid}
        if error:
            msg["error"] = error
        else:
            msg["result"] = result
        self._proc.stdin.write(json.dumps(msg) + "\n")
        self._proc.stdin.flush()

    def _start_mcp(self):
        env = dict(os.environ)
        env.setdefault("BLENDER_MCP_HOST", ADDON_HOST)
        env.setdefault("BLENDER_MCP_PORT", str(ADDON_PORT))
        self._proc = subprocess.Popen(
            ["uv", "--directory", MCP_DIR, "run", "blender-mcp"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            text=True, env=env, cwd=MCP_DIR)
        init = self._mcp_request("initialize",
                                 {"protocolVersion": PROTO_VERSION, "capabilities": {},
                                  "clientInfo": {"name": "lukesdevlab-bench", "version": "1.0"}},
                                 timeout=120)
        self._mcp_notify("notifications/initialized")
        self.tools = self._mcp_request("tools/list", {}).get("tools", [])
        self.mode = "mcp"
        self.log(f"MCP mode: server={init.get('serverInfo', {}).get('name')} "
                 f"v{init.get('serverInfo', {}).get('version')}, {len(self.tools)} tools")

    def _start_tcp(self):
        self.mode = "tcp"
        self.tools = [{"name": n, "description": d, "inputSchema": s}
                      for n, d, s, _, _ in FALLBACK_TOOLS]
        self.log(f"TCP fallback mode: {len(self.tools)} tools (no api-docs tools)")

    # ---------- lifecycle ----------
    def start(self):
        reachable = self.probe(force=True).get("reachable")
        if not reachable:
            raise ConnectionError(f"Blender add-on not reachable at {ADDON_HOST}:{ADDON_PORT} "
                                  "(is Blender running with the MCP add-on server started?)")
        if self._prefer_mcp:
            try:
                self._start_mcp()
                return self
            except Exception as e:
                self.log("MCP start failed, falling back to TCP:", e)
                try:
                    if self._proc:
                        self._proc.kill()
                except Exception:
                    pass
                self._proc = None
        self._start_tcp()
        return self

    def close(self):
        if self._proc:
            try:
                self._proc.stdin.close()
            except Exception:
                pass
            try:
                self._proc.terminate()
                self._proc.wait(timeout=10)
            except Exception:
                try:
                    self._proc.kill()
                except Exception:
                    pass
            self._proc = None

    # ---------- tool surface ----------
    def openai_tools(self):
        out = []
        for t in self.tools:
            out.append({"type": "function", "function": {
                "name": t["name"], "description": t.get("description", ""),
                "parameters": t.get("inputSchema") or {"type": "object", "properties": {}}}})
        return out

    def _save_image(self, b64, tag):
        if not self.save_dir:
            return None
        os.makedirs(self.save_dir, exist_ok=True)
        self._shot_n += 1
        p = os.path.join(self.save_dir, f"{int(time.time())}_{self._shot_n:03d}_{tag}.png")
        try:
            open(p, "wb").write(base64.b64decode(b64))
            return p
        except Exception:
            return None

    def call(self, name, arguments):
        """Run one tool; returns list of OpenAI content parts (text + optional image_url)."""
        args = arguments or {}
        image_b64 = None
        try:
            if self.mode == "mcp":
                res = self._mcp_request("tools/call",
                                        {"name": name, "arguments": args},
                                        timeout=TOOL_CALL_TIMEOUT)
                parts, texts = [], []
                for c in (res.get("content") or []):
                    ctype = c.get("type")
                    if ctype == "text":
                        texts.append(c.get("text", ""))
                    elif ctype == "image":
                        image_b64 = c.get("data")
                    elif ctype == "resource":
                        texts.append(json.dumps(c.get("resource", {}))[:4000])
                if res.get("isError"):
                    texts = ["ERROR: " + " ".join(texts)]
                text = "\n".join(texts) if texts else "ok"
                if image_b64:
                    saved = self._save_image(image_b64, name.replace("/", "_"))
                    note = f" [saved: {saved}]" if saved else ""
                    text = (text or "screenshot") + note
            else:
                entry = next((e for e in FALLBACK_TOOLS if e[0] == name), None)
                if entry is None:
                    raise RuntimeError(f"tool {name} not available in TCP fallback mode")
                if name == "execute_blender_code":
                    resp = _tcp_send(args.get("code", ""))
                else:
                    stem, pnames = entry[3], entry[4]
                    pnames = next(pn for n, _, _, _, pn in FALLBACK_TOOLS if n == name)
                    params = {k: args.get(k) for k in pnames} if pnames else None
                    resp = _tcp_send(_toolcode(stem, params))
                if resp.get("status") != "ok":
                    text = f"ERROR: {resp.get('message', resp)}"
                else:
                    result = resp.get("result")
                    text = json.dumps(result) if not isinstance(result, str) else result
                    if isinstance(result, dict) and result.get("image_base64"):
                        image_b64 = result.pop("image_base64")
                        text = json.dumps(result)
                        saved = self._save_image(image_b64, name)
                        if saved:
                            text += f" [saved: {saved}]"
        except Exception as e:
            text = f"ERROR: {type(e).__name__}: {str(e)[:400]}"
        parts = [{"type": "text", "text": text[:6000]}]
        if image_b64:
            parts.append({"type": "image_url",
                          "image_url": {"url": f"data:image/png;base64,{image_b64}"}})
        return parts

    # ---------- bench helpers ----------
    def reset_scene(self):
        code = ("import bpy\n"
                "for o in list(bpy.data.objects):\n"
                "    bpy.data.objects.remove(o, do_unlink=True)\n"
                "for block in (bpy.data.meshes, bpy.data.materials, bpy.data.images, bpy.data.lights, bpy.data.cameras):\n"
                "    for b in list(block):\n"
                "        if b.users == 0:\n"
                "            block.remove(b)\n"
                "result = {'objects': len(bpy.data.objects)}")
        return self.call("execute_blender_code", {"code": code})

    def save_blend(self, ws, name="session.blend"):
        path = os.path.join(ws, name)
        code = (f"import bpy\nbpy.ops.wm.save_as_mainfile(filepath={path!r}, compress=True)\n"
                f"result = {{'saved': {path!r}}}")
        return self.call("execute_blender_code", {"code": code})

    def scene_state(self):
        return self.call("execute_blender_code", {"code": _health_code()})

    # ---------- status probe ----------
    @staticmethod
    def probe(force=False, timeout=2.5):
        with _probe_lock:
            now = time.time()
            if not force and _probe_cache["data"] and now - _probe_cache["ts"] < 5:
                return _probe_cache["data"]
            data = {"reachable": False, "host": ADDON_HOST, "port": ADDON_PORT}
            try:
                r = _tcp_send(_health_code(), timeout=timeout)
                if r.get("status") == "ok":
                    data["reachable"] = True
                    res = r.get("result") or {}
                    data["objects"] = res.get("objects")
                    data["version"] = res.get("version")
                    data["file"] = res.get("file")
            except Exception as e:
                data["error"] = str(e)[:120]
            _probe_cache["ts"] = now
            _probe_cache["data"] = data
            return data


def main():
    """Smoke test: python3 blender_bridge.py [--tcp]"""
    prefer_mcp = "--tcp" not in sys.argv
    b = BlenderBridge(prefer_mcp=prefer_mcp).start()
    print("mode:", b.mode, "| tools:", [t["name"] for t in b.tools])
    state = b.scene_state()
    print("scene_state:", state[0]["text"][:300] if state else None)
    b.close()


if __name__ == "__main__":
    main()
