#!/usr/bin/env python3
"""Where the time goes in a "Hey Jarvis" turn: one line per turn, read-only.

Joins, per turn, Home Assistant's Assist pipeline debug events (the last 10 runs since HA
started), the hallpanel server's journal and its history database on Jarvis, and, when given,
a saved ESPHome log of the panel. Nothing is changed anywhere and nothing paid is called.

  .venv/bin/python tools/voice_latency.py                  the last 10 turns of the past 24 h
  .venv/bin/python tools/voice_latency.py --since 3d --turns 50
  .venv/bin/python tools/voice_latency.py --json           the joined records with every stamp
  .venv/bin/python tools/voice_latency.py --panel-log "$TMPDIR/hallpanel-logs/net-093000.log"

Clocks. Home Assistant and the server run on Jarvis, so their stamps share Jarvis's clock:
microseconds from journald and from HA's event timestamps, whole seconds in the history
database. A panel log is stamped by the Mac as each line arrives over Wi-Fi; the panel's own
`latency` line is a duration measured on the panel. Nothing here is finger-to-ear: the first
audio byte on the server is not the first sound in the hallway, and the end of Joel's speech is
not stamped anywhere yet (see docs/voice-latency.md).
"""
import argparse
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import re
import shlex
import statistics
import subprocess
import sys
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
TZ = ZoneInfo("Europe/Stockholm")
HOST = "user@jarvis.local"
PIPELINE = "Jarvis"
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "IgnoreUnknown=UseKeychain",
       "-o", "UseKeychain=yes"]

CLOCKS = {
    "jarvis": "Jarvis's system clock: journald stamps (µs) of the hallpanel server, Home Assistant's pipeline "
              "event timestamps (µs, HA runs in a container on Jarvis), history database rows (whole seconds).",
    "mac": "The Mac's clock when a panel log line arrived over Wi-Fi (esphome logs); jitter of some 10 ms.",
    "panel": "The panel's own clock: the `latency` line is a duration it measured itself (STT text -> announcing).",
}

# Runs ON Jarvis through ssh with python3 -I, stdlib only. Reads the HA token from its file there, uses it for
# the local websocket login and never prints it. Every source is read-only. argv: since (UTC ISO), pipeline.
REMOTE_SCRIPT = r'''
import base64, hashlib, json, os, re, socket, sqlite3, struct, subprocess, sys
from datetime import datetime, timezone
from pathlib import Path

SINCE, PIPELINE = sys.argv[1], sys.argv[2]
since_dt = datetime.fromisoformat(SINCE)
out = {"generated": datetime.now(timezone.utc).isoformat(timespec="seconds"), "since": SINCE}


def section(name, fn):
    try:
        out[name] = fn()
    except Exception as e:                      # one missing source must not hide the others
        out[name] = {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def journal():
    cmd = ["journalctl", "--user-unit", "hallpanel", "--since", f"@{int(since_dt.timestamp())}", "-o", "json",
           "--no-pager"]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode:
        raise RuntimeError(proc.stderr.strip()[:200] or f"journalctl exited {proc.returncode}")
    entries = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        msg = d.get("MESSAGE")
        if isinstance(msg, list):
            msg = bytes(msg).decode(errors="replace")
        if not isinstance(msg, str) or '"GET /' in msg:
            continue
        entries.append({"us": int(d["__REALTIME_TIMESTAMP"]), "msg": msg[:400]})
    return {"entries": entries}


def history():
    db = Path("~/.local/share/hallpanel/history.db").expanduser()
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    have = [r[1] for r in con.execute("PRAGMA table_info(messages)")]
    want = ["id", "ts", "conversation_id", "role", "text", "ok", "brain", "first_token_ms", "total_ms", "tokens_in",
            "tokens_out", "cost_usd", "tools", "error", "tokens_cached", "tokens_cache_write", "steps",
            "agent_queue_ms", "request_to_first_text_ms"]
    cols = [c for c in want if c in have]
    rows = con.execute(f"SELECT {', '.join(cols)} FROM messages WHERE ts >= ? ORDER BY id",
                       (since_dt.astimezone(timezone.utc).isoformat(timespec="seconds"),)).fetchall()
    return {"columns": cols, "rows": [list(r) for r in rows]}


def ha_log():
    cmd = ["docker", "logs", "homeassistant", "--since", since_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    keep = re.compile(r"jarvis_agent|First TTS audio ready|assist_pipeline|esphome\.manager\] Hallpanel")
    lines = []
    for line in (proc.stdout + proc.stderr).splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line)
        if keep.search(line):
            lines.append(line[:300])
    return {"lines": lines}


class WebSocket:
    GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

    def __init__(self, host, port, path):
        self.s = socket.create_connection((host, port), timeout=15)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("closed during the websocket handshake")
            buf += chunk
        head, self.buf = buf.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n")[0]:
            raise ConnectionError("websocket refused: " + head.split(b"\r\n")[0].decode(errors="replace"))
        accept = base64.b64encode(hashlib.sha1((key + self.GUID).encode()).digest()).decode()
        if accept.encode() not in head:
            raise ConnectionError("bad Sec-WebSocket-Accept")

    def _take(self, n):
        while len(self.buf) < n:
            chunk = self.s.recv(1 << 16)
            if not chunk:
                raise ConnectionError("websocket closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _send(self, opcode, data):
        mask, n = os.urandom(4), len(data)
        head = bytes([0x80 | opcode])
        if n < 126:
            head += bytes([0x80 | n])
        elif n < 65536:
            head += bytes([0xFE]) + struct.pack(">H", n)
        else:
            head += bytes([0xFF]) + struct.pack(">Q", n)
        self.s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send(self, obj):
        self._send(0x1, json.dumps(obj).encode())

    def recv(self):
        parts = []
        while True:
            b0, b1 = self._take(2)
            opcode, fin, n = b0 & 0x0F, b0 & 0x80, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._take(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._take(8))[0]
            mask = self._take(4) if b1 & 0x80 else None
            payload = self._take(n)
            if mask:
                payload = bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
            if opcode == 0x8:
                raise ConnectionError("websocket closed by Home Assistant")
            if opcode == 0x9:
                self._send(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            parts.append(payload)
            if fin:
                return json.loads(b"".join(parts).decode())


def redact(obj):
    if isinstance(obj, dict):
        return {k: ("<redacted>" if k in ("url", "token", "access_token", "stt_binary_handler_id") else redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [redact(v) for v in obj]
    return obj


def ha_debug():
    ws = WebSocket("127.0.0.1", 8123, "/api/websocket")
    if ws.recv().get("type") != "auth_required":
        raise RuntimeError("unexpected websocket greeting")
    ws.send({"type": "auth", "access_token": Path("~/.config/homeassistant/token").expanduser().read_text().strip()})
    auth = ws.recv()
    if auth.get("type") != "auth_ok":
        raise RuntimeError("Home Assistant refused the token")
    seq = [0]

    def call(payload):
        seq[0] += 1
        ws.send({"id": seq[0], **payload})
        while True:
            msg = ws.recv()
            if msg.get("id") == seq[0] and msg.get("type") == "result":
                if not msg.get("success"):
                    raise RuntimeError(f"{payload['type']} failed: {(msg.get('error') or {}).get('code')}")
                return msg["result"]

    result = {"ha_version": auth.get("ha_version")}
    pipelines = call({"type": "assist_pipeline/pipeline/list"})["pipelines"]
    chosen = next((p for p in pipelines if p.get("name") == PIPELINE), None)
    if chosen is None:
        result["error"] = f"no Assist pipeline named {PIPELINE!r}"
        return result
    result["pipeline"] = {"id": chosen["id"], "name": chosen["name"], "conversation_engine": chosen.get("conversation_engine"),
                          "stt_engine": chosen.get("stt_engine"), "tts_engine": chosen.get("tts_engine")}
    result["runs"] = []
    for run in call({"type": "assist_pipeline/pipeline_debug/list", "pipeline_id": chosen["id"]}).get("pipeline_runs", []):
        events = call({"type": "assist_pipeline/pipeline_debug/get", "pipeline_id": chosen["id"],
                       "pipeline_run_id": run["pipeline_run_id"]}).get("events", [])
        result["runs"].append({"pipeline_run_id": run["pipeline_run_id"], "timestamp": run.get("timestamp"),
                               "events": redact(events)})
    return result


section("journal", journal)
section("history", history)
section("ha_log", ha_log)
section("ha", ha_debug)
print(json.dumps(out, ensure_ascii=False))
'''

