"""The turntable's speed on the panel (Joel, 2026-10-28): 33 plays Spotify on the JBL as it is, 45 sends Soloist's
streams through the "jbl45" pitch filter (soloist/jbl45.conf, hallpanel-jbl45.service), a real 45's pitch at the same
tempo. It moves the streams with pw-metadata's target.object; without the filter, or with no Spotify playing, it
stays at 33. Home Assistant asks for it (POST /speed) and sets 33 again at the next song, a pause or a stop.
"""
import json
import subprocess

NORMAL, FAST = "jbl", "jbl45"
STREAM = "spotify"                    # Soloist's stream nodes


def _dump():
    result = subprocess.run(["pw-dump"], capture_output=True, text=True, timeout=5, check=True)
    return json.loads(result.stdout or "[]")


def _nodes(dump):
    out = {}
    for obj in dump:
        props = ((obj.get("info") or {}).get("props") or {}) if obj.get("type") == "PipeWire:Interface:Node" else {}
        if props.get("node.name"):
            out.setdefault(props["node.name"], []).append(obj["id"])
    return out


def state(dump=None):
    """{"rpm": 33 or 45, "available": the filter runs, "streams": Soloist's stream count}."""
    dump = _dump() if dump is None else dump
    nodes = _nodes(dump)
    fast = set(nodes.get(FAST, []))
    streams = set(nodes.get(STREAM, []))
    linked = any(obj.get("type") == "PipeWire:Interface:Link" and (obj.get("info") or {}).get("output-node-id") in streams
                 and (obj.get("info") or {}).get("input-node-id") in fast for obj in dump)
    return {"rpm": 45 if linked else 33, "available": bool(fast), "streams": len(streams)}


def set_speed(rpm):
    """Move Soloist's streams to 45 or back to 33; returns state() afterwards, with "error" when 45 was refused."""
    if rpm not in (33, 45):
        raise ValueError("rpm is 33 or 45")
    dump = _dump()
    nodes = _nodes(dump)
    if rpm == 45 and not nodes.get(FAST):
        return {**state(dump), "error": "Pitchfiltret körs inte"}
    if not nodes.get(STREAM):
        return {**state(dump), **({"error": "Spotify spelar inte"} if rpm == 45 else {})}
    target = FAST if rpm == 45 else NORMAL
    for node in nodes[STREAM]:
        subprocess.run(["pw-metadata", str(node), "target.object", target], capture_output=True, timeout=5, check=True)
    import time
    time.sleep(0.4)                   # WirePlumber relinks
    return state()
