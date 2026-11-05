#!/usr/bin/env python3
"""Live test of the Luna agent on Jarvis against the real calendar-mcp. Run it on Jarvis:

  python3 tests/live_jarvis_test.py            read phase only
  python3 tests/live_jarvis_test.py --write    read phase, then one controlled write

Read phase: Luna gets only the read tools (calendars, agenda, search, show_event).
Write phase: Luna also gets add_event and is asked to add one clearly marked event,
"Jarvis-test", tomorrow at 07:00 in Att göra. The script then checks the calendar itself and
removes the event again with undo_change, using the change id from Luna's own add_event call.
Luna is never offered edit_event, delete_event or undo_change.

Requests go through server/hallpanel_server.py, so the timing is measured as in production,
but the history goes to a temporary database, not the real one. Needs the OpenAI key in
~/.config/hallpanel/openai.env; the calendar's address and token file come from the server's own
~/.config/hallpanel/server.env.
"""
import json
import os
import re
import shlex
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
from agent_core import CALENDAR_READ_TOOLS, CALENDAR_TOOLS, mcp_client  # noqa: E402
from env_files import load_key_file  # noqa: E402
import jarvis_env  # noqa: E402

TEST_TITLE = "Jarvis-test"
FAILURE = re.compile(r"(kunde inte|gick inte|misslyck|lyckades inte|fel\b|svarade inte)", re.I)


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def run_phase(name, tools, scenarios, tmp, calendar):
    """Start the server with Luna restricted to `tools`, run the scenarios, return results."""
    mcp_url, token_file = calendar
    log, port, token = tmp / f"tools-{name}.jsonl", free_port(), "live-" + os.urandom(6).hex()
    agent = [sys.executable, str(ROOT / "server" / "agents" / "openai_agent.py"), "--model", "gpt-6-luna",
             "--tier", "fast", "--effort", "none", "--mcp", mcp_url, "--mcp-token-file", token_file,
             "--mcp-tools", tools]
    env = {**os.environ, "HALLPANEL_TOKEN": token, "JARVIS_AGENT_CMD": shlex.join(agent), "AGENT_TOOL_LOG": str(log)}
    env.pop("JARVIS_AGENT", None)       # an exported JARVIS_AGENT=inprocess would skip the restricted command
    server = subprocess.Popen([sys.executable, str(ROOT / "server" / "hallpanel_server.py"), "--port", str(port),
                               "--bind", "127.0.0.1", "--db", str(tmp / "history.db"), "--assets", str(tmp)],
                              env=env, stdout=subprocess.DEVNULL, stderr=open(tmp / f"server-{name}.log", "w"))
    time.sleep(1)
    results = []
    try:
        for text, check in scenarios:
            log.write_text("")
            req = urllib.request.Request(f"http://127.0.0.1:{port}/converse", method="POST",
                                         data=json.dumps({"text": text, "conversation_id": f"live-{name}"}).encode(),
                                         headers={"X-Hallpanel-Token": token, "Content-Type": "application/json"})
            with urllib.request.urlopen(req, timeout=150) as r:
                res = json.load(r)
            calls = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
            bad = [label for label, ok in check(calls, res["reply"]) + [("server ok", res.get("ok") is True)] if not ok]
            t = res.get("timing") or {}
            results.append({"phase": name, "text": text, "reply": res["reply"], "calls": calls, "failed_checks": bad,
                            "first_token_s": t.get("first_token_s"), "total_s": t.get("total_s"), "cost_usd": t.get("cost_usd")})
            tool_ms = ", ".join(f"{c['tool']} {c['ms']} ms" for c in calls) or "no tools"
            print(f"{'PASS' if not bad else 'FAIL'} [{name}] {text}")
            print(f"     tools: {[(c['tool'], c['args']) for c in calls]}  ({tool_ms})")
            print(f"     reply: {res['reply']}")
            print(f"     first token {t.get('first_token_s')} s, total {t.get('total_s')} s" + (f"  FAILED: {bad}" if bad else ""))
    finally:
        server.terminate()
        server.wait()
    return results


def main():
    load_key_file()
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY is not set (put it in ~/.config/hallpanel/openai.env)")
    calendar = jarvis_env.calendar()
    now = datetime.now(ZoneInfo("Europe/Stockholm"))
    tomorrow = (now.date() + timedelta(days=1)).isoformat()
    friday = (now.date() + timedelta(days=(4 - now.weekday()) % 7 or 7)).isoformat()
    tmp = Path(tempfile.mkdtemp(prefix="hallpanel-live-"))

    def agenda_from(day):
        return lambda calls: any(c["tool"] == "agenda" and str(c["args"].get("start", "")).startswith(day) for c in calls)

    reads = [
        ("Vad har jag i kalendern i morgon?", lambda calls, reply: [
            ("agenda from tomorrow", agenda_from(tomorrow)(calls)), ("no tool errors", not any(c["error"] for c in calls))]),
        ("Har jag något inbokat på fredag?", lambda calls, reply: [
            ("agenda from Friday", agenda_from(friday)(calls)), ("no tool errors", not any(c["error"] for c in calls))]),
        ("När är nästa tvättid?", lambda calls, reply: [
            ("searches or reads the calendar", any(c["tool"] in ("search", "agenda") for c in calls)),
            ("no tool errors", not any(c["error"] for c in calls))]),
    ]
    results = run_phase("read", CALENDAR_READ_TOOLS, reads, tmp, calendar)

    if "--write" in sys.argv:
        writes = [(f"Lägg in {TEST_TITLE} i kalendern Att göra i morgon klockan sju", lambda calls, reply: [
            ("adds exactly one event", [c["tool"] for c in calls].count("add_event") == 1),
            ("in Att göra, titled Jarvis-test, tomorrow 07:00", any(
                c["tool"] == "add_event" and c["args"].get("calendar") == "Att göra"
                and TEST_TITLE.lower() in c["args"].get("title", "").lower()
                and re.match(rf"{tomorrow}[T ]07:00", str(c["args"].get("start", ""))) for c in calls)),
            ("reply does not report a failure", not FAILURE.search(reply))])]
        w = run_phase("write", CALENDAR_TOOLS, writes, tmp, calendar)
        results += w
        mcp = mcp_client(*calendar)
        mcp.start()
        changes = [m.group(1) for c in w[0]["calls"] if c["tool"] == "add_event" and not c["error"]
                   for m in [re.search(r"change ([^)\s;]+)\)", c["result"])] if m]
        _, before = mcp.run("agenda", {"start": tomorrow, "days": 1, "calendar": "Att göra"})
        print(f"\ncalendar check: {TEST_TITLE} {'is' if TEST_TITLE in before else 'is NOT'} in Att göra tomorrow")
        for cid in changes:
            err, text = mcp.run("undo_change", {"change": cid})
            print(f"cleanup: undo_change {cid} -> {'ERROR ' if err else ''}{text}")
        _, after = mcp.run("agenda", {"start": tomorrow, "days": 1, "calendar": "Att göra"})
        gone = TEST_TITLE not in after
        print(f"cleanup check: {TEST_TITLE} {'is gone' if gone else 'IS STILL THERE - remove it by hand'}")
        w[0]["cleanup"] = {"changes": changes, "was_added": TEST_TITLE in before, "gone_after": gone}

    out = ROOT / "tests" / "last-live-jarvis-run.json"
    out.write_text(json.dumps({"run_at": now.isoformat(timespec="seconds"), "results": results}, ensure_ascii=False, indent=1))
    failed = sum(bool(r["failed_checks"]) for r in results)
    print(f"\n{len(results) - failed}/{len(results)} scenarios passed -> {out}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
