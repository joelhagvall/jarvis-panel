#!/usr/bin/env python3
"""ask_agent in server/hallpanel_server.py with a fake in-process agent, no network: the question is stored
off the request path and is not its own context, a running warm-up is waited for briefly, the language and
the rounds' timings travel through, and the "God morgon" rule holds whether or not the question is stored.

  python3 -m unittest tests/test_ask_agent.py
"""
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import Mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "server" / "agents"))
import hallpanel_server as hs  # noqa: E402


class FakeAgent:
    label, early, home = "fake brain", set(), None

    def __init__(self, reply="Svar.", fail=None, waits=0.12):
        self.reply, self.fail, self.waits = reply, fail, waits
        self.last_tools, self.screen, self.asked = [], NS(encoded=lambda: ""), []
        self.last_rounds = [{"ms": 900, "first_ms": 850, "out": 40, "tools": 0}]

    def fresh_agenda(self):
        return None

    def fresh_home(self):
        return None

    def wait_for_snapshot(self, timeout):
        self.asked.append(("wait", timeout))
        return self.waits

    def ask(self, prompt, on_text=None, instructions=None, on_tools=None, language=None):
        self.asked.append((prompt, language))
        if self.fail:
            raise RuntimeError(self.fail)
        on_text(self.reply)
        return self.reply, 0.001, {"input_tokens": 1, "output_tokens": 1, "steps": 1}


class AskAgentTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = hs.History(Path(self.tmp.name) / "h.db")
        self.saved = hs.AGENT, hs.FALLBACK
        hs.AGENT, hs.FALLBACK = FakeAgent(), False

    def tearDown(self):
        hs.AGENT, hs.FALLBACK = self.saved
        self.tmp.cleanup()

    def rows(self):
        return [(m["role"], m["text"]) for m in self.history.latest(10)]

    def test_the_question_is_stored_here_and_is_not_its_own_context(self):
        self.history.add("c", "user", "Hej")
        self.history.add("c", "jarvis", "Hej själv.")
        said = []
        reply, timing = hs.ask_agent(self.history, "Vilket plan?", said.append, language="en", store="c")
        self.assertEqual((reply, said), ("Svar.", ["Svar."]))
        prompt, language = hs.AGENT.asked[-1]
        self.assertEqual(language, "en")
        self.assertIn("Joel: Hej\nJarvis: Hej själv.", prompt)               # the earlier talk is context
        self.assertEqual(prompt.count("Vilket plan?"), 1)                     # the question itself only once
        self.assertEqual(self.rows(), [("user", "Hej"), ("jarvis", "Hej själv."), ("user", "Vilket plan?")])
        self.assertEqual((timing["snapshot_wait_ms"], timing["rounds"][0]["first_ms"]), (120, 850))
        self.assertEqual(hs.AGENT.asked[0], ("wait", hs.SNAPSHOT_WAIT))

    def test_without_store_the_caller_owns_the_row(self):
        hs.ask_agent(self.history, "Hej", [].append)
        self.assertEqual(self.rows(), [])

    def test_cached_weather_reaches_the_first_model_request_and_keeps_the_compound_question(self):
        question = "Hur blir vädret imorgon och behöver jag paraply?"
        hs.AGENT.home = NS(prompt_forecast=lambda text: ('{"days":[{"date":"2026-10-26"}]}', 12))
        hs.ask_agent(self.history, question, [].append, language="sv")
        prompt, _ = hs.AGENT.asked[-1]
        self.assertIn('"date":"2026-10-26"', prompt)
        self.assertIn("hämtad för 12 sekunder sedan", prompt)
        self.assertEqual(prompt.count(question), 1)
        self.assertEqual(len([call for call in hs.AGENT.asked if call[0] != "wait"]), 1)

    def test_the_question_is_stored_even_when_the_agent_fails(self):
        hs.AGENT = FakeAgent(fail="API 503")
        with self.assertRaisesRegex(RuntimeError, "API 503"):
            hs.ask_agent(self.history, "Hej", [].append, store="c")
        self.assertEqual(self.rows(), [("user", "Hej")])

    def test_a_quick_reply_stores_the_question_too(self):
        reply, timing = hs.ask_agent(self.history, "Vad är klockan?", [].append, store="c")
        self.assertTrue(reply.startswith("Klockan är"))
        self.assertEqual((timing["brain"], timing["snapshot_wait_ms"]), ("direct", 0))
        self.assertEqual(hs.AGENT.asked, [])
        self.assertEqual(self.rows(), [("user", "Vad är klockan?")])

    def test_a_quick_command_runs_its_tool_and_skips_the_model(self):
        ran = []
        def command(name, args, language=None):
            ran.append((name, args, language))
            hs.AGENT.last_tools = [{"name": name, "ms": 8, "error": False, "round": 0}]
            return "Radion är av."
        hs.AGENT.command = command
        hs.AGENT.wait_for_snapshot = Mock(side_effect=AssertionError("a command waits for no snapshot"))
        said = []
        reply, timing = hs.ask_agent(self.history, "Stäng av radion.", said.append, store="c")
        self.assertEqual((reply, said, ran), ("Radion är av.", [reply], [("radio", {"action": "stop"}, None)]))
        self.assertEqual((timing["brain"], timing["steps"], [t["name"] for t in timing["tools"]]),
                         ("direct", 0, ["radio"]))
        self.assertEqual(hs.AGENT.asked, [])
        self.assertEqual(self.rows(), [("user", "Stäng av radion.")])

    def test_fresh_calendar_does_not_wait_for_unrelated_warmup_or_call_model(self):
        today = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
        hs.AGENT.fresh_agenda = lambda: (f"{today} 13:00-14:00 | Jobb | Möte | id 1", 1, 8)
        hs.AGENT.wait_for_snapshot = Mock(side_effect=AssertionError("unrelated slow warm-up"))
        said = []
        reply, timing = hs.ask_agent(self.history, "Vad har jag i kalendern idag?", said.append, store="c")
        self.assertEqual((reply, said), ("I dag har du Möte klockan 13:00.", [reply]))
        self.assertEqual((timing["brain"], timing["snapshot_wait_ms"]), ("direct", 0))
        self.assertEqual(hs.AGENT.asked, [])
        self.assertEqual(self.rows(), [("user", "Vad har jag i kalendern idag?")])

    def test_missing_calendar_can_become_a_direct_reply_after_bounded_warmup(self):
        today = datetime.now(ZoneInfo("Europe/Stockholm")).date().isoformat()
        snapshot = (f"{today} 13:00-14:00 | Jobb | Möte | id 1", 1, 8)
        hs.AGENT.fresh_agenda = Mock(side_effect=[None, snapshot])
        reply, timing = hs.ask_agent(self.history, "Vad har jag i kalendern idag?", [].append)
        self.assertEqual(reply, "I dag har du Möte klockan 13:00.")
        self.assertEqual((timing["brain"], timing["snapshot_wait_ms"]), ("direct", 120))
        self.assertEqual(hs.AGENT.asked, [("wait", hs.SNAPSHOT_WAIT)])

    def test_the_log_line_shows_the_rounds_and_the_wait(self):
        line = hs.log_line({"brain": "b", "first_token_ms": 1, "total_ms": 2, "snapshot_wait_ms": 120,
                            "rounds": [{"ms": 900, "first_ms": 850, "out": 40}, {"ms": 1300, "first_ms": 1290, "out": 60}]})
        self.assertIn("model 900 ms (first 850, out 40) + 1300 ms (first 1290, out 60)", line)
        self.assertIn("waited 120 ms for the warm-up", line)
        self.assertNotIn("waited", hs.log_line({"brain": "b", "snapshot_wait_ms": 0}))

    def test_tool_results_stay_on_jarvis(self):
        # history.db keeps each tool's result start; the reply streamed to Home Assistant never carries it
        timing = {"brain": "b", "tools": [{"name": "spotify", "ms": 500, "result": "Spelar Kent.",
                                           "args": '{"query": "Kent"}'}]}      # args too, since 2026-11-01
        self.assertEqual(hs.public_timing(timing)["tools"], [{"name": "spotify", "ms": 500}])
        self.assertEqual(timing["tools"][0]["result"], "Spelar Kent.")      # the stored meta is untouched
        self.assertIsNone(hs.public_timing(None))

    def test_god_morgon_whether_or_not_the_question_is_stored_yet(self):
        at = lambda t: datetime.fromisoformat(f"2026-10-22T{t}").replace(tzinfo=ZoneInfo("Europe/Stockholm"))
        self.assertTrue(hs.first_question_this_morning(self.history, at("07:30:00"), stored=False))
        ts = at("07:30:00").astimezone(hs.timezone.utc).isoformat(timespec="seconds")
        self.history.db.execute("INSERT INTO messages (ts, conversation_id, role, text, ok) VALUES (?, 'c', 'user', 'x', 1)", (ts,))
        self.assertTrue(hs.first_question_this_morning(self.history, at("07:30:05")))
        self.assertFalse(hs.first_question_this_morning(self.history, at("07:45:00"), stored=False))


if __name__ == "__main__":
    unittest.main()