# ---------------------------------------------------------------------------------------------- parsing

REQUEST = re.compile(r'^\S+ - - \[[^\]]*\] "(?P<method>[A-Z]+) (?P<path>\S+) HTTP/[\d.]+" (?P<code>\d{3}) ')
# log_line() in server/hallpanel_server.py, prefixed by log_message() with the client and a date.
AGENT = re.compile(r"^(?:\S+ - - \[[^\]]*\] )?(?P<brain>[^:\"]+?): first word (?P<first>\S+) ms, total (?P<total>\S+) ms, "
                   r"tokens (?P<tin>\S+)/(?P<tout>\S+) \(cached (?P<cached>\S+), cache write (?P<cw>\S+)\), "
                   r"(?:(?P<steps>\S+) rounds, )?\$(?P<cost>\S+), (?P<tools>.*)$")
TOOL = re.compile(r"^(?P<name>.+?) (?P<ms>-?\d+) ms$")
FAILED = re.compile(r"^(?:\S+ - - \[[^\]]*\] )?agent failed: (?P<error>.*)$")
HA_LOG = re.compile(r"^(?P<t>\d{4}-\d\d-\d\d \d\d:\d\d:\d\d\.\d{3}) (?P<level>\w+) \((?P<thread>[^)]*)\) \[(?P<logger>[^\]]+)\] (?P<msg>.*)$")
FIRST_TTS = re.compile(r"First TTS audio ready in (?P<ms>\d+) ms")
WARM_REJECTED = re.compile(r"Service call jarvis_agent\.warm.*rejected")
PANEL_LINE = re.compile(r"^\[(?P<t>\d\d:\d\d:\d\d\.\d{3})\]\[(?P<level>[A-Z])\]\[(?P<tag>[^\]:]+):\d+\]: ?(?P<msg>.*)$")
PANEL_LATENCY = re.compile(r"STT text -> player announcing (?P<ms>\d+) ms")


def number(text):
    """An int, float or None from a log field such as '1191', '0.037475', 'None'."""
    if text in (None, "None", "null", "-"):
        return None
    try:
        return int(text)
    except ValueError:
        try:
            return float(text)
        except ValueError:
            return None


def parse_tools(text):
    text = text.strip()
    if not text or text == "no tools":
        return []
    tools = []
    for part in text.split(", "):
        m = TOOL.match(part.strip())
        tools.append({"name": m.group("name"), "ms": int(m.group("ms"))} if m else {"name": part.strip(), "ms": None})
    return tools


def parse_agent_line(message):
    """The per-reply summary the server logs, or None."""
    m = AGENT.match(message)
    if not m:
        return None
    return {"brain": m.group("brain").strip(), "first_token_ms": number(m.group("first")), "total_ms": number(m.group("total")),
            "tokens_in": number(m.group("tin")), "tokens_out": number(m.group("tout")), "tokens_cached": number(m.group("cached")),
            "tokens_cache_write": number(m.group("cw")), "steps": number(m.group("steps")), "cost_usd": number(m.group("cost")),
            "tools": parse_tools(m.group("tools"))}


