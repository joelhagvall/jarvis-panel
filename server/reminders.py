#!/usr/bin/env python3
"""Reminders Joel asks for by voice ("påminn mig klockan sex att ...", "säg till om tjugo minuter").
Standard library only.

They are kept in the table `reminders` in the history database, so they survive a restart, and nothing is
deleted: a reminder keeps its row with the time it was said or cancelled. server/watch.py says each one when
it is due, through server/notify.py: on the panel when Joel is home, otherwise on his phone.

Timers on the panel are Home Assistant's own (it understands "sätt en timer på tio minuter" itself and the panel
counts down); a timer that reaches the agent becomes a reminder in that many minutes.

Like the memory's tools, adding or cancelling runs only when what Joel just said asks for it, so neither a web
page the agent reads nor the agent itself can make Jarvis say something later.
"""
import re
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")
MAX_PENDING, MAX_CHARS, MAX_DAYS = 30, 200, 366
ADD = re.compile(r"påminn|påmin|säg\s*till|väck|timer|äggklocka|klocka|alarm|larma|ring|om\s+\w+\s+minut|"
                 r"\bremind me\b|\bset (a |an |the )?reminder\b|\bwake me\b|\btell me in\b", re.I)
CANCEL = re.compile(r"ta\s*bort|stryk|avbryt|glöm|stäng\s*av|stoppa|ställ\s*in|radera|behövs\s*inte|"
                    r"\b(cancel|delete|remove|stop)\b|\bturn off\b", re.I)
DAYS = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]

REMIND_TOOL = {"type": "function", "name": "paminnelse", "strict": False,
               "description": ("Påminnelser som Jarvis säger högt på panelen vid en viss tid, eller skickar till "
                               "telefonen när Joel inte är hemma: lägg till en, visa de som väntar eller ta bort en. "
                               "Ber Joel om en timer eller äggklocka lägger du en påminnelse om så många minuter."),
               "parameters": {"type": "object", "properties": {
                   "action": {"type": "string", "enum": ["add", "list", "cancel"], "description": "Standard add"},
                   "text": {"type": "string",
                            "description": "Det Jarvis ska säga då, kort och till Joel, till exempel \"Dags att ta ut tvätten.\""},
                   "at": {"type": "string", "description": "Klockslaget i Stockholm, YYYY-MM-DDTHH:MM"},
                   "in_minutes": {"type": "number", "description": "Om så här många minuter, i stället för at"},
                   "id": {"type": "integer", "description": "Påminnelsens nummer, för cancel"}}}}


PANEL_ROWS = 9                          # the reminder page's rows (reminders_page.ROWS)


def _field(text):
    return " ".join(str(text).replace("|", " ").replace(";", ",").split())


def now_utc():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def when_text(due, now=None):
    """"18:00", "i morgon 07:30" or "fre 24/10 09:00", in Stockholm time."""
    local, today = due.astimezone(TZ), (now or datetime.now(TZ)).astimezone(TZ).date()
    days = (local.date() - today).days
    if days == 0:
        return f"{local:%H:%M}"
    if days == 1:
        return f"i morgon {local:%H:%M}"
    return f"{DAYS[local.weekday()]} {local.day}/{local.month} {local:%H:%M}"


