#!/usr/bin/env python3
"""Every "Hey Jarvis" turn and how it ended, from the voice_turns table in history.db (read only).

  python3 ~/hallpanel-live/server/show_turns.py           the last 20 turns and a summary per day (7 days)
  python3 ~/hallpanel-live/server/show_turns.py 50        the last 50
  python3 ~/hallpanel-live/server/show_turns.py --days 30
  python3 ~/hallpanel-live/server/show_turns.py --db FILE
  python3 ~/hallpanel-live/server/show_turns.py --model-tools [--days 14]
                                             questions the model answered with at most one kind of tool, by
                                             tool: exact, common ones are candidates for quick_command.py

Home Assistant reports each stage under the turn's id (POST /turn): recognition (stt) and speech (tts); the
server adds its own /transcribe attempts and the reply's messages row. A stage that never reported shows -.
"""
from collections import Counter, defaultdict
import json
from datetime import datetime
from pathlib import Path
import sqlite3
import sys
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")
STT = {"text": "text", "no_speech": "inget tal", "empty": "tom text", "failed": "FEL", "invalid": "FEL format",
       "error": "FEL", "stopped": "stoppad", "lost": "FEL kontakt", "cancelled": "avbruten"}
TTS = {"complete": "uppläst", "stopped": "avbruten", "error": "FEL"}