def parse_journal(entries):
    """Journald entries ({us, msg}) of the hallpanel unit -> typed events, oldest first (Jarvis clock, seconds)."""
    events = []
    for entry in sorted(entries, key=lambda e: e["us"]):
        t, msg = entry["us"] / 1e6, entry["msg"]
        if m := REQUEST.match(msg):
            if m.group("method") == "GET":
                continue                                # the panel's polling; the collector drops these already
            events.append({"t": t, "kind": "request", "method": m.group("method"), "path": m.group("path"), "code": int(m.group("code"))})
        elif agent := parse_agent_line(msg):
            events.append({"t": t, "kind": "reply", **agent})
        elif m := FAILED.match(msg):
            events.append({"t": t, "kind": "failed", "error": m.group("error")[:200]})
        elif "transcribe unavailable:" in msg:
            events.append({"t": t, "kind": "stt_reason", "reason": msg.split("transcribe unavailable:", 1)[1].strip()[:160]})
        elif "warm-up:" in msg:
            events.append({"t": t, "kind": "warmup", "text": msg.split("warm-up:", 1)[1].strip()[:200]})
        elif "warm-up failed" in msg:
            events.append({"t": t, "kind": "warmup_failed"})
        elif "text to speech failed" in msg:
            events.append({"t": t, "kind": "tts_failed"})
        elif "Started hallpanel.service" in msg:
            events.append({"t": t, "kind": "restart"})
    return events


def server_turns(events):
    """Group the server's journal events into turns.

    With streaming, the "POST /converse" line is written when the response headers go out, before the agent
    runs, so it marks the request's arrival. Without streaming (/voice, or /converse without stream) the line
    comes after the reply, just after the agent's summary line; such a turn has no arrival stamp.
    """
    turns, loose_reply, pending_transcribe, stt_failures, warms = [], None, None, [], []
    stt_reason = None                                   # the server logs its reason just before the 503 line
    for e in events:
        if e["kind"] == "stt_reason":
            stt_reason = e
            continue
        if e["kind"] == "request" and e["method"] == "POST":
            if e["path"] == "/transcribe":
                if e["code"] == 200:
                    pending_transcribe = e["t"]
                else:
                    failure = {"t": e["t"], "code": e["code"]}
                    if stt_reason is not None and 0 <= e["t"] - stt_reason["t"] <= 2:
                        failure["reason"] = stt_reason["reason"]
                    stt_failures.append(failure)
                stt_reason = None
            elif e["path"] in ("/converse", "/voice") and e["code"] == 200:
                turn = {"converse": e["t"], "transcribe": None, "reply_done": None, "reply": None, "error": None,
                        "speaks": [], "streamed": True, "path": e["path"]}
                if pending_transcribe is not None and e["t"] - pending_transcribe <= 3:
                    turn["transcribe"] = pending_transcribe
                pending_transcribe = None
                if loose_reply is not None and e["t"] - loose_reply["t"] <= 2:
                    turn.update(converse=None, request_done=e["t"], streamed=False, reply_done=loose_reply["t"],
                                reply={k: v for k, v in loose_reply.items() if k not in ("t", "kind")})
                    loose_reply = None
                turns.append(turn)
            elif e["path"] == "/speak" and e["code"] == 200:
                if turns and (turns[-1]["reply_done"] is None or e["t"] <= turns[-1]["reply_done"] + 60):
                    turns[-1]["speaks"].append(e["t"])
            elif e["path"] == "/warm":
                warms.append(e["t"])
        elif e["kind"] == "reply":
            open_turn = next((t for t in reversed(turns) if t["reply_done"] is None and t["error"] is None
                              and t["converse"] is not None and e["t"] >= t["converse"]), None)
            if open_turn is None:
                loose_reply = e
            else:
                open_turn["reply_done"] = e["t"]
                open_turn["reply"] = {k: v for k, v in e.items() if k not in ("t", "kind")}
        elif e["kind"] == "failed":
            open_turn = next((t for t in reversed(turns) if t["reply_done"] is None and t["error"] is None), None)
            if open_turn is not None:
                open_turn["error"] = e["error"]
                open_turn["reply_done"] = e["t"]
    return {"turns": turns, "stt_failures": stt_failures, "warms": warms,
            "warmups": [e for e in events if e["kind"] in ("warmup", "warmup_failed")],
            "restarts": [e["t"] for e in events if e["kind"] == "restart"]}


def iso_seconds(text):
    return datetime.fromisoformat(text).timestamp()


def history_turns(history):
    """Pairs of (user, jarvis) rows from the history database -> turns with the reply's metadata."""
    cols = history.get("columns") or []
    rows = [dict(zip(cols, r)) for r in history.get("rows") or []]
    turns, pending = [], None
    for row in rows:
        if str(row.get("brain") or "").startswith("notice") or row.get("conversation_id") == "notice":
            continue                                    # reminders the server speaks on its own are not turns
        if row["role"] == "user":
            if pending is not None:
                turns.append(pending)
            pending = {"user_ts": iso_seconds(row["ts"]), "text": row["text"], "conversation_id": row.get("conversation_id"),
                       "reply_ts": None, "reply_text": None, "ok": None, "meta": None}
        elif row["role"] == "jarvis" and pending is not None and pending["reply_ts"] is None:
            tools = row.get("tools")
            try:
                tools = json.loads(tools) if isinstance(tools, str) else tools
            except ValueError:
                tools = None
            meta = {k: row.get(k) for k in ("brain", "first_token_ms", "total_ms", "tokens_in", "tokens_out", "cost_usd",
                                             "error", "tokens_cached", "tokens_cache_write", "steps", "agent_queue_ms",
                                             "request_to_first_text_ms")}
            meta["tools"] = tools
            pending.update(reply_ts=iso_seconds(row["ts"]), reply_text=row["text"], ok=bool(row.get("ok", 1)), meta=meta)
            turns.append(pending)
            pending = None
    if pending is not None:
        turns.append(pending)
    return turns


