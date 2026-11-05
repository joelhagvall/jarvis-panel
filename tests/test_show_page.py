"""The show_page action in the generated firmware: Jarvis can show a page by voice (board_config/voice.py).

  python3 -m unittest tests/test_show_page.py
"""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def generated():
    return json.loads(subprocess.run([sys.executable, str(ROOT / "board.py"), "--voice", "jarvis_orb.py",
                                      "--remote", "http://example.invalid/assets"], cwd=ROOT / "styles/glass-v2",
                                     check=True, capture_output=True, text=True).stdout)


class ShowPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = generated()
        cls.scripts = {s["id"]: s["then"] for s in cls.cfg["script"]}

    def test_the_action_takes_a_page_id_and_knows_every_page_but_orb_and_ota(self):
        action = next(a for a in self.cfg["api"]["actions"] if a["action"] == "show_page")
        self.assertEqual(action["variables"], {"page": "string"})
        known = [line.split('"')[1] for line in action["then"][0]["lambda"].splitlines() if line.startswith("if (page")]
        pages = {p["id"] for p in self.cfg["lvgl"]["pages"]}
        self.assertEqual(set(known), pages - {"page_orb", "page_ota"})
        # voice_go indexes the same pages in the same order, in one lambda
        go = self.scripts["voice_go"][0]["lambda"]
        self.assertEqual(go.split("pages[")[1].split("{")[1].split("}")[0],
                         ", ".join(f"id({pid})" for pid in known))
        # not on the voice page: at once; on it: when the voice page goes back
        self.assertEqual(action["then"][1]["if"]["then"], [{"script.execute": "voice_go"}])
        self.assertIn("page_orb", action["then"][1]["if"]["condition"]["not"]["lambda"])

    def test_voice_done_goes_to_the_asked_page_before_night_or_the_page_it_came_from(self):
        # now_playing.py's jump to Spelas nu (music that started in this turn) comes first; then the asked page,
        # before night and before the page the voice page was opened from
        def find(actions):
            for a in actions:
                if "if" in a:
                    if a["if"]["condition"] == {"lambda": "return id(voice_to) >= 0;"}:
                        return a["if"]
                    for key in ("then", "else"):
                        if (hit := find(a["if"].get(key, []))):
                            return hit
            return None
        asked = find(self.scripts["voice_done"])
        self.assertIsNotNone(asked)
        self.assertEqual(asked["then"], [{"script.execute": "voice_go"}])
        self.assertIn("night", json.dumps(asked["else"]))
        self.assertIn("voice_from", json.dumps(asked["else"]))

    def test_going_somewhere_leaves_night_and_natt_goes_through_night_on(self):
        go = self.scripts["voice_go"]
        self.assertEqual(len(go), 1, "one lambda: an ESPHome branch per page cost ~5.5 kB internal RAM (2026-11-04)")
        lines = go[0]["lambda"].splitlines()
        self.assertEqual(lines[:2], ["const int to = id(voice_to);", "id(voice_to) = -1;"])
        natt = next(l for l in lines if "night_on" in l)
        self.assertIn("return;", natt)
        rest = "\n".join(lines[lines.index(natt) + 1:])
        self.assertIn("id(night) = false; id(night_hw_off).execute();", rest)
        self.assertIn("id(settings_close).execute();", rest)
        self.assertIn("if (to == 1) id(bar_page) = 1;", rest)
        self.assertIn("LV_SCREEN_LOAD_ANIM_NONE", rest)

    def test_voice_go_adds_no_automation_objects(self):
        # internal RAM: every ESPHome action/condition is a heap object; voice_go must stay a single lambda
        self.assertEqual([list(a) for a in self.scripts["voice_go"]], [["lambda"]])

if __name__ == "__main__":
    unittest.main()