def load(db, limit, days):
    con = sqlite3.connect(f"file:{Path(db).expanduser()}?mode=ro", uri=True)
    if not con.execute("SELECT 1 FROM sqlite_master WHERE name = 'voice_turns'").fetchone():
        return [], []
    question = ("(SELECT text FROM messages q WHERE q.role = 'user' AND q.id < t.reply_id ORDER BY q.id DESC "
                "LIMIT 1)")
    columns = ("started, stt, listen_ms, speech_ms, early, language, recognitions, recognition_ms, stt_error, "
               f"reply_id, reply_ok, m.total_ms, tts, sentences, sentences_failed, first_audio_ms, audio_ms, {question}")
    query = f"SELECT {columns} FROM voice_turns t LEFT JOIN messages m ON m.id = t.reply_id"
    names = [c.strip().split(".")[-1] for c in columns.split(",")[:-1]] + ["question"]
    rows = con.execute(query + " ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
    since = datetime.now(TZ).timestamp() - days * 86400
    every = [dict(zip(names, r)) for r in con.execute(query + " ORDER BY started").fetchall()]
    recent = [r for r in every if datetime.fromisoformat(r["started"]).timestamp() >= since]
    return [dict(zip(names, r)) for r in reversed(rows)], recent


def outcome(turn):
    """One word for how the turn ended, worst stage first."""
    if turn["stt"] in ("failed", "invalid", "error") or (turn["stt"] is None and turn["stt_error"]):
        return "text-tolkning misslyckades"
    if turn["stt"] in ("no_speech", "empty"):
        return "inget att svara på"
    if turn["stt"] == "stopped":
        return "stoppad på panelen"
    if turn["stt"] == "lost":
        return "tappade kontakten under lyssning"
    if turn["stt"] == "cancelled":
        return "avbruten under lyssning"
    if turn["reply_ok"] == 0:
        return "svaret misslyckades"
    if turn["tts"] == "error" or turn["sentences_failed"]:
        return "uppläsningen misslyckades"
    if turn["tts"] == "stopped":
        return "uppläsningen avbröts"
    if turn["stt"] == "text" and turn["tts"] == "complete":
        return "ok" if turn["reply_id"] else "ok (HA svarade själv)"
    return "ofullständig"


def ms(value):
    return "-" if value is None else f"{value / 1000:.1f}"


def table(turns):
    print(f"{'tid':14} {'tolkning':10} {'lyssn s':>7} {'tal s':>5} {'försök':>6} {'svar':5} {'svar s':>6} "
          f"{'uppläst':9} {'1:a ljud':>8}  utfall / fråga")
    for t in turns:
        when = datetime.fromisoformat(t["started"]).astimezone(TZ).strftime("%m-%d %H:%M:%S")
        stt = STT.get(t["stt"], "-")
        if t["stt"] == "text" and t["language"] == "en":
            stt = "text (en)"
        reply = "-" if t["reply_ok"] is None else ("ok" if t["reply_ok"] else "FEL")
        tts = TTS.get(t["tts"], "-")
        if t["sentences"] is not None:
            tts += f" {t['sentences']}" + (f"/{t['sentences_failed']}F" if t["sentences_failed"] else "")
        note = outcome(t)
        if t["stt_error"]:
            note += f" [{t['stt_error'][:60]}]"
        if t["question"]:
            note += ": " + t["question"].replace("\n", " ")[:60]
        print(f"{when:14} {stt:10} {ms(t['listen_ms']):>7} {ms(t['speech_ms']):>5} {t['recognitions'] or 0:>6} "
              f"{reply:5} {ms(t['total_ms']):>6} {tts:9} {ms(t['first_audio_ms']):>8}  {note}")


def summary(turns):
    days = {}
    for t in turns:
        day = datetime.fromisoformat(t["started"]).astimezone(TZ).strftime("%Y-%m-%d")
        days.setdefault(day, Counter())[outcome(t)] += 1
    if not days:
        return
    kinds = ["ok", "ok (HA svarade själv)", "inget att svara på", "stoppad på panelen", "text-tolkning misslyckades",
             "tappade kontakten under lyssning", "svaret misslyckades", "uppläsningen misslyckades",
             "uppläsningen avbröts", "avbruten under lyssning", "ofullständig"]
    kinds = [k for k in kinds if any(c[k] for c in days.values())]
    print("\nper dag   turer  " + "  ".join(kinds))
    for day, counts in sorted(days.items()):
        print(f"{day} {sum(counts.values()):>5}  " + "  ".join(f"{counts[k]:>{len(k)}}" for k in kinds))


def model_tools(db, days):
    """Questions the model answered (not the direct path) with no tool or one kind of tool, by tool and how
    often, with the time it took. The direct paths for "spela X" and the radio came from this list (2026-10-28
    and 2026-10-30: 1.2-3.8 s on the model for one tool call)."""
    con = sqlite3.connect(f"file:{Path(db).expanduser()}?mode=ro", uri=True)
    rows = con.execute("SELECT conversation_id, role, text, brain, total_ms, tools FROM messages "
                       "WHERE ts >= date('now', ?) ORDER BY id", (f"-{int(days)} day",)).fetchall()
    asked, by_tool = {}, defaultdict(list)
    for conversation, role, text, brain, total_ms, tools in rows:
        if role == "user":
            asked[conversation] = text
        elif role == "jarvis" and conversation in asked and brain and "direct" not in brain:
            try:
                names = sorted({t.get("name") for t in json.loads(tools or "[]") if isinstance(t, dict)})
            except ValueError:
                names = []
            if len(names) <= 1:
                by_tool[names[0] if names else "-"].append((total_ms, asked.pop(conversation)))
    for tool, turns in sorted(by_tool.items(), key=lambda kv: -len(kv[1])):
        print(f"{tool}: {len(turns)}")
        for total_ms, question in turns[-12:]:
            print(f"  {total_ms or 0:>6} ms  {(question or '')[:80]}")


def main(argv):
    db, limit, days = "~/.local/share/hallpanel/history.db", 20, 7
    args = list(argv)
    if "--db" in args:
        i = args.index("--db")
        db, args = args[i + 1], args[:i] + args[i + 2:]
    if "--days" in args:
        i = args.index("--days")
        days, args = int(args[i + 1]), args[:i] + args[i + 2:]
    if "--model-tools" in args:
        model_tools(db, days if "--days" in argv else 14)
        return
    if args:
        limit = int(args[0])
    last, recent = load(db, limit, days)
    if not last:
        print("Inga röstturer sparade än (voice_turns finns sedan 2026-10-27).")
        return
    table(last)
    summary(recent)


if __name__ == "__main__":
    main(sys.argv[1:])