def ha_runs(ha):
    """Home Assistant's pipeline debug runs -> the stamps that matter (Jarvis clock, seconds)."""
    runs = []
    for run in ha.get("runs") or []:
        r = {"run_id": run.get("pipeline_run_id"), "run_start": None, "stt_start": None, "stt_end": None, "text": None,
             "intent_start": None, "agent_called": None, "first_content": None, "tts_start_streaming": None,
             "tool_calls": [], "intent_end": None, "processed_locally": None, "reply_text": None, "tts_start": None,
             "tts_end": None, "run_end": None, "error": None, "conversation_id": None, "satellite": None, "deltas": 0}
        for e in run.get("events") or []:
            kind, data, t = e.get("type"), e.get("data") or {}, iso_seconds(e["timestamp"])
            if kind == "run-start":
                r.update(run_start=t, conversation_id=data.get("conversation_id"), satellite=data.get("satellite_id"))
            elif kind == "stt-start":
                r["stt_start"] = t
            elif kind == "stt-end":
                r.update(stt_end=t, text=(data.get("stt_output") or {}).get("text"))
            elif kind == "intent-start":
                r["intent_start"] = t
                r["text"] = r["text"] or data.get("intent_input")
            elif kind == "intent-progress":
                delta = data.get("chat_log_delta") or {}
                if delta:
                    r["deltas"] += 1
                if delta.get("content") and r["first_content"] is None:
                    r["first_content"] = t
                if delta.get("role") == "assistant" and r["agent_called"] is None and not delta.get("content"):
                    r["agent_called"] = t
                if delta.get("tool_calls"):
                    r["tool_calls"].append(t)
                if data.get("tts_start_streaming") and r["tts_start_streaming"] is None:
                    r["tts_start_streaming"] = t
            elif kind == "intent-end":
                speech = (((data.get("intent_output") or {}).get("response") or {}).get("speech") or {}).get("plain") or {}
                r.update(intent_end=t, processed_locally=data.get("processed_locally"), reply_text=speech.get("speech"))
            elif kind == "tts-start":
                r["tts_start"] = t
            elif kind == "tts-end":
                r["tts_end"] = t
            elif kind == "run-end":
                r["run_end"] = t
            elif kind == "error":
                r["error"] = f"{data.get('code')}: {data.get('message')}"[:120]
        runs.append(r)
    return runs


def parse_ha_log(lines):
    """Home Assistant's own log lines (local time, Europe/Stockholm) that touch Jarvis."""
    out = []
    for line in lines or []:
        m = HA_LOG.match(re.sub(r"\x1b\[[0-9;]*m", "", line).strip())
        if not m:
            continue
        t = datetime.strptime(m.group("t"), "%Y-%m-%d %H:%M:%S.%f").replace(tzinfo=TZ).timestamp()
        msg = m.group("msg")
        if WARM_REJECTED.search(msg):
            out.append({"t": t, "kind": "warm_rejected"})
        elif tts := FIRST_TTS.search(msg):
            out.append({"t": t, "kind": "first_tts", "ms": int(tts.group("ms"))})
    return out


def parse_panel_log(text, log_end):
    """A saved `esphome logs` capture -> runs with the panel's stamps (Mac arrival clock, seconds since the epoch).

    Lines carry only a time of day; the date is the capture's end (the file's modification time), so a capture
    across midnight puts earlier-looking times on the previous day. The chime after the wake word is also an
    announcement, so only the ANNOUNCING state after the recognised text counts as the reply.
    """
    end = datetime.fromtimestamp(log_end, TZ)
    runs, cur, wake = [], None, None

    def stamp(hms):
        tod = datetime.strptime(hms, "%H:%M:%S.%f")
        t = end.replace(hour=tod.hour, minute=tod.minute, second=tod.second, microsecond=tod.microsecond)
        if t > end + timedelta(seconds=60):
            t -= timedelta(days=1)
        return t.timestamp()

    for line in text.splitlines():
        line = re.sub(r"\x1b\[[0-9;]*m", "", line).strip()
        m = PANEL_LINE.match(line)
        if not m:
            continue
        t, tag, msg = stamp(m.group("t")), m.group("tag"), m.group("msg")
        if tag == "micro_wake_word" and "Detected" in msg:
            wake = t
        elif tag == "voice_assistant" and "Requesting start" in msg:
            cur = {"start": wake if wake is not None and t - wake <= 3 else t, "wake": wake, "stt_end": None, "text": None,
                   "intent_start": None, "url": None, "announcing": None, "decoded": None, "finished": None,
                   "reply_text": None, "latency_ms": None, "pipeline_end": None}
            runs.append(cur)
            wake = None
        elif cur is None:
            continue
        elif tag == "voice_assistant" and "Speech recognised as:" in msg:
            cur["stt_end"], cur["text"] = t, msg.split("as:", 1)[1].strip().strip('"')
        elif tag == "voice_assistant" and "Intent started" in msg:
            cur["intent_start"] = t
        elif tag == "voice_assistant" and "Intent progress" in msg and cur["url"] is None:
            cur["url"] = t
        elif tag == "speaker_media_player" and "State changed to ANNOUNCING" in msg and cur["stt_end"] and cur["announcing"] is None:
            cur["announcing"] = t
        elif tag.startswith("speaker_media_player") and "Decoded audio" in msg and cur["announcing"] and cur["decoded"] is None:
            cur["decoded"] = t
        elif tag == "voice_assistant" and "Response:" in msg and cur["reply_text"] is None:
            cur["reply_text"] = msg.split("Response:", 1)[1].strip().strip('"')
        elif tag == "latency" and (lat := PANEL_LATENCY.search(msg)):
            cur["latency_ms"] = int(lat.group("ms"))
        elif tag == "voice_assistant" and "Announcement finished playing" in msg:
            cur["finished"] = t
        elif tag == "voice_assistant" and "Assist Pipeline ended" in msg:
            cur["pipeline_end"] = t
    return runs


# ------------------------------------------------------------------------------------------ correlation

def ms(a, b):
    """b - a in whole ms, or None when either stamp is missing."""
    return None if a is None or b is None else round((b - a) * 1000)


def nearest(candidates, key, target, tolerance):
    best = None
    for c in candidates:
        if c.get(key) is None or c.get("_used"):
            continue
        gap = abs(c[key] - target)
        if gap <= tolerance and (best is None or gap < abs(best[key] - target)):
            best = c
    return best