class Reminders:
    def __init__(self, db_path):
        path = Path(db_path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.said = ""                  # what Joel said in the question being answered; the tool checks it
        self.db.execute("""CREATE TABLE IF NOT EXISTS reminders (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,                      -- UTC, ISO 8601, when it was asked for
            due TEXT NOT NULL,                     -- UTC, ISO 8601, when to say it
            text TEXT NOT NULL,
            said TEXT,                             -- what Joel said when he asked for it
            done TEXT,                             -- UTC, ISO 8601, when it was said, or NULL
            cancelled TEXT                         -- UTC, ISO 8601, when it was cancelled, or NULL
        )""")
        self.db.commit()
        # An in-flight delivery must survive a crash without being silently replayed.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if "attempted" not in {r[1] for r in self.db.execute("PRAGMA table_info(reminders)")}:
                self.db.execute("ALTER TABLE reminders ADD COLUMN attempted TEXT")

    def pending(self, include_attempted=True):
        """[(id, due datetime, text)], soonest first."""
        with self.lock:
            rows = self.db.execute("SELECT id, due, text FROM reminders WHERE done IS NULL AND cancelled IS NULL "
                                   + ("" if include_attempted else "AND attempted IS NULL ") +
                                   "ORDER BY due").fetchall()
        return [(i, datetime.fromisoformat(due), text) for i, due, text in rows]

    def due(self, now=None):
        """The pending reminders whose time has come."""
        now = now or datetime.now(timezone.utc)
        return [r for r in self.pending(include_attempted=False) if r[1] <= now]

    def claim(self, n):
        """Persist ownership before any delivery; concurrent/restarted workers cannot repeat it."""
        with self.lock:
            cur = self.db.execute("UPDATE reminders SET attempted = ? WHERE id = ? AND attempted IS NULL "
                                  "AND done IS NULL AND cancelled IS NULL", (now_utc(), n))
            self.db.commit()
            return cur.rowcount == 1

    def release(self, n):
        """Retry only after the notifier knows no destination accepted the delivery."""
        with self.lock:
            self.db.execute("UPDATE reminders SET attempted = NULL WHERE id = ? AND done IS NULL AND cancelled IS NULL", (n,))
            self.db.commit()

    def unconfirmed(self):
        with self.lock:
            return {r[0] for r in self.db.execute("SELECT id FROM reminders WHERE attempted IS NOT NULL "
                                                "AND done IS NULL AND cancelled IS NULL")}

    def mark_done(self, n):
        with self.lock:
            self.db.execute("UPDATE reminders SET done = ? WHERE id = ?", (now_utc(), n))
            self.db.commit()

    def add(self, due, text, said=""):
        with self.lock:
            cur = self.db.execute("INSERT INTO reminders (ts, due, text, said) VALUES (?, ?, ?, ?)",
                                  (now_utc(), due.astimezone(timezone.utc).isoformat(timespec="seconds"), text, said))
            self.db.commit()
        return cur.lastrowid

    def cancel(self, n):
        with self.lock:
            cur = self.db.execute("UPDATE reminders SET cancelled = ? WHERE id = ? AND done IS NULL AND cancelled IS NULL",
                                  (now_utc(), n))
            self.db.commit()
        return cur.rowcount == 1

    def lines(self, now=None, limit=3):
        """Up to `limit` pending reminders as "18:00 Ta ut tvätten.", for the panel."""
        return [line for _, line in self.upcoming(now, limit)]

    def upcoming(self, now=None, limit=3):
        """[(due, line)] of lines(): the Jarvis card's Härnäst keeps only those due soon (home_status.next_up)."""
        unconfirmed = self.unconfirmed()
        return [(due, ("Leverans ej bekräftad: " if n in unconfirmed else "") + f"{when_text(due, now)} {text}")
                for n, due, text in self.pending()[:limit]]

    def panel_rows(self, now=None, limit=PANEL_ROWS):
        """Hemmet's reminder page (2026-11-03) as `rem_list`: rows "r|id|when|text|u" soonest first (u is "1" while
        a delivery is unconfirmed), then "m|N" when N more wait than fit. Rows are separated by ";", fields by "|",
        so both are taken out of the text (styles/glass-v2/reminders_page.py reads it)."""
        pending, unconfirmed = self.pending(), self.unconfirmed()
        rows = [f"r|{n}|{when_text(due, now)}|{_field(text)}|{int(n in unconfirmed)}" for n, due, text in pending[:limit]]
        if len(pending) > limit:
            rows.append(f"m|{len(pending) - limit}")
        return ";".join(rows)

    # -- the agent's tool ---------------------------------------------------------------------------------
    def tools(self):
        return [(REMIND_TOOL, self.tool)]

    def tool(self, args, now=None):
        now = now or datetime.now(TZ)
        action = str(args.get("action") or "add").lower()
        if action == "list":
            pending = self.pending()
            if not pending:
                return False, "Inga påminnelser väntar."
            unconfirmed = self.unconfirmed()
            return False, "Påminnelser som väntar: " + "; ".join(
                f"[{i}] {when_text(due, now)}: " + ("Leverans ej bekräftad: " if i in unconfirmed else "") + text
                for i, due, text in pending)
        if action == "cancel":
            if not CANCEL.search(self.said):
                return True, "Inte borttagen: Joel bad inte om det nu."
            try:
                n = int(args.get("id"))
            except (TypeError, ValueError):
                return True, "Inte borttagen: ange påminnelsens nummer (visa dem med action list)."
            return (False, f"Påminnelse {n} är borttagen.") if self.cancel(n) else (True, f"Ingen påminnelse {n} väntar.")
        if not ADD.search(self.said):
            return True, "Inte sparad: Joel bad inte om en påminnelse nu."
        text = " ".join(str(args.get("text") or "").split())
        if not text:
            return True, "Inte sparad: säg vad påminnelsen ska säga."
        if len(text) > MAX_CHARS:
            return True, f"Inte sparad: högst {MAX_CHARS} tecken."
        if args.get("in_minutes") not in (None, ""):
            try:
                minutes = float(args["in_minutes"])
            except (TypeError, ValueError):
                return True, "Inte sparad: in_minutes ska vara ett tal."
            if minutes <= 0:
                return True, "Inte sparad: in_minutes ska vara mer än noll."
            due = now + timedelta(minutes=minutes)
        elif args.get("at"):
            try:
                due = datetime.fromisoformat(str(args["at"]))
            except ValueError:
                return True, "Inte sparad: at ska vara YYYY-MM-DDTHH:MM."
            due = due if due.tzinfo else due.replace(tzinfo=TZ)
            if due <= now:
                return True, "Inte sparad: den tiden har redan varit."
        else:
            return True, "Inte sparad: ange at eller in_minutes."
        if due > now + timedelta(days=MAX_DAYS):
            return True, "Inte sparad: högst ett år fram."
        pending = self.pending()
        if len(pending) >= MAX_PENDING:
            return True, f"Inte sparad: {MAX_PENDING} påminnelser väntar redan."
        if any(abs((d - due).total_seconds()) < 60 and t.casefold() == text.casefold() for _, d, t in pending):
            return False, "Den påminnelsen finns redan."
        n = self.add(due, text, self.said)
        return False, f"Sparad som påminnelse {n}, {when_text(due, now)}: {text}"
