#!/usr/bin/env python3
"""A background picked in Inställningar shows at once (Joel, 2026-11-02: "slipper klicka Klar"): the pick
notes its time, and the sheet closes when the new bg is painted, but only within gen.BG_PICK_WINDOW_MS of a
pick, so NASA's new picture or a change in Home Assistant leaves an open sheet alone. Checks the board's
generated config. Run: python3 -m unittest tests/test_settings_background.py"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "styles" / "glass-v2"))


def find(node, key):
    """Every value under key anywhere in node."""
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from find(v, key)
    elif isinstance(node, list):
        for v in node:
            yield from find(v, key)


class SettingsBackgroundTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = json.loads(subprocess.check_output([sys.executable, str(ROOT / "board.py"), "--voice",
            str(ROOT / "styles/glass-v2/jarvis_orb.py"), "--remote", "http://example.test"], cwd=ROOT / "styles/glass-v2"))

    def test_a_pick_notes_its_time_before_asking_home_assistant(self):
        dropdown = next(d for d in find(self.cfg["lvgl"], "dropdown") if d.get("id") == "s_bg")
        self.assertEqual(dropdown["on_change"][0], {"lambda": "id(bg_picked_ms) = millis();"})
        out = next(d for d in find(self.cfg["lvgl"], "dropdown") if d.get("id") == "s_out")
        self.assertNotIn("bg_picked_ms", json.dumps(out["on_change"]))       # Ljud ut does not close the sheet

    def test_the_sheet_closes_only_after_a_recent_pick_once_bg_is_painted(self):
        import gen
        scripts = {s["id"]: s for s in self.cfg["script"]}
        shown = scripts["bg_shown"]["then"][0]["if"]
        self.assertIn(f"millis() - id(bg_picked_ms) < {gen.BG_PICK_WINDOW_MS}", shown["condition"]["lambda"])
        self.assertIn({"script.execute": "settings_close"}, shown["then"])
        self.assertIn({"lambda": "id(bg_picked_ms) = 0;"}, shown["then"])       # one pick closes it once
        bg = next(im for im in self.cfg["image"] if im["id"] == "bg")
        after_boot = next(step["if"]["then"] for step in bg["on_download_finished"] if "if" in step)
        self.assertIn({"script.execute": "bg_shown"}, after_boot)
        self.assertNotIn("bg_shown", json.dumps(next(step["if"]["else"] for step in bg["on_download_finished"]
                                                     if "if" in step)))         # not at boot
        self.assertIn({"id": "bg_picked_ms", "type": "uint32_t", "initial_value": "0"}, self.cfg["globals"])


if __name__ == "__main__":
    unittest.main()