def cache_status(meta):
    if not meta or meta.get("tokens_cached") is None and meta.get("tokens_cache_write") is None:
        return None
    cached, written = meta.get("tokens_cached") or 0, meta.get("tokens_cache_write") or 0
    if cached and written:
        return "hit+write"
    if cached:
        return "hit"
    if written:
        return "cold"
    return "none"


def join_turns(server, db_turns, runs, ha_log, panel_runs=None):
    """One record per turn from whatever sources have it. Server turns are the spine when present."""
    records = []
    for t in server["turns"]:
        records.append({"server": t, "db": None, "ha": None, "panel": None})
    for d in db_turns:
        rec = None
        for r in records:
            s = r["server"]
            if s is None:
                continue
            anchor = s["converse"] if s["converse"] is not None else arrival_estimate(s)
            if r["db"] is None and anchor is not None and -1.5 <= anchor - d["user_ts"] <= 2.5:
                if s["reply"] and d["meta"] and s["reply"].get("total_ms") not in (None, d["meta"].get("total_ms")):
                    continue
                rec = r
                break
        if rec is None:
            records.append({"server": None, "db": d, "ha": None, "panel": None})
        else:
            rec["db"] = d
    for run in runs:
        rec = None
        if run["stt_end"] is not None and run["processed_locally"] is not True:
            for r in sorted(records, key=lambda r: abs((anchor_of(r) or 0) - run["stt_end"])):
                anchor = anchor_of(r)
                if r["ha"] is None and anchor is not None and abs(anchor - run["stt_end"]) <= 3:
                    text = r["db"]["text"] if r["db"] else None
                    if text is None or run["text"] is None or text.strip() == run["text"].strip():
                        rec = r
                        break
        if rec is None:
            records.append({"server": None, "db": None, "ha": run, "panel": None})
        else:
            rec["ha"] = run
    for p in panel_runs or []:
        if p["stt_end"] is None:
            continue
        rec = None
        for r in records:
            if r["panel"] is not None:
                continue
            text = question_of(r)
            anchor = anchor_of(r)
            if text and p["text"] and text.strip() == p["text"].strip() and anchor is not None and abs(anchor - p["stt_end"]) <= 600:
                rec = r
                break
        if rec is None:
            close = [r for r in records if r["panel"] is None and anchor_of(r) is not None and abs(anchor_of(r) - p["stt_end"]) <= 30]
            if len(close) == 1:
                rec = close[0]
        if rec is not None:
            rec["panel"] = p
    for failure in server["stt_failures"]:
        rec = next((r for r in records if r["ha"] and r["ha"]["stt_end"] is None and r["ha"]["run_end"] is not None
                    and abs(r["ha"]["run_end"] - failure["t"]) <= 3 and "stt_failure" not in r), None)
        if rec is None:
            rec = {"server": None, "db": None, "ha": None, "panel": None}
            records.append(rec)
        rec["stt_failure"] = failure
    records.sort(key=lambda r: anchor_of(r) or 0)
    return [build_record(r, server, ha_log) for r in records]


def arrival_estimate(s):
    """A non-streamed request's arrival: its line is logged after the reply, total_ms earlier is close enough to match."""
    if s.get("request_done") is None:
        return None
    total = (s["reply"] or {}).get("total_ms") if s.get("reply") else None
    return s["request_done"] - (total or 0) / 1000


def anchor_of(rec):
    """The turn's text-ready moment: HA stt-end, else the server's /transcribe, else /converse, else the user row."""
    if rec["ha"] and rec["ha"]["stt_end"] is not None:
        return rec["ha"]["stt_end"]
    s = rec["server"]
    if s:
        for key in ("transcribe", "converse", "request_done", "reply_done"):
            if s.get(key) is not None:
                return s[key]
    if rec["db"]:
        return rec["db"]["user_ts"]
    if rec["ha"]:
        for key in ("intent_start", "run_end", "run_start"):
            if rec["ha"].get(key) is not None:
                return rec["ha"][key]
    if rec.get("stt_failure"):
        return rec["stt_failure"]["t"]
    return None


def question_of(rec):
    if rec["db"] and rec["db"]["text"]:
        return rec["db"]["text"]
    if rec["ha"] and rec["ha"]["text"]:
        return rec["ha"]["text"]
    return None


def warm_status(anchor_start, anchor_end, server, ha_log):
    """Did the wake word's /warm reach the server before this question?"""
    if anchor_end is None:
        return None
    lo = (anchor_start if anchor_start is not None else anchor_end - 20) - 5
    if any(lo <= w <= anchor_end for w in server["warms"]):
        return "ok"
    if any(e["kind"] == "warm_rejected" and lo <= e["t"] <= anchor_end for e in ha_log):
        return "rejected"
    return "none"


