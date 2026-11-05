#!/usr/bin/env python3
"""show_spend.py counts the anthropic warm-ups from the journal beside history.db's answers: until 2026-11-03
the warm-ups were most of the bill and a history.db-only sum undercounted it by more than half."""
import datetime
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server"))
import show_spend  # noqa: E402


class ShowSpendTest(unittest.TestCase):
    def test_warm_up_lines_are_counted_by_stockholm_day(self):
        lines = ["2026-11-02T23:30:00+00:00 jarvis python3[1]: anthropic warm-up: {'cache_read': 0, 'cache_write': 24763,"
                 " 'input': 79, 's': 1.73, 'cost_usd': 0.062066}",
                 "2026-11-03T10:59:44+01:00 jarvis python3[2]: anthropic warm-up: {'connection_only': True, 's': 0.36,"
                 " 'cost_usd': 0.0}",
                 "2026-11-03T11:00:00+01:00 jarvis python3[2]: POST /warm"]
        got = show_spend.warmups(datetime.date(2026, 11, 1), lines)
        self.assertEqual(dict(got), {datetime.date(2026, 11, 3): [2, 1, 0.062066]})   # 23:30 UTC is the 3rd here

    def test_answers_sum_per_day_and_brain(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "h.db"
            con = sqlite3.connect(db)
            con.execute("create table messages (ts text, role text, brain text, cost_usd real)")
            con.executemany("insert into messages values (?,?,?,?)", [
                ("2026-11-02T09:00:00+00:00", "jarvis", "anthropic claude-sonnet-5-5", 0.02),
                ("2026-11-02T10:00:00+00:00", "jarvis", "anthropic claude-sonnet-5-5", 0.03),
                ("2026-11-02T10:00:00+00:00", "user", None, 0.0),
                ("2026-11-02T11:00:00+00:00", "jarvis", "direct", 0.0)])
            con.commit()
            got = show_spend.answers(datetime.date(2026, 11, 1), db)
        [(key, (n, usd))] = got.items()
        self.assertEqual((key, n), ((datetime.date(2026, 11, 2), "anthropic claude-sonnet-5-5"), 2))
        self.assertAlmostEqual(usd, 0.05)


if __name__ == "__main__":
    unittest.main()
