"""show_history.py: --full prints whole reply texts (2026-11-01: a laundry reply had to be read from the db)."""
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ShowHistoryTest(unittest.TestCase):
    def run_tool(self, *args):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "h.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, ts, role, text, ok)")
            con.execute("INSERT INTO messages(ts, role, text, ok) VALUES ('2026-11-01T16:13:58+00:00', 'jarvis', ?, 1)",
                        ("x" * 100,))
            con.commit()
            con.close()
            return subprocess.run([sys.executable, str(ROOT / "server/show_history.py"), *args, "--db", str(db)],
                                  capture_output=True, text=True, check=True).stdout.splitlines()[-1]

    def test_text_is_cut_unless_full(self):
        self.assertIn("x" * 70, self.run_tool("1"))
        self.assertNotIn("x" * 71, self.run_tool("1"))
        self.assertIn("x" * 100, self.run_tool("1", "--full"))

    def test_one_tools_calls_with_their_args(self):
        with tempfile.TemporaryDirectory() as d:
            db = Path(d) / "h.db"
            con = sqlite3.connect(db)
            con.execute("CREATE TABLE messages(id INTEGER PRIMARY KEY, ts, role, text, ok, tools)")
            tools = '[{"name": "resa", "error": true, "args": "{\\"destination\\": \\"Zinkens damm\\"}", ' \
                    '"result": "Platsen är oklar"}, {"name": "vader", "args": "{}"}]'
            con.execute("INSERT INTO messages(ts, role, text, ok, tools) VALUES ('2026-11-04T10:05:00+00:00', "
                        "'jarvis', 'x', 1, ?)", (tools,))
            con.commit()
            con.close()
            out = subprocess.run([sys.executable, str(ROOT / "server/show_history.py"), "--tool", "resa", "--db", str(db)],
                                 capture_output=True, text=True, check=True).stdout.splitlines()
        self.assertEqual(out, ['11-04 11:05 FEL {"destination": "Zinkens damm"} -> Platsen är oklar', "1 anrop, 1 fel"])


if __name__ == "__main__":
    unittest.main()
