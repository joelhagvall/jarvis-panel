"""The conversation history and the voice turns, kept forever in SQLite (Joel's choice, 2026-10-21)."""
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path


class History:
    # Kept on every Jarvis reply: which model answered, how long it took, what it cost and which tools
    # it used (JSON list of name, ms, error), or the error when it failed. Added 2026-10-21; older rows
    # have them empty.
    META = {"brain": "TEXT", "first_token_ms": "INTEGER", "total_ms": "INTEGER", "tokens_in": "INTEGER",
            "tokens_out": "INTEGER", "cost_usd": "REAL", "tools": "TEXT", "error": "TEXT",
            "tokens_cached": "INTEGER", "tokens_cache_write": "INTEGER",   # input read from / written to the prompt cache
            "steps": "INTEGER",                    # requests to the model for the answer (since 2026-10-22)
            "card": "TEXT", "agent_queue_ms": "INTEGER", "request_to_first_text_ms": "INTEGER"}                        # the cards shown on the panel (agents/screen.py, since 2026-10-23)

    def __init__(self, path):
        path = Path(path).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        self.db.execute("""CREATE TABLE IF NOT EXISTS messages (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,                      -- UTC, ISO 8601
            conversation_id TEXT,
            role TEXT NOT NULL CHECK (role IN ('user', 'jarvis')),
            text TEXT NOT NULL,
            ok INTEGER NOT NULL DEFAULT 1           -- 0 when the agent failed
        )""")
        # Rensa on the panel (POST /clear): the conversation's context and /history start after the last one.
        self.db.execute("""CREATE TABLE IF NOT EXISTS clears (
            id INTEGER PRIMARY KEY,
            ts TEXT NOT NULL,                      -- UTC, ISO 8601
            after_id INTEGER NOT NULL              -- the last message forgotten
        )""")
        have = {row[1] for row in self.db.execute("PRAGMA table_info(messages)")}
        for column, kind in self.META.items():
            if column not in have:
                self.db.execute(f"ALTER TABLE messages ADD COLUMN {column} {kind}")
        # One row per "Hey Jarvis" turn and how it ended, failures and silence included (since 2026-10-27). The
        # journal holds about a day and Home Assistant keeps its last 10 runs in memory, so this is the record.
        # No transcript here: the question and reply are the messages rows (reply_id). show_turns.py reads it.
        self.db.execute("""CREATE TABLE IF NOT EXISTS voice_turns (
            id TEXT PRIMARY KEY,                   -- Home Assistant's turn id (16 hex)
            started TEXT NOT NULL,                 -- UTC, ISO 8601 with ms: when the panel began streaming
            updated TEXT NOT NULL,
            stt TEXT,                              -- TURN_STAGES['stt']['outcome']
            listen_ms INTEGER,                     -- stream start -> recognition result in Home Assistant
            speech_ms INTEGER,                     -- the recording sent for recognition
            early INTEGER,                         -- 1 when the early transcript was used
            language TEXT,
            recognitions INTEGER,                  -- POST /transcribe requests for the turn
            recognition_ms INTEGER,                -- the last successful one, on the server
            stt_error TEXT,                        -- the server's reason for the last failed one
            reply_id INTEGER,                      -- messages.id of Jarvis's reply; none when HA answered itself
            reply_ok INTEGER,
            tts TEXT,                              -- TURN_STAGES['tts']['outcome']
            sentences INTEGER,
            sentences_failed INTEGER,
            first_audio_ms INTEGER,                -- speech stream start -> first sentence's audio
            audio_ms INTEGER                       -- speech handed to the panel
        )""")
        self.db.commit()

    def turn(self, turn_id, started_ms_ago=0, increment=(), **fields):
        """Create or update a voice turn's row; the stages arrive in any order. Columns come from code only."""
        now = datetime.now(timezone.utc)
        stamp = lambda t: t.isoformat(timespec="milliseconds")  # noqa: E731
        start = stamp(now - timedelta(milliseconds=max(0, started_ms_ago)))
        with self.lock:
            self.db.execute("INSERT OR IGNORE INTO voice_turns (id, started, updated) VALUES (?, ?, ?)",
                            (turn_id, start, stamp(now)))
            sets = ["updated = ?", "started = MIN(started, ?)", *(f"{c} = ?" for c in fields),
                    *(f"{c} = COALESCE({c}, 0) + 1" for c in increment)]
            self.db.execute(f"UPDATE voice_turns SET {', '.join(sets)} WHERE id = ?",
                            (stamp(now), start, *fields.values(), turn_id))
            self.db.commit()

    def add(self, conversation_id, role, text, ok=True, meta=None):
        """Store one message; meta holds the reply's model, times, tokens, cost, tools or error."""
        meta = {k: (json.dumps(v, ensure_ascii=False) if k == "tools" else v)
                for k, v in (meta or {}).items() if k in self.META and v is not None}
        columns = ["ts", "conversation_id", "role", "text", "ok", *meta]
        values = [datetime.now(timezone.utc).isoformat(timespec="seconds"), conversation_id, role, text, int(ok),
                  *meta.values()]
        with self.lock:
            row = self.db.execute(f"INSERT INTO messages ({', '.join(columns)}) VALUES ({', '.join('?' * len(values))})",
                                  values).lastrowid
            self.db.commit()
        return row

    def clear(self):
        """Jarvis forgets the conversation so far; the messages stay in the database."""
        with self.lock:
            self.db.execute("INSERT INTO clears (ts, after_id) SELECT ?, COALESCE(MAX(id), 0) FROM messages",
                            (datetime.now(timezone.utc).isoformat(timespec="seconds"),))
            self.db.commit()

    def last_clear(self):
        """The id of the last message Rensa made Jarvis forget, or 0."""
        with self.lock:
            return self.db.execute("SELECT COALESCE(MAX(after_id), 0) FROM clears").fetchone()[0]

    def questions_since(self, since):
        with self.lock:
            return self.db.execute("SELECT COUNT(*) FROM messages WHERE role = 'user' AND ts >= ?",
                                   (since,)).fetchone()[0]

    def latest(self, limit, since=None, after_clear=True):
        """The latest messages, oldest first; with after_clear only those after the last Rensa."""
        q, where, args = "SELECT ts, conversation_id, role, text, card FROM messages", [], []
        if after_clear and (cleared := self.last_clear()):
            where, args = ["id > ?"], [cleared]
        if since:
            where, args = where + ["ts >= ?"], args + [since]
        if where:
            q += " WHERE " + " AND ".join(where)
        with self.lock:
            rows = self.db.execute(q + " ORDER BY id DESC LIMIT ?", (*args, limit)).fetchall()
        return [{"ts": ts, "conversation_id": cid, "role": role, "text": text, "card": card or ""}
                for ts, cid, role, text, card in reversed(rows)]
