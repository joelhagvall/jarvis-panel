#!/usr/bin/env python3
"""A mock MCP server (stdio) with the tools Jarvis would use, for tests. Nothing here
touches iCloud, the TV or anything else: every call is only written to a log.

Tools: calendar_add_event, calendar_list_events, tv_power.

Environment:
  MOCK_LOG        JSONL file; one line per tool call: {"tool", "args", "mode"}
  MOCK_MODE_FILE  file holding ok/fail/empty/fail-read/slow; read on every call. With "fail",
                  calendar_add_event returns an error instead of success.
  MOCK_STATE      JSON file with the events added so far, so listing a day shows them.
Standard library only. Speaks newline-delimited JSON-RPC 2.0, as MCP stdio does.
Other agents can import TOOLS and call() to use the very same mock in-process.
"""
import json
import os
import sys
import time
from pathlib import Path

TOOLS = [
    {"name": "calendar_add_event",
     "description": "Lägg till en händelse i Joels iCloud-kalender. Tider i lokal tid (Europe/Stockholm).",
     "inputSchema": {"type": "object", "properties": {
         "title": {"type": "string", "description": "Händelsens namn"},
         "start": {"type": "string", "description": "Start, ISO 8601 utan tidszon, t.ex. 2026-10-21T17:00"},
         "end": {"type": "string", "description": "Slut, ISO 8601 utan tidszon (valfri)"},
         "calendar": {"type": "string", "enum": ["Att göra", "Jobb", "Annat"],
                      "description": "Vilken kalender (valfri, standard Att göra)"}},
         "required": ["title", "start"]}},
    {"name": "calendar_list_events",
     "description": "Lista händelser i Joels kalender för ett datum.",
     "inputSchema": {"type": "object", "properties": {
         "date": {"type": "string", "description": "Datum, YYYY-MM-DD"}}, "required": ["date"]}},
    {"name": "tv_power",
     "description": "Slå på eller stäng av TV:n i vardagsrummet.",
     "inputSchema": {"type": "object", "properties": {
         "on": {"type": "boolean", "description": "true för på, false för av"}}, "required": ["on"]}},
]


def mode():
    f = os.environ.get("MOCK_MODE_FILE")
    return Path(f).read_text().strip() if f and Path(f).exists() else "ok"


def call(name, args):
    m = mode()
    with open(os.environ.get("MOCK_LOG", os.devnull), "a") as log:
        log.write(json.dumps({"tool": name, "args": args, "mode": m}, ensure_ascii=False) + "\n")
    state_file = Path(os.environ.get("MOCK_STATE", os.devnull))
    added = json.loads(state_file.read_text()) if state_file.is_file() and state_file.stat().st_size else []
    if name == "calendar_add_event":
        if m == "fail":
            return True, "Fel: kalendern svarade inte (CalDAV 503). Händelsen skapades inte."
        added.append({"title": args.get("title"), "start": args.get("start"), "calendar": args.get("calendar", "Att göra")})
        state_file.write_text(json.dumps(added, ensure_ascii=False))
        return False, json.dumps({"ok": True, "uid": "mock-1", "title": args.get("title"),
                                  "start": args.get("start"), "calendar": args.get("calendar", "Att göra")},
                                 ensure_ascii=False)
    if name == "calendar_list_events":
        if m == "fail-read":
            return True, "Fel: kalendern svarade inte (CalDAV 503). Inga kalenderuppgifter kunde hämtas."
        if m == "slow":
            time.sleep(0.3)
        day = args.get("date", "")
        events = [{"title": "Standup", "start": "09:30"}, {"title": "Tvättstuga", "start": "18:00"}] if day and m != "empty" else []
        events += [{"title": e["title"], "start": e["start"][11:16]} for e in added if e["start"].startswith(day)]
        return False, json.dumps({"date": day, "events": sorted(events, key=lambda e: e["start"])}, ensure_ascii=False)
    if name == "tv_power":
        return False, json.dumps({"ok": True, "tv": "på" if args.get("on") else "av"}, ensure_ascii=False)
    return True, f"Okänt verktyg: {name}"


def reply(msg_id, result=None, error=None):
    out = {"jsonrpc": "2.0", "id": msg_id}
    out.update({"error": error} if error else {"result": result})
    sys.stdout.write(json.dumps(out, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def main():
    for line in sys.stdin:
        if not line.strip():
            continue
        msg = json.loads(line)
        method, mid = msg.get("method"), msg.get("id")
        if mid is None:                       # notifications need no answer
            continue
        if method == "initialize":
            reply(mid, {"protocolVersion": msg.get("params", {}).get("protocolVersion", "2025-06-18"),
                        "capabilities": {"tools": {}}, "serverInfo": {"name": "mocktools", "version": "1"}})
        elif method == "tools/list":
            reply(mid, {"tools": TOOLS})
        elif method == "tools/call":
            p = msg.get("params", {})
            is_error, text = call(p.get("name"), p.get("arguments") or {})
            reply(mid, {"content": [{"type": "text", "text": text}], "isError": is_error})
        elif method == "ping":
            reply(mid, {})
        else:
            reply(mid, error={"code": -32601, "message": f"unknown method {method}"})


if __name__ == "__main__":
    main()
