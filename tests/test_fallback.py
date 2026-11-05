#!/usr/bin/env python3
"""The fallback brain (server/hallpanel_server.py): when the brain's API fails before anything was done, the
question goes to the other company's brain; after a booking or a push it does not. No network.

  python3 -m unittest tests/test_fallback.py
"""
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "server" / "agents"))
import hallpanel_server as hs  # noqa: E402
import screen  # noqa: E402


class FakeAgent:
    early = {"flygplan", "vader"}

    def __init__(self, label, tools=(), fail=None, reply="Svar."):
        self.label, self.tools, self.fail, self.reply = label, tools, fail, reply
        self.last_tools, self.screen, self.asked = [], screen.Screen(), 0

    def fresh_agenda(self):
        return None

    def fresh_home(self):
        return None

    def wait_for_snapshot(self, timeout):
        return 0.0

    def ask(self, prompt, on_text=None, instructions=None, on_tools=None, language=None):
        self.asked += 1
        self.prompt = prompt
        self.last_tools = [{"name": name, "ms": 1, "error": False, "round": 1} for name in self.tools]
        if self.fail:
            on_text("Genast. Jag kollar.")
            raise RuntimeError(self.fail)
        on_text(self.reply)
        return self.reply, 0.001, {"input_tokens": 1, "output_tokens": 1, "steps": 1}


class FallbackTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.history = hs.History(Path(self.tmp.name) / "h.db")
        self.saved = hs.AGENT, hs.FALLBACK

    def tearDown(self):
        hs.AGENT, hs.FALLBACK = self.saved
        self.tmp.cleanup()

    def test_the_other_brain_answers_when_the_api_fails_before_anything_was_done(self):
        hs.AGENT = FakeAgent("luna", tools=["flygplan"], fail="OpenAI 503")
        hs.FALLBACK = FakeAgent("anthropic claude-sonnet-5-5", reply="Planet är SAS58E.")
        said = []
        reply, timing = hs.ask_agent(self.history, "Vilket plan?", said.append)
        self.assertEqual(reply, "Planet är SAS58E.")
        self.assertEqual(timing["brain"], "anthropic claude-sonnet-5-5")
        self.assertEqual(said, ["Genast. Jag kollar.", "\n", "Planet är SAS58E."])

    def test_no_second_brain_after_a_booking(self):
        hs.AGENT = FakeAgent("luna", tools=["add_event"], fail="OpenAI 503")
        hs.FALLBACK = FakeAgent("anthropic claude-sonnet-5-5")
        with self.assertRaises(RuntimeError):
            hs.ask_agent(self.history, "Boka bio", [].append)
        self.assertEqual(hs.FALLBACK.asked, 0)                  # the booking is not made twice

    def test_without_a_fallback_the_failure_stands(self):
        hs.AGENT, hs.FALLBACK = FakeAgent("luna", fail="OpenAI 503"), False
        with self.assertRaises(RuntimeError):
            hs.ask_agent(self.history, "Hej", [].append)


    def test_context_is_the_last_half_hour_only(self):
        self.history.add("c", "jarvis", "Elen kostar 10 till 15 kronor.")
        self.history.add("c", "jarvis", "Ska jag lägga in den?")
        old = (datetime.now(timezone.utc) - timedelta(minutes=40)).isoformat(timespec="seconds")
        self.history.db.execute("UPDATE messages SET ts = ? WHERE text LIKE 'Elen%'", (old,))
        hs.AGENT, hs.FALLBACK = FakeAgent("luna"), False
        hs.ask_agent(self.history, "ja", [].append)
        self.assertIn("Ska jag lägga in den?", hs.AGENT.prompt)
        self.assertNotIn("Elen kostar", hs.AGENT.prompt)

if __name__ == "__main__":
    unittest.main()
