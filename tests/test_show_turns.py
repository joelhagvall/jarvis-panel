#!/usr/bin/env python3
"""server/show_turns.py against a temporary history.db built by the server's own History class (no Jarvis)."""
import contextlib
import io
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import show_turns  # noqa: E402


class ShowTurnsTest(unittest.TestCase):
    def setUp(self):
        import hallpanel_server as hs
        self.tmp = tempfile.TemporaryDirectory()
        self.db = Path(self.tmp.name) / "h.db"
        h = hs.History(self.db)
        h.add("c", "user", "Vad är klockan?")
        reply = h.add("c", "jarvis", "Den är tio.", True, {"total_ms": 2100})
        h.turn("a" * 16, started_ms_ago=4000, stt="text", language="sv", recognitions=1, reply_id=reply,
               reply_ok=1, tts="complete", sentences=1, sentences_failed=0)
        h.turn("b" * 16, stt="no_speech", listen_ms=5000)
        h.turn("c" * 16, increment=("recognitions",), stt_error="Unavailable: Groq answered HTTP 503")
        h.turn("c" * 16, stt="failed")
        h.turn("d" * 16, stt="text", tts="stopped", sentences=1)
        self.hs = hs

    def tearDown(self):
        self.tmp.cleanup()

    def test_outcomes_and_daily_summary(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            show_turns.main(["--db", str(self.db)])
        text = out.getvalue()
        self.assertIn("ok: Vad är klockan?", text)
        self.assertIn("inget att svara på", text)
        self.assertIn("text-tolkning misslyckades [Unavailable: Groq answered HTTP 503]", text)
        self.assertIn("uppläsningen avbröts", text)
        self.assertIn("per dag", text)
        last = text.strip().splitlines()[-1].split()
        self.assertEqual(last[1], "4")                       # four turns today

    def test_model_tools_lists_one_tool_model_answers_by_tool(self):
        # How the direct paths for "spela X" and the radio were found (2026-10-28, 2026-10-30).
        h = self.hs.History(self.db)
        for question, tools, brain in (("Spela P3.", [{"name": "radio", "ms": 500}], "anthropic claude"),
                                       ("Spela P2.", [{"name": "radio", "ms": 400}], "anthropic claude"),
                                       ("Stoppa musiken.", [{"name": "spotify", "ms": 100}], "direct"),
                                       ("Väder och kalender?", [{"name": "vader"}, {"name": "agenda"}], "anthropic claude")):
            h.add("m" + question, "user", question)
            h.add("m" + question, "jarvis", "Ok.", True, {"brain": brain, "total_ms": 2500, "tools": tools})
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            show_turns.main(["--db", str(self.db), "--model-tools"])
        text = out.getvalue()
        self.assertIn("radio: 2", text)
        self.assertIn("Spela P3.", text)
        self.assertNotIn("Stoppa musiken.", text)            # already direct
        self.assertNotIn("Väder och kalender?", text)        # two kinds of tool

    def test_increment_counts_and_old_database_without_the_table(self):
        import sqlite3
        con = sqlite3.connect(self.db)
        self.assertEqual(con.execute("SELECT recognitions FROM voice_turns WHERE id = ?", ("c" * 16,)).fetchone()[0], 1)
        con.close()
        empty = Path(self.tmp.name) / "old.db"
        sqlite3.connect(empty).execute("CREATE TABLE messages (id INTEGER PRIMARY KEY)").connection.close()
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            show_turns.main(["--db", str(empty)])
        self.assertIn("Inga röstturer", out.getvalue())


if __name__ == "__main__":
    unittest.main()