def build_record(rec, server, ha_log):
    s, d, h, p = rec["server"], rec["db"], rec["ha"], rec["panel"]
    meta = (d["meta"] if d and d["meta"] else None) or (s["reply"] if s and s["reply"] else None)
    stt_start = h["stt_start"] if h else None
    stt_end = h["stt_end"] if h else None
    transcribe = s["transcribe"] if s else None
    converse = s["converse"] if s else None
    text_ready = next((x for x in (stt_end, transcribe, converse) if x is not None), None)
    if text_ready is None and d is not None and s is not None and not s["streamed"]:
        text_ready = d["user_ts"]                       # whole seconds, but it is the arrival of the question
    first_content = h["first_content"] if h else None
    tts_point = None
    tts_mode = None
    if h:
        if h["tts_start_streaming"] is not None:
            tts_point, tts_mode = h["tts_start_streaming"], "streamed"
        elif h["tts_start"] is not None:
            tts_point, tts_mode = h["tts_start"], "whole"
    first_speak = s["speaks"][0] if s and s["speaks"] else None
    last_speak = s["speaks"][-1] if s and s["speaks"] else None
    intent_end = h["intent_end"] if h else None
    reply_done = s["reply_done"] if s else None
    error = None
    if h and h["error"]:
        error = h["error"]
    elif s and s["error"]:
        error = "agent failed: " + s["error"]
    elif meta and meta.get("error"):
        error = str(meta["error"])[:120]
    elif rec.get("stt_failure"):
        error = f"transcribe answered HTTP {rec['stt_failure']['code']}"
        if rec["stt_failure"].get("reason"):
            error += f": {rec['stt_failure']['reason']}"
    local = bool(h and h["processed_locally"])

    first_token_ms, first_token_source = ms(stt_end, first_content), "ha"
    if first_token_ms is None and meta and meta.get("request_to_first_text_ms") is not None:
        first_token_ms, first_token_source = meta["request_to_first_text_ms"] + (ms(transcribe, converse) or 0), "server"
    elif first_token_ms is None and meta and meta.get("first_token_ms") is not None:
        first_token_ms, first_token_source = meta["first_token_ms"], "model"
    elif first_token_ms is None:
        first_token_source = None

    panel = None
    if p:
        panel = {"stt_end_mac": p["stt_end"], "announcing_mac": p["announcing"], "finished_mac": p["finished"],
                 "stt_to_announcing_ms": ms(p["stt_end"], p["announcing"]), "latency_line_ms": p["latency_ms"],
                 "url_to_announcing_ms": ms(p["url"], p["announcing"]), "played_ms": ms(p["announcing"], p["finished"]),
                 "start_to_stt_end_ms": ms(p["start"], p["stt_end"])}
        # Both legs start at the same event (the recognised text), one on Jarvis, one on the panel/Mac, so
        # their difference is how long the first audio took to become sound, within the Wi-Fi hop's jitter.
        text_to_audio = ms(text_ready, first_speak)
        if text_to_audio is not None and panel["stt_to_announcing_ms"] is not None:
            panel["first_audio_to_announcing_ms"] = panel["stt_to_announcing_ms"] - text_to_audio

    reply_total_ms, reply_total_source = ms(stt_end, intent_end), "ha"
    if reply_total_ms is None:
        reply_total_ms, reply_total_source = ms(text_ready, reply_done), "server"
    if reply_total_ms is None and meta and meta.get("total_ms") is not None:
        reply_total_ms, reply_total_source = meta["total_ms"], "agent"
    if reply_total_ms is None:
        reply_total_source = None
    record = {
        "time": text_ready if text_ready is not None else anchor_of(rec),
        "time_means": ("HA stt-end (text ready)" if stt_end is not None else "server /transcribe done" if transcribe is not None
                       else "server /converse arrived (about 0.1 s after the text)" if converse is not None
                       else "history row (whole seconds)" if text_ready is not None
                       else "server /transcribe failed" if rec.get("stt_failure") and s is None and d is None
                       else "server reply logged" if reply_done is not None
                       else "HA pipeline event" if h else "history row (whole seconds)"),
        "question": question_of(rec),
        "reply": (d["reply_text"] if d else None) or (h["reply_text"] if h else None),
        "conversation_id": (d["conversation_id"] if d else None) or (h["conversation_id"] if h else None),
        "sources": "".join(c if x else "-" for c, x in (("H", h), ("J", s or rec.get("stt_failure")), ("D", d), ("P", p))),
        "local": local,
        "error": error,
        "stamps_jarvis": {"run_start": h["run_start"] if h else None, "stt_start": stt_start, "stt_end": stt_end,
                          "transcribe_done": transcribe, "converse_request": converse, "intent_start": h["intent_start"] if h else None,
                          "agent_called": h["agent_called"] if h else None, "first_content": first_content,
                          "tool_calls": h["tool_calls"] if h else [], "tts_start_streaming": h["tts_start_streaming"] if h else None,
                          "intent_end": intent_end, "tts_start": h["tts_start"] if h else None, "reply_logged": reply_done,
                          "speaks": s["speaks"] if s else [], "run_end": h["run_end"] if h else None,
                          "history_user_row": d["user_ts"] if d else None, "history_reply_row": d["reply_ts"] if d else None},
        "deltas_ms": {
            "listen": ms(stt_start, stt_end),
            "text_to_first_token": first_token_ms,
            "first_token_to_tts": ms(first_content, tts_point),
            "tts_to_first_audio": ms(tts_point, first_speak),
            "text_to_first_audio": ms(text_ready, first_speak),
            "text_to_last_audio": ms(text_ready, last_speak),
            "reply_total": reply_total_ms,
            "ha_overhead_text_to_intent": ms(stt_end, h["intent_start"]) if h else None,
            "ha_to_server": ms(h["intent_start"], converse) if h else None,
        },
        "first_token_source": first_token_source,
        "reply_total_source": reply_total_source,
        "tts_mode": tts_mode,
        "sentences": len(s["speaks"]) if s else None,
        "model": (meta or {}).get("brain") or ("HA local" if local else None),
        "agent": {k: (meta or {}).get(k) for k in ("first_token_ms", "total_ms", "request_to_first_text_ms", "agent_queue_ms",
                                                   "steps", "tokens_in", "tokens_out", "tokens_cached", "tokens_cache_write",
                                                   "cost_usd")} if meta else None,
        "tools": (meta or {}).get("tools") if meta else None,
        "cache": cache_status(meta),
        "warm": warm_status(stt_start, text_ready, server, ha_log),
        "panel": panel,
        "streamed_request": s["streamed"] if s else None,
    }
    return record


# ------------------------------------------------------------------------------------------ rendering

def local_time(t, fmt="%H:%M:%S.%f"):
    if t is None:
        return "-"
    text = datetime.fromtimestamp(t, TZ).strftime(fmt)
    return text[:-5] if fmt.endswith("%f") else text


def iso_local(t):
    return None if t is None else datetime.fromtimestamp(t, TZ).isoformat(timespec="milliseconds")


