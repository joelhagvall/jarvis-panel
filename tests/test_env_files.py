#!/usr/bin/env python3
"""The NAME=value files in ~/.config/hallpanel (server/agents/env_files.py): the last assignment wins, an empty
one too, one pair of matching quotes goes, and a key already in the environment stays.

  python3 -m unittest tests/test_env_files.py
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import env_files  # noqa: E402


class EnvFilesTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        home = Path(tmp.name)
        (home / ".config" / "hallpanel").mkdir(parents=True)
        (home / ".config" / "hallpanel" / "server.env").write_text(
            '# comment\nJARVIS_CALENDAR_MCP=http://a/mcp\nJARVIS_CALENDAR_MCP=\nKEY="quoted"\n'
            "ODD='x\"\nno equals sign\n")
        patch = mock.patch.object(Path, "home", return_value=home)
        patch.start()
        self.addCleanup(patch.stop)

    def test_last_assignment_wins_and_quotes_go(self):
        env = env_files.env_file("server.env")
        self.assertEqual(env["JARVIS_CALENDAR_MCP"], "")
        self.assertEqual(env["KEY"], "quoted")
        self.assertEqual(env["ODD"], "'x\"")                   # quotes that do not match stay
        self.assertNotIn("no equals sign", env)
        self.assertEqual(env_files.env_file("missing.env"), {})

    def test_load_key_file_keeps_the_environment(self):
        with mock.patch.dict(os.environ, {"KEY": "from the shell"}):
            env_files.load_key_file("KEY", "server.env")
            self.assertEqual(os.environ["KEY"], "from the shell")
        with mock.patch.dict(os.environ):
            os.environ.pop("KEY", None)
            os.environ.pop("JARVIS_CALENDAR_MCP", None)
            env_files.load_key_file("KEY", "server.env")
            self.assertEqual(os.environ["KEY"], "quoted")
            env_files.load_key_file("JARVIS_CALENDAR_MCP", "server.env")   # empty: not set
            self.assertNotIn("JARVIS_CALENDAR_MCP", os.environ)


if __name__ == "__main__":
    unittest.main()
