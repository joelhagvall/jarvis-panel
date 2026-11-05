"""The tool automatiska_bokningar: when the laundry and Gympass bookers run next, what they book then, and their
status files (Joel, 2054-11-04). Fake status files; nothing runs or books."""
import json
import os
import sys
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "agents"))
import bookers  # noqa: E402


class BookersTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        patch = mock.patch.object(bookers, "STATE_DIR", Path(self.dir.name))
        patch.start()
        self.addCleanup(patch.stop)

    def read(self, when):
        error, text = bookers.read(now=datetime.fromisoformat(when).replace(tzinfo=bookers.TZ))
        self.assertFalse(error)
        return json.loads(text)

    def test_next_runs_and_what_they_book(self):
        r = self.read("2054-11-04T17:00")                       # a Wednesday, the day before the gym run
        self.assertEqual(r["tvättstugan"]["nästa_körning"], "2054-11-07T10:15+01:00")
        self.assertEqual(r["tvättstugan"]["bokar_då"], "2054-11-21 12-15")
        self.assertEqual(r["Gympass"]["nästa_körning"], "2054-11-05T18:00+01:00")
        self.assertEqual(r["Gympass"]["bokar_då"], "2054-11-12 18:00")
        r = self.read("2054-11-12T17:00")                       # Thursday before the run: today counts
        self.assertEqual(r["Gympass"]["nästa_körning"], "2054-11-12T18:00+01:00")

    def test_rhythm_matches_the_booker(self):
        # tvatt_book.py: anchor 2054-10-10, every 14 days, the first rhythm Saturday after the day.
        self.assertEqual(bookers.next_rhythm_saturday(date(2054, 10, 10)), date(2054, 10, 24))
        self.assertEqual(bookers.next_rhythm_saturday(date(2054, 10, 31)), date(2054, 11, 7))
        self.assertEqual(bookers.next_rhythm_saturday(date(2054, 11, 7)), date(2054, 11, 21))

    def test_status_files_in_swedish_and_missing_ones_said(self):
        Path(self.dir.name, "booking-sats.json").write_text(json.dumps(
            {"last_run": "2054-11-04T09:17:33+01:00", "last_result": "Bokat", "next_booking": "Gympass mån 9/11"}))
        r = self.read("2054-11-04T17:00")
        self.assertEqual(r["Gympass"]["senaste_resultat"], "Bokat")
        self.assertEqual(r["Gympass"]["nästa_kända_bokning"], "Gympass mån 9/11")
        self.assertEqual(r["tvättstugan"]["status"], "saknas")


if __name__ == "__main__":
    unittest.main()
