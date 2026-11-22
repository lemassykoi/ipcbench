import json
import os
import struct


def _glb_json(path):
    with open(path, "rb") as f:
        head = f.read(12)
        if len(head) < 12 or head[:4] != b"glTF":
            raise ValueError("not a GLB file")
        version, total = struct.unpack("<II", head[4:])
        if version != 2:
            raise ValueError("GLB version != 2")
        chunk_len, chunk_type = struct.unpack("<II", f.read(8))
        if chunk_type != 0x4E4F534A:
            raise ValueError("no JSON chunk")
        return json.loads(f.read(chunk_len).decode("utf-8"))


def grade(ctx):
    issues, checks = [], {}
    ws = ctx["outdir"]
    glb = os.path.join(ws, "exports", "sensor.glb")
    blend = os.path.join(ws, "session.blend")
    checks["glb_exists"] = os.path.isfile(glb)
    if not checks["glb_exists"]:
        issues.append("exports/sensor.glb missing")
    else:
        try:
            g = _glb_json(glb)
            names = [n.get("name", "") for n in g.get("nodes", [])]
            for want in ("SensorBody", "MountBracket", "StatusLED"):
                checks["node_" + want] = want in names
                if want not in names:
                    issues.append(f"GLB node {want} missing")
            mats = g.get("materials", [])
            checks["pbr_material"] = any(
                abs(m.get("pbrMetallicRoughness", {}).get("metallicFactor", -1) - 0.2) < 0.15
                and abs(m.get("pbrMetallicRoughness", {}).get("roughnessFactor", -1) - 0.4) < 0.15
                for m in mats)
            checks["emissive_material"] = any(
                max((m.get("emissiveFactor") or [0, 0, 0])[:3]) > 0 for m in mats)
            if not checks["pbr_material"]:
                issues.append("no PBR material with metal~0.2 rough~0.4")
            if not checks["emissive_material"]:
                issues.append("no emissive material for StatusLED")
        except Exception as e:
            issues.append(f"GLB unreadable: {e}")
    checks["blend_exists"] = os.path.isfile(blend) and os.path.getsize(blend) > 10000
    if not checks["blend_exists"]:
        issues.append("session.blend missing or too small")
    calls = ctx.get("tool_calls", [])
    bcalls = [c for c in calls if c.get("tool") in (
        "execute_blender_code", "get_objects_summary", "get_object_detail_summary",
        "get_screenshot_of_area_as_image", "jump_to_view3d_object_by_name")]
    checks["blender_calls"] = len(bcalls)
    if len(bcalls) < 5:
        issues.append(f"only {len(bcalls)} Blender tool calls (need >=5)")
    shots = [f for f in ctx.get("files", []) if f.startswith("_shots/") and f.endswith(".png")]
    checks["screenshots"] = len(shots)
    if not shots:
        issues.append("no screenshot saved")
    final = ""
    if os.path.isfile(os.path.join(ws, "FINAL.md")):
        final = open(os.path.join(ws, "FINAL.md"), errors="replace").read()
    checks["final_lists_files"] = ".glb" in final and ".blend" in final
    if not checks["final_lists_files"]:
        issues.append("FINAL.md does not list both .glb and .blend")
    return {"pass": not issues, "issues": issues, "checks": checks, "score": sum(checks.values())}
