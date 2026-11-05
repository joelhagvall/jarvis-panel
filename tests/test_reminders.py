#!/usr/bin/env python3
"""Reminders (server/reminders.py): added, listed and cancelled only when Joel asks, at a time or in a while,
kept across a restart, and handed out when due.
Run: python3 -m unittest tests/test_reminders.py"""
import sys
import tempfile
import unittest
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import reminders as rem  # noqa: E402

TZ = rem.TZ
NOW = datetime(2026, 10, 22, 16, 30, tzinfo=TZ)


class RemindersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hallpanel-reminders-"))
        self.r = rem.Reminders(self.tmp / "h.db")
        self.addCleanup(self.r.db.close)

    def tool(self, said, **args):
        self.r.said = said
        return self.r.tool(args, now=NOW)

    def test_add_at_a_time_and_in_a_while(self):
        err, text = self.tool("Påminn mig klockan sex att ta ut tvätten", text="Dags att ta ut tvätten.",
                              at="2026-10-22T18:00")
        self.assertFalse(err)
        self.assertEqual(text, "Sparad som påminnelse 1, 18:00: Dags att ta ut tvätten.")
        err, text = self.tool("sätt en timer på tio minuter", text="Tio minuter har gått.", in_minutes=10)
        self.assertFalse(err)
        self.assertIn("16:40", text)
        self.assertEqual(self.r.lines(NOW), ["16:40 Tio minuter har gått.", "18:00 Dags att ta ut tvätten."])

    def test_only_when_joel_asks(self):
        err, text = self.tool("vad är huvudstaden i Australien", text="Hej", in_minutes=5)
        self.assertTrue(err)
        self.assertEqual(self.r.pending(), [])
        self.tool("påminn mig om fem minuter", text="Hej", in_minutes=5)
        err, _ = self.tool("hur mår du", action="cancel", id=1)
        self.assertTrue(err)
        self.assertEqual(len(self.r.pending()), 1)

    def test_bad_times(self):
        said = "påminn mig"
        self.assertIn("redan varit", self.tool(said, text="x", at="2026-10-22T16:00")[1])
        self.assertIn("YYYY-MM-DD", self.tool(said, text="x", at="i morgon")[1])
        self.assertIn("ett år", self.tool(said, text="x", at="2028-01-01T10:00")[1])
        self.assertIn("at eller in_minutes", self.tool(said, text="x")[1])
        self.assertIn("mer än noll", self.tool(said, text="x", in_minutes=0)[1])

    def test_english_add_cancel_and_unrequested_write(self):
        self.assertTrue(self.tool("What's tomorrow?", text="Hello", in_minutes=5)[0])
        self.assertFalse(self.tool("Remind me in five minutes", text="Take out the laundry.", in_minutes=5)[0])
        self.assertTrue(self.tool("What reminders do I have?", action="cancel", id=1)[0])
        self.assertFalse(self.tool("Cancel that reminder", action="cancel", id=1)[0])
        self.assertEqual(self.r.pending(), [])

    def test_list_cancel_and_days(self):
        self.tool("påminn mig", text="Ring mamma.", at="2026-10-23T09:00")
        self.tool("påminn mig", text="Betala hyran.", at="2026-10-30T09:00")
        self.assertEqual(self.tool("vilka påminnelser har jag", action="list")[1],
                         "Påminnelser som väntar: [1] i morgon 09:00: Ring mamma.; [2] fre 30/10 09:00: Betala hyran.")
        self.assertEqual(self.tool("ta bort den om mamma", action="cancel", id=1), (False, "Påminnelse 1 är borttagen."))
        self.assertTrue(self.tool("ta bort den igen", action="cancel", id=1)[0])
        self.assertEqual([t for _, _, t in self.r.pending()], ["Betala hyran."])

    def test_same_reminder_twice_is_kept_once(self):
        self.tool("påminn mig", text="Ring mamma.", at="2026-10-23T09:00")
        self.assertEqual(self.tool("påminn mig", text="ring mamma.", at="2026-10-23T09:00"),
                         (False, "Den påminnelsen finns redan."))
        self.assertEqual(len(self.r.pending()), 1)

    def test_due_and_done_survive_a_restart(self):
        self.tool("påminn mig", text="Nu.", in_minutes=1)
        self.tool("påminn mig", text="Sen.", in_minutes=60)
        again = rem.Reminders(self.tmp / "h.db")
        self.addCleanup(again.db.close)
        later = (NOW + timedelta(minutes=2)).astimezone(timezone.utc)
        due = again.due(later)
        self.assertEqual([t for _, _, t in due], ["Nu."])
        again.mark_done(due[0][0])
        self.assertEqual(again.due(later), [])
        self.assertEqual([t for _, _, t in again.pending()], ["Sen."])

    def test_two_connections_cannot_claim_the_same_reminder(self):
        n = self.r.add(NOW, "Nu.")
        again = rem.Reminders(self.tmp / "h.db")
        self.addCleanup(again.db.close)
        self.assertTrue(self.r.claim(n))
        self.assertFalse(again.claim(n))
        self.assertEqual(again.due(NOW), [])
        self.r.release(n)
        self.assertTrue(again.claim(n))

    def test_old_database_migrates_without_losing_or_replaying_a_claim(self):
        path=self.tmp/'old.db'
        with sqlite3.connect(path) as db:
            db.execute('CREATE TABLE reminders (id INTEGER PRIMARY KEY, ts TEXT, due TEXT, text TEXT, said TEXT, done TEXT, cancelled TEXT)')
            db.execute('INSERT INTO reminders VALUES (1, ?, ?, ?, NULL, NULL, NULL)',(NOW.isoformat(),NOW.isoformat(),'Bevarad.'))
        first=rem.Reminders(path);self.addCleanup(first.db.close)
        self.assertTrue(first.claim(1))
        second=rem.Reminders(path);self.addCleanup(second.db.close)
        self.assertEqual(second.due(NOW),[])
        self.assertEqual(second.pending()[0][2],'Bevarad.')
        self.assertIn('Leverans ej bekräftad',second.lines(NOW)[0])


    def test_panel_rows_carry_ids_clean_text_and_the_rest(self):
        """Hemmet's reminder page (rem_list): the id Ta bort sends, separators taken out of the text, "m|N" for more."""
        self.assertEqual(self.r.panel_rows(NOW), "")
        first = self.r.add(NOW + timedelta(hours=1), "Ring Pelle | om nyckeln; snart")
        self.r.add(NOW + timedelta(days=1), "Tvättid")
        self.assertEqual(self.r.panel_rows(NOW), f"r|{first}|17:30|Ring Pelle om nyckeln, snart|0;r|{first + 1}|i morgon 16:30|Tvättid|0")
        self.assertTrue(self.r.claim(first))
        self.assertTrue(self.r.panel_rows(NOW).startswith(f"r|{first}|17:30|Ring Pelle om nyckeln, snart|1;"))
        for i in range(rem.PANEL_ROWS):
            self.r.add(NOW + timedelta(days=2, minutes=i), f"Extra {i}")
        rows = self.r.panel_rows(NOW).split(";")
        self.assertEqual(len(rows), rem.PANEL_ROWS + 1)
        self.assertEqual(rows[-1], "m|2")
        self.assertTrue(self.r.cancel(first))
        self.assertNotIn(f"r|{first}|", self.r.panel_rows(NOW))

if __name__ == "__main__":
    unittest.main()
