#!/usr/bin/env python3
"""The agenda the warm-up reads is handed to Luna only while fresh, and any calendar change drops it.
No network: the agent runs with the mock tools. Run: python3 -m unittest tests/test_agenda.py"""
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import openai_agent  # noqa: E402
import agent_core  # noqa: E402

AGENDA = "2026-10-22 10:20-10:35 | Att göra | Dammsuga | id 1"


class AgendaTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hallpanel-agenda-")
        self.addCleanup(tmp.cleanup)
        guard = patch.object(agent_core, "GUARD_FILE", Path(tmp.name) / "creates.json")
        guard.start()
        self.addCleanup(guard.stop)
        self.agent = openai_agent.LunaAgent(key="test", mock_tools=True)
        self.agent._ensure_tools()
        self.agent.read_only.add("calendar_list_events")     # as the MCP annotation readOnlyHint says
        self.agent.agenda = (time.monotonic(), AGENDA)

    def test_fresh_agenda_is_handed_over(self):
        text, age, days = self.agent.fresh_agenda()
        self.assertEqual((text, age, days), (AGENDA, 0, openai_agent.AGENDA_DAYS))

    def test_old_agenda_is_not(self):
        self.agent.agenda = (time.monotonic() - openai_agent.AGENDA_MAX_AGE - 1, AGENDA)
        self.assertIsNone(self.agent.fresh_agenda())

    def test_reading_keeps_it_and_writing_drops_it(self):
        self.agent.run_tool("calendar_list_events", {"date": "2026-10-22"})
        self.assertIsNotNone(self.agent.fresh_agenda())
        self.agent.run_tool("calendar_add_event", {"title": "Köp kvarg", "start": "2026-10-22T17:00"})
        self.assertIsNone(self.agent.fresh_agenda())
        self.assertEqual([t["name"] for t in self.agent.last_tools], ["calendar_list_events", "calendar_add_event"])


if __name__ == "__main__":
    unittest.main()
