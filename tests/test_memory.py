#!/usr/bin/env python3
"""What Jarvis remembers (server/memory.py): the profile's age, notes saved and forgotten only when Joel
asks for it, end dates, the limit, and that the memory follows the fixed instructions in the prompt.
Run: python3 -m unittest tests/test_memory.py"""
import json
import sys
import tempfile
import unittest
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "server" / "agents"))
import memory as mem


class MemoryTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hallpanel-memory-"))
        self.profile = self.tmp / "profile.json"
        self.profile.write_text(json.dumps({"born": "1990-03-15", "facts": ["Heter Demo Person."]}))
        self.m = mem.Memory(self.tmp / "h.db", self.profile)
        self.addCleanup(self.m.db.close)

    def remember(self, said, **args):
        self.m.said = said
        return self.m.remember(args)

    def test_age_from_the_birth_date(self):
        self.assertIn("Född 15 mars 1990, alltså 36 år.", self.m.profile(date(2026, 10, 22)))
        self.assertIn("Född 15 mars 1990, alltså 36 år.", self.m.profile(date(2027, 3, 14)))
        on_the_day = self.m.profile(date(2027, 3, 15))
        self.assertIn("Född 15 mars 1990, alltså 37 år.", on_the_day)
        self.assertIn("I dag är det hans födelsedag: han fyller 37.", on_the_day)

    def test_no_profile_and_no_notes_add_nothing(self):
        empty = mem.Memory(self.tmp / "e.db", self.tmp / "missing.json")
        self.addCleanup(empty.db.close)
        self.assertEqual(empty.block(), "")

    def test_saved_only_when_asked_now(self):
        err, text = self.remember("Var ligger tvättnyckeln?", text="Tvättnyckeln ligger i den blå skålen.")
        self.assertTrue(err)
        self.assertEqual(self.m.notes(), [])
        err, text = self.remember("Kom ihåg att tvättnyckeln ligger i den blå skålen",
                                  text="Tvättnyckeln ligger i den blå skålen.")
        self.assertFalse(err)
        self.assertIn("Sparad som lapp 1", text)
        self.assertIn("[1] Tvättnyckeln ligger i den blå skålen.", self.m.block())
        err, text = self.remember("komihåg det där igen", text="tvättnyckeln ligger i den blå skålen.")
        self.assertEqual((err, text), (False, "Den finns redan som lapp 1."))
        self.assertEqual(len(self.m.notes()), 1)

    def test_forgotten_only_when_asked_now(self):
        self.remember("kom ihåg att jag gillar te", text="Han gillar te.")
        self.m.said = "vad kommer du ihåg?"
        self.assertTrue(self.m.forget({"id": 1})[0])
        self.assertEqual(len(self.m.notes()), 1)
        self.m.said = "glöm det om teet"
        self.assertEqual(self.m.forget({"id": 1}), (False, "Glömde lapp 1: Han gillar te."))
        self.assertEqual(self.m.notes(), [])
        self.assertEqual(len(self.m.notes(everything=True)), 1)       # the row stays
        self.assertTrue(self.m.forget({"id": 1})[0])                  # already forgotten

    def test_end_date(self):
        friday = mem.today() + timedelta(days=2)
        err, text = self.remember("kom ihåg att jag är i Göteborg till fredag", text="Han är i Göteborg.",
                                  till=friday.isoformat())
        self.assertFalse(err)
        self.assertIn("gäller till och med", self.m.block(friday))
        self.assertEqual(self.m.block(friday + timedelta(days=1)).count("Göteborg"), 0)
        self.assertTrue(self.remember("kom ihåg", text="Gammalt.", till="2020-01-01")[0])
        self.assertTrue(self.remember("kom ihåg", text="Fel.", till="på fredag")[0])

    def test_english_save_forget_and_unrequested_write(self):
        self.assertTrue(self.remember("Where are the keys?", text="Keys in the bowl.")[0])
        self.assertFalse(self.remember("Remember that the keys are in the bowl", text="Keys in the bowl.")[0])
        self.m.said = "Where are the keys?"
        self.assertTrue(self.m.forget({"id": 1})[0])
        self.m.said = "Forget the note about the keys"
        self.assertFalse(self.m.forget({"id": 1})[0])
        self.assertEqual(self.m.notes(), [])

    def test_limit_and_length(self):
        self.assertTrue(self.remember("kom ihåg", text="x" * (mem.MAX_CHARS + 1))[0])
        for i in range(mem.MAX_NOTES):
            self.assertFalse(self.remember("kom ihåg", text=f"Lapp {i}.")[0])
        err, text = self.remember("kom ihåg", text="En till.")
        self.assertTrue(err)
        self.assertIn("fullt", text)

    def test_follows_the_fixed_instructions(self):
        import hallpanel_server as hs
        hs.MEMORY = self.m
        try:
            self.remember("kom ihåg att jag gillar te", text="Han gillar te.")
            full = hs.instructions()
            self.assertTrue(full.startswith(hs.INSTRUCTIONS))
            self.assertIn("Heter Demo Person.", full)
            self.assertIn("[1] Han gillar te.", full)
        finally:
            hs.MEMORY = None

    def test_agent_offers_the_tools_without_dropping_the_agenda(self):
        from agent_core import ToolAgent
        agent = ToolAgent()
        agent._init_tools(None, None, None, None)
        agent.extra_tools = self.m.tools()
        agent._ensure_tools()
        self.assertTrue({"kom_ihag", "glom"} <= {t["name"] for t in agent.tools})
        agent.agenda = (0, "agenda")
        self.m.said = "kom ihåg att jag gillar te"
        self.assertFalse(agent.run_tool("kom_ihag", {"text": "Han gillar te."})[0])
        self.assertEqual(agent.agenda, (0, "agenda"))


if __name__ == "__main__":
    unittest.main()
