#!/usr/bin/env python3
"""Latency on Jarvis, three ways, against the real calendar-mcp (read-only questions). Run on Jarvis:

  python3 tests/latency_jarvis.py [--gap 60] [--lead 2] [--modes inprocess,warm]

The in-process modes use the brain in JARVIS_BRAIN / JARVIS_BRAIN_MODEL, so for the anthropic brain:
  JARVIS_BRAIN=anthropic JARVIS_BRAIN_MODEL=claude-sonnet-5-5 \
      ~/hallpanel-live/venv/bin/python3 tests/latency_jarvis.py --modes inprocess,warm

  spawn       the agent is a new process for every question (JARVIS_AGENT_CMD), as before
  inprocess   the agent lives in the server and keeps its connections (JARVIS_AGENT=inprocess)
  warm        inprocess, and /warm is called --lead seconds before the question, as the panel
              does on the wake word while Joel is still talking

Between questions it waits --gap seconds, so kept connections have time to cool. The time
measured is from sending the question to having the whole reply, i.e. what Joel waits after he
stops talking. History goes to a temporary database. Results: tests/last-latency-jarvis.json.
"""
import json
import os
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
from agent_core import CALENDAR_READ_TOOLS as READ_TOOLS  # noqa: E402
import jarvis_env  # noqa: E402

QUESTIONS = ["Vad har jag i kalendern i morgon?", "Har jag något på lördag?", "Hur många dagar är det kvar till julafton?"]


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def post(port, path, token, body=None):
    req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", method="POST", data=json.dumps(body or {}).encode(),
                                 headers={"X-Hallpanel-Token": token, "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=150) as r:
        return json.load(r)


def run_mode(mode, gap, lead, tmp, calendar):
    mcp_url, token_file = calendar
    port, token, log = free_port(), "lat-" + os.urandom(6).hex(), tmp / f"tools-{mode}.jsonl"
    env = {**os.environ, "HALLPANEL_TOKEN": token, "AGENT_TOOL_LOG": str(log),
           "JARVIS_WRITE_GUARD_FILE": str(tmp / "recent-creates.json")}
    if mode == "spawn":
        env.pop("JARVIS_AGENT", None)   # an exported JARVIS_AGENT=inprocess would measure inprocess instead
        env["JARVIS_AGENT_CMD"] = shlex.join([sys.executable, str(ROOT / "server" / "agents" / "openai_agent.py"),
                                              "--mcp", mcp_url, "--mcp-token-file", token_file, "--mcp-tools", READ_TOOLS])
    else:
        env.update({"JARVIS_AGENT": "inprocess", "JARVIS_CALENDAR_MCP": mcp_url, "JARVIS_CALENDAR_MCP_TOKEN_FILE": token_file,
                    "JARVIS_CALENDAR_MCP_TOOLS": READ_TOOLS})
    server = subprocess.Popen([sys.executable, str(ROOT / "server" / "hallpanel_server.py"), "--port", str(port),
                               "--bind", "127.0.0.1", "--db", str(tmp / f"history-{mode}.db"), "--assets", str(tmp)],
                              env=env, stdout=subprocess.DEVNULL, stderr=open(tmp / f"server-{mode}.log", "w"))
    time.sleep(3)
    rows = []
    try:
        for n, q in enumerate(QUESTIONS):
            if n:
                time.sleep(gap)
            if mode == "warm":
                post(port, "/warm", token)
                time.sleep(lead)
            log.write_text("")
            t0 = time.monotonic()
            res = post(port, "/converse", token, {"text": q, "conversation_id": f"lat-{mode}"})
            wall = round(time.monotonic() - t0, 2)
            calls = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
            t = res.get("timing") or {}
            rows.append({"mode": mode, "question": q, "wait_s": wall, "first_token_s": t.get("first_token_s"),
                         "ok": res.get("ok"), "tools": [(c["tool"], c["ms"]) for c in calls], "reply": res["reply"],
                         "brain": t.get("brain"), "cost_usd": t.get("cost_usd"), "tokens_in": t.get("tokens_in"),
                         "tokens_cached": t.get("tokens_cached"), "tokens_cache_write": t.get("tokens_cache_write")})
            print(f"{mode:9} {wall:5.2f} s  first token {t.get('first_token_s')} s  tools {rows[-1]['tools']}  "
                  f"${t.get('cost_usd')} in {t.get('tokens_in')} cached {t.get('tokens_cached')}  | {q}", flush=True)
    finally:
        server.terminate()
        server.wait()
    return rows


def main():
    gap = float(sys.argv[sys.argv.index("--gap") + 1]) if "--gap" in sys.argv else 60
    lead = float(sys.argv[sys.argv.index("--lead") + 1]) if "--lead" in sys.argv else 2
    modes = sys.argv[sys.argv.index("--modes") + 1].split(",") if "--modes" in sys.argv else ["spawn", "inprocess", "warm"]
    calendar = jarvis_env.calendar()
    tmp = Path(tempfile.mkdtemp(prefix="hallpanel-lat-"))
    rows = []
    for mode in modes:
        rows += run_mode(mode, gap, lead, tmp, calendar)
    (ROOT / "tests" / "last-latency-jarvis.json").write_text(json.dumps(
        {"gap_s": gap, "lead_s": lead, "rows": rows}, ensure_ascii=False, indent=1))
    for mode in modes:
        w = [r["wait_s"] for r in rows if r["mode"] == mode]
        print(f"{mode:9} mean {sum(w) / len(w):.2f} s, first question {w[0]:.2f} s, max {max(w):.2f} s")


if __name__ == "__main__":
    main()