def cell(value, money=False):
    if value is None:
        return "-"
    if money:
        return f"${value:.4f}"
    return str(value)


def early_saved_ms(tools):
    """The time lookups started while the model still wrote (the server's early_ms, since 2026-10-30) took off
    the wait for each round's tools: the slowest tool as if all started at the round's end, minus the slowest
    as they ran. An estimate that assumes the round's tools run side by side; None without early_ms."""
    rounds = {}
    for t in tools or []:
        if isinstance(t, dict) and isinstance(t.get("ms"), int):
            rounds.setdefault(t.get("round"), []).append(t)
    if not any("early_ms" in t for ts in rounds.values() for t in ts):
        return None
    return sum(max(t["ms"] for t in ts) - max(t["ms"] - t.get("early_ms", 0) for t in ts) for ts in rounds.values())


def tools_cell(record):
    if record["error"]:
        return record["error"][:40]
    if record["local"]:
        return "handled by Home Assistant"
    tools = record["tools"] or []
    if not tools:
        return "-"
    saved = early_saved_ms(tools)
    return ("" if not saved else f"early -{saved}: ") + ", ".join(f"{t.get('name')} {t.get('ms')}" if t.get("ms") is not None else str(t.get("name")) for t in tools)[:40]


COLUMNS = [
    ("time", lambda r: local_time(r["time"]), "text ready (HA stt-end), Europe/Stockholm, Jarvis's clock"),
    ("date", lambda r: local_time(r["time"], "%m-%d"), ""),
    ("src", lambda r: r["sources"], "H = HA pipeline debug, J = server journal, D = history database, P = panel log"),
    ("listen", lambda r: cell(r["deltas_ms"]["listen"]), "stt-start -> stt-end: your speaking, the configured pause, upload and transcription"),
    (">tok", lambda r: cell(r["deltas_ms"]["text_to_first_token"]) + {"ha": "", "server": "*", "model": "m", None: ""}[r["first_token_source"]],
     "text ready -> the agent's first word reaches Home Assistant (* = from the server's request, m = from the model request)"),
    (">tts", lambda r: cell(r["deltas_ms"]["first_token_to_tts"]) + ("" if r["tts_mode"] != "whole" else "w"),
     "first word -> HA starts the speech stream (first sentence, 60 characters, or text before a tool; w = spoken whole after the reply)"),
    (">audio", lambda r: cell(r["deltas_ms"]["tts_to_first_audio"]), "speech stream start -> first sentence's audio done on the server (/speak)"),
    ("text>audio", lambda r: cell(r["deltas_ms"]["text_to_first_audio"]), "text ready -> first sentence's audio done; the wait you notice, minus the pause before the text"),
    ("total", lambda r: cell(r["deltas_ms"]["reply_total"]) + {"ha": "", "server": "*", "agent": "a", None: ""}[r["reply_total_source"]],
     "text ready -> reply complete (HA intent-end; * = server's reply line, a = the agent's own total)"),
    ("panel", lambda r: cell(r["panel"]["stt_to_announcing_ms"]) if r["panel"] else "-",
     "panel log: recognised text -> player ANNOUNCING, Mac arrival clock (needs --panel-log)"),
    ("model", lambda r: (r["model"] or "-")[:27], "which brain answered"),
    ("r", lambda r: cell((r["agent"] or {}).get("steps")), "model requests for the answer"),
    ("cache", lambda r: r["cache"] or "-", "prompt cache: hit, cold (written, not read), hit+write, none"),
    ("cost", lambda r: cell((r["agent"] or {}).get("cost_usd"), money=True), "the reply's cost from the agent's usage"),
    ("warm", lambda r: r["warm"] or "-", "the wake word's /warm: ok, rejected by HA, none seen"),
    ("tools / note", tools_cell, "tools with their ms, or the error"),
    ("question", lambda r: (r["question"] or "").replace("\n", " ")[:38], "what was recognised"),
]


def median_row(records, name):
    cells = []
    for col, getter, _ in COLUMNS:
        if col in ("listen", ">tok", ">tts", ">audio", "text>audio", "total", "panel") and name == "median":
            values = []
            for r in records:
                raw = getter(r).rstrip("*wam")
                if raw not in ("-", ""):
                    values.append(int(raw))
            cells.append(str(round(statistics.median(values))) if values else "-")
        elif col == "cost":
            costs = [(r["agent"] or {}).get("cost_usd") for r in records]
            costs = [c for c in costs if c is not None]
            cells.append(f"${sum(costs):.4f}" if name == "sum" and costs else "-")
        elif col == "time":
            cells.append(name)
        else:
            cells.append("")
    return cells


def render_table(records, sources, window):
    lines = []
    if not records:
        lines.append("No Jarvis turns found in the window.")
    else:
        rows = [[getter(r) for _, getter, _ in COLUMNS] for r in records]
        if len(records) >= 2:
            rows.append(median_row(records, "median"))
            rows.append(median_row(records, "sum"))
        head = [c for c, _, _ in COLUMNS]
        widths = [max(len(h), *(len(row[i]) for row in rows)) for i, h in enumerate(head)]
        numeric = {"listen", ">tok", ">tts", ">audio", "text>audio", "total", "panel", "r", "cost"}
        fmt = lambda row: "  ".join(c.rjust(w) if h in numeric else c.ljust(w) for h, c, w in zip(head, row, widths)).rstrip()  # noqa: E731
        lines.append(fmt(head))
        for i, row in enumerate(rows):
            if i == len(records) and len(records) >= 2:
                lines.append("")
            lines.append(fmt(row))
    lines.append("")
    lines.append(f"Window {window}. Times in ms; - = unavailable (nothing is estimated).")
    for col, _, meaning in COLUMNS:
        if meaning:
            lines.append(f"  {col:<13}{meaning}")
    lines.append("")
    lines.append("Sources: " + "; ".join(sources))
    return "\n".join(lines)


