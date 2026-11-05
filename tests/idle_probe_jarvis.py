#!/usr/bin/env python3
"""Cold-start probe on Jarvis: how slow is a question after a long idle, with and without /warm?

Starts the server with the in-process Luna agent (read-only calendar tools, temporary history)
and keeps it running. Every --every minutes it asks one calendar question; every other probe
first calls /warm and waits --lead seconds, as the panel does on the wake word. Appends one
JSON line per probe to tests/idle-probe.jsonl and stops by itself after --probes probes.

  nohup python3 tests/idle_probe_jarvis.py --every 45 --probes 8 &
"""
import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
from agent_core import CALENDAR_READ_TOOLS  # noqa: E402
import jarvis_env  # noqa: E402


def arg(name, default):
    return float(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else default


def main():
    every, probes, lead = arg("--every", 45), int(arg("--probes", 8)), arg("--lead", 2)
    tmp = Path(tempfile.mkdtemp(prefix="hallpanel-probe-"))
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    token = "probe-" + os.urandom(6).hex()
    log = tmp / "tools.jsonl"
    url, token_file = jarvis_env.calendar()
    env = {**os.environ, "HALLPANEL_TOKEN": token, "AGENT_TOOL_LOG": str(log), "JARVIS_AGENT": "inprocess",
           "JARVIS_CALENDAR_MCP": url, "JARVIS_CALENDAR_MCP_TOOLS": CALENDAR_READ_TOOLS,
           "JARVIS_CALENDAR_MCP_TOKEN_FILE": token_file, "JARVIS_WRITE_GUARD_FILE": str(tmp / "recent-creates.json")}
    server = subprocess.Popen([sys.executable, str(ROOT / "server" / "hallpanel_server.py"), "--port", str(port),
                               "--bind", "127.0.0.1", "--db", str(tmp / "history.db"), "--assets", str(tmp)],
                              env=env, stdout=subprocess.DEVNULL, stderr=open(tmp / "server.log", "w"))
    out = ROOT / "tests" / "idle-probe.jsonl"

    def post(path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=json.dumps(body or {}).encode(),
                                     headers={"X-Hallpanel-Token": token, "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=150) as r:
            return json.load(r)

    try:
        for n in range(probes):
            time.sleep(every * 60)
            warm = n % 2 == 1
            if warm:
                post("/warm")
                time.sleep(lead)
            log.write_text("")
            t0 = time.monotonic()
            res = post("/converse", {"text": "Vad har jag i kalendern i morgon?", "conversation_id": f"probe-{n}"})
            wall = round(time.monotonic() - t0, 2)
            calls = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
            row = {"at": datetime.now(ZoneInfo("Europe/Stockholm")).isoformat(timespec="seconds"), "probe": n,
                   "idle_min": every, "warm": warm, "wait_s": wall, "first_token_s": (res.get("timing") or {}).get("first_token_s"),
                   "ok": res.get("ok"), "tools": [(c["tool"], c["ms"]) for c in calls]}
            with open(out, "a") as f:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
    finally:
        server.terminate()
        server.wait()


if __name__ == "__main__":
    main()
