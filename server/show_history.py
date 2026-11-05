#!/usr/bin/env python3
"""The latest chat messages on Jarvis as a table: time, who, model, speed, tokens, tools, text.

  python3 ~/hallpanel-live/server/show_history.py         the last 20 messages
  python3 ~/hallpanel-live/server/show_history.py 50      the last 50
  python3 ~/hallpanel-live/server/show_history.py --db ~/.local/share/hallpanel/history-sim.db
  python3 ~/hallpanel-live/server/show_history.py 5 --full   whole texts, not cut at 70 characters, and each tool's
                                                              time: "tvatt_boka 7020 ms (r2 +2649)" = round 2, started
                                                              2649 ms after that round's model request began
  python3 ~/hallpanel-live/server/show_history.py --tool resa    every resa call with its args and the start of its
                                                              result, failures marked, and how many failed (the 20
                                                              latest; a number before it sets how many)
"""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

args = sys.argv[1:]
if "-h" in args or "--help" in args:
    sys.exit(__doc__)
full = "--full" in args
args = [a for a in args if a != "--full"]
db = Path("~/.local/share/hallpanel/history.db")
if "--db" in args:
    i = args.index("--db")
    db, args = Path(args[i + 1]), args[:i] + args[i + 2:]
tool = None
if "--tool" in args:
    i = args.index("--tool")
    tool, args = args[i + 1], args[:i] + args[i + 2:]
limit = int(args[0]) if args else 20
con = sqlite3.connect(db.expanduser())
have = {row[1] for row in con.execute("PRAGMA table_info(messages)")}
if tool:
    # One tool's calls, as the model asked them: why a lookup failed (2026-11-04: resa's "Zinkens damm").
    calls = [(ts, t) for ts, tools in con.execute("SELECT ts, tools FROM messages WHERE tools LIKE ? ORDER BY id",
                                                  (f'%"{tool}"%',))
             for t in json.loads(tools or "[]") if t.get("name") == tool]
    for ts, t in calls[-limit:]:
        when = datetime.fromisoformat(ts).astimezone(ZoneInfo("Europe/Stockholm")).strftime("%m-%d %H:%M")
        print(f"{when} {'FEL' if t.get('error') else 'ok ':3} {t.get('args') or ''} -> {(t.get('result') or '')[:120]}")
    print(f"{len(calls)} anrop, {sum(1 for _, t in calls if t.get('error'))} fel")
    sys.exit()
extra = [c for c in ("brain", "first_token_ms", "total_ms", "tokens_in", "tokens_out", "tokens_cached", "tools",
                     "error", "steps") if c in have]
rows = con.execute(f"SELECT ts, role, text, ok, {', '.join(extra) or 'NULL'} FROM messages ORDER BY id DESC LIMIT ?",
                   (limit,)).fetchall()
print(f"{'tid':5} {'vem':6} {'modell':24} {'första':>7} {'totalt':>7} {'tokens (cachade)':>18}  {'verktyg':22} text")
for ts, role, text, ok, *rest in reversed(rows):
    m = dict(zip(extra, rest))
    when = datetime.fromisoformat(ts).astimezone(ZoneInfo("Europe/Stockholm")).strftime("%H:%M")
    ms = lambda k: f"{m[k]} ms" if m.get(k) is not None else ""
    tokens = f"{m['tokens_in']}/{m['tokens_out']}" if m.get("tokens_in") is not None else ""
    if m.get("tokens_cached"):
        tokens += f" ({m['tokens_cached']} c)"
    tools = ", ".join(t["name"] for t in json.loads(m["tools"])) if m.get("tools") else ""
    if m.get("steps"):
        tools = f"{m['steps']}r " + tools          # rounds: requests to the model for this answer
    note = f"  [fel: {m['error'][:60]}]" if m.get("error") else ""
    print(f"{when:5} {'Joel' if role == 'user' else 'Jarvis':6} {(m.get('brain') or '')[:24]:24} {ms('first_token_ms'):>7} "
          f"{ms('total_ms'):>7} {tokens:>18}  {tools[:22]:22} {text.replace(chr(10), ' ')[:None if full else 70]}{note}")
    if full and m.get("tools"):
        for t in json.loads(m["tools"]):
            at = f" +{t['at_ms']}" if t.get("at_ms") is not None else ""
            print(f"{'':6}{t['name']} {t.get('ms')} ms (r{t.get('round', '?')}{at}){' fel' if t.get('error') else ''}")