def describe_sources(raw, server, db_turns, runs, ha_log, panel_path, panel_runs):
    out = []
    j = raw.get("journal") or {}
    out.append(f"journal: {j['error']}" if "error" in j else
               f"journal: {len(server['turns'])} turns, {len(server['warms'])} /warm, {len(server['restarts'])} restarts")
    hist = raw.get("history") or {}
    out.append(f"history: {hist['error']}" if "error" in hist else f"history: {len(db_turns)} turns")
    ha = raw.get("ha") or {}
    if "error" in ha:
        out.append(f"HA debug: {ha['error']}")
    else:
        matched = sum(1 for r in runs if r["stt_end"] is not None)
        out.append(f"HA {ha.get('ha_version', '?')} debug: {len(runs)} runs kept in memory ({matched} with text); only the last 10 since HA started")
    hl = raw.get("ha_log") or {}
    if "error" in hl:
        out.append(f"HA log: {hl['error']}")
    else:
        rejected = sum(1 for e in ha_log if e["kind"] == "warm_rejected")
        first_tts = sum(1 for e in ha_log if e["kind"] == "first_tts")
        out.append(f"HA log: {rejected} rejected warm calls, {first_tts} 'First TTS audio ready' lines"
                   + ("" if first_tts else " (needs logger custom_components.jarvis_agent: info)"))
    if panel_path:
        out.append(f"panel log: {len(panel_runs)} runs in {panel_path}")
    else:
        out.append("panel log: none (capture one with the hallpanel skill's panel.sh logs 60 while talking, then --panel-log FILE)")
    return out


# --------------------------------------------------------------------------------------------- driver

def parse_since(text, now=None):
    now = now or datetime.now(timezone.utc)
    m = re.fullmatch(r"(\d+)\s*([smhd])", text.strip())
    if m:
        unit = {"s": 1, "m": 60, "h": 3600, "d": 86400}[m.group(2)]
        return now - timedelta(seconds=int(m.group(1)) * unit)
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        raise SystemExit(f"--since must be like 2h, 3d, 45m or an ISO time, not {text!r}")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=TZ)
    return dt.astimezone(timezone.utc)


def fetch(host, since, pipeline=PIPELINE):
    """Run the collector on Jarvis over ssh; the token stays there. Returns the raw JSON document."""
    cmd = [*SSH, host, f"python3 -I - {shlex.quote(since.isoformat(timespec='seconds'))} {shlex.quote(pipeline)}"]
    try:
        proc = subprocess.run(cmd, input=REMOTE_SCRIPT, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        raise SystemExit("Jarvis did not answer within 120 s")
    if proc.returncode:
        raise SystemExit(f"ssh {host} failed ({proc.returncode}): {proc.stderr.strip()[:300]}")
    try:
        return json.loads(proc.stdout)
    except ValueError:
        raise SystemExit(f"unexpected output from Jarvis: {proc.stdout[:200]!r}")


def analyse(raw, panel_text=None, panel_end=None, panel_path=None):
    server = server_turns(parse_journal((raw.get("journal") or {}).get("entries") or []))
    db_turns = history_turns(raw.get("history") or {})
    runs = ha_runs(raw.get("ha") or {})
    ha_log = parse_ha_log((raw.get("ha_log") or {}).get("lines"))
    panel_runs = parse_panel_log(panel_text, panel_end) if panel_text is not None else []
    records = join_turns(server, db_turns, runs, ha_log, panel_runs)
    sources = describe_sources(raw, server, db_turns, runs, ha_log, panel_path, panel_runs)
    return records, sources


def to_json(records, sources, window):
    def convert(rec):
        out = json.loads(json.dumps(rec))
        out["time"] = iso_local(rec["time"])
        out["stamps_jarvis"] = {k: ([iso_local(x) for x in v] if isinstance(v, list) else iso_local(v))
                                for k, v in rec["stamps_jarvis"].items()}
        if rec["panel"]:
            for key in ("stt_end_mac", "announcing_mac", "finished_mac"):
                out["panel"][key] = iso_local(rec["panel"][key])
        return out
    return {"window": window, "clocks": CLOCKS, "sources": sources, "turns": [convert(r) for r in records]}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--since", default="24h", help="window start: 2h, 3d, 45m or an ISO time (default 24h)")
    ap.add_argument("--turns", type=int, default=10, help="show the last N turns of the window (default 10, 0 = all)")
    ap.add_argument("--json", action="store_true", help="print the joined records as JSON instead of the table")
    ap.add_argument("--panel-log", help="a saved `esphome logs` capture of the panel (Mac clock) to add the panel's stamps")
    ap.add_argument("--host", default=HOST, help=f"ssh target that holds the logs (default {HOST})")
    ap.add_argument("--pipeline", default=PIPELINE, help=f"the Assist pipeline's name (default {PIPELINE})")
    ap.add_argument("--raw", help="analyse a JSON document saved with --save-raw instead of asking Jarvis")
    ap.add_argument("--save-raw", help="save the collected JSON document (no secrets in it) for later --raw runs")
    args = ap.parse_args(argv)

    since = parse_since(args.since)
    if args.raw:
        raw = json.loads(Path(args.raw).read_text())
    else:
        raw = fetch(args.host, since, args.pipeline)
    if args.save_raw:
        Path(args.save_raw).write_text(json.dumps(raw, ensure_ascii=False))
    panel_text = panel_end = None
    if args.panel_log:
        path = Path(args.panel_log)
        panel_text, panel_end = path.read_text(errors="replace"), path.stat().st_mtime
    records, sources = analyse(raw, panel_text, panel_end, args.panel_log)
    records = [r for r in records if r["time"] is None or r["time"] >= since.timestamp()]
    if args.turns:
        records = records[-args.turns:]
    window = f"since {since.astimezone(TZ).isoformat(timespec='minutes')} ({args.since}), last {args.turns or 'all'} turns"
    if args.json:
        print(json.dumps(to_json(records, sources, window), ensure_ascii=False, indent=1))
    else:
        print(render_table(records, sources, window))
    return 0


if __name__ == "__main__":
    sys.exit(main())
