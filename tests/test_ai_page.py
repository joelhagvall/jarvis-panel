"""The AI page and Jarvis's ai_anvandning tool (styles/glass-v2/ai_page.py, server/agents/ai_usage.py). No panel,
no network."""
import importlib.util
import json
import re
import sys
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server" / "agents"))
import ai_usage  # noqa: E402


def load_page():
    spec = importlib.util.spec_from_file_location("ai_page", ROOT / "styles/glass-v2/ai_page.py")
    page = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(page)
    return page


TOKENS = {"state": "0.01", "last_updated": "2026-11-01T06:28:58+00:00",
          "attributes": {"today_b": 0.01, "today_m": 6, "month_b": 0.01, "prev_month_b": 34.43, "all_b": 35.1,
                         "since": "2026-04-20"}}
CLAUDE_1 = {"state": "27", "attributes": {"five_hour_pct": 2, "five_hour_resets": "2026-11-01T10:29:59+00:00",
                                          "seven_day_resets": "2026-11-05T04:59:59+00:00", "month_b": 0.01}}


class ToolTest(unittest.TestCase):
    def test_summary_has_the_billions_and_the_limits(self):
        out = ai_usage.summary(TOKENS, {"claude_1": CLAUDE_1, "claude_2": {"state": "unknown", "attributes": {}},
                                        "codex": None})
        self.assertEqual(out["tokens_miljarder"]["forra_manaden"], 34.43)
        self.assertEqual(out["konton"]["Claude 1 (Macen)"]["vecka_procent"], 27.0)
        self.assertIsNone(out["konton"]["Claude 2 (Jarvis)"]["vecka_procent"])
        self.assertNotIn("Codex", out["konton"])

    def test_asking_opens_the_page_with_a_fresh_stamp(self):
        states = {"/api/states/sensor.ai_tokens": TOKENS, "/api/states/sensor.ai_claude_1": CLAUDE_1,
                  "/api/states/sensor.ai_claude_2": CLAUDE_1, "/api/states/sensor.ai_codex": CLAUDE_1}
        with mock.patch.object(ai_usage.hass, "get_json", side_effect=states.__getitem__), \
                mock.patch.object(ai_usage.hass, "request") as request, \
                mock.patch.object(ai_usage.time, "time", return_value=1793500000):
            error, text = ai_usage.usage()
        self.assertFalse(error)
        self.assertEqual(json.loads(text)["tokens_miljarder"]["forra_manaden"], 34.43)
        self.assertNotIn("totalt", text)   # no all-time total: the logs reach back unevenly (Joel, 2026-11-01)
        path, body = request.call_args.args
        self.assertEqual(path, "/api/states/sensor.jarvis_open_page")
        self.assertEqual(body["state"], "ai@1793500000")

    def test_a_failed_open_still_answers_and_missing_sensors_are_a_short_error(self):
        with mock.patch.object(ai_usage.hass, "get_json", return_value=TOKENS), \
                mock.patch.object(ai_usage.hass, "request", side_effect=OSError("down")):
            error, _ = ai_usage.usage()
        self.assertFalse(error)
        with mock.patch.object(ai_usage.hass, "get_json", side_effect=OSError("refused")):
            error, text = ai_usage.usage()
        self.assertTrue(error and text.startswith("AI-användningen gick inte"))


class PageTest(unittest.TestCase):
    def test_every_id_the_lambda_fills_is_on_the_page(self):
        page = load_page()
        sys.path.insert(0, str(ROOT / "styles/glass-v2"))
        import gen
        built = json.dumps(page.page(gen._ui()))
        for wid in set(re.findall(r"id\((ai_[a-z0-9_]+)\)", page.FILL)):
            self.assertIn(f'"id": "{wid}"', built, wid)

    def test_the_bars_fit_inside_their_row(self):
        """The 7-day bar was first placed in screen x and cut off at the row's right edge (2026-11-01)."""
        page = load_page()
        for x in page.BAR_X:
            self.assertGreaterEqual(x, 0)
            self.assertLessEqual(x + page.BAR_W, page.ROW_W)
        self.assertGreaterEqual(page.BAR_X[1] - page.BAR_X[0], page.BAR_W + 16)

    def test_the_logos_are_there(self):
        page = load_page()
        for n in set(page.LOGOS):
            self.assertTrue((ROOT / f"styles/glass-v2/assets/{n}.png").is_file(), n)
        self.assertEqual(len(page.LOGOS), page.ACCOUNTS)

    def test_open_ignores_old_requests_and_other_pages(self):
        page = load_page()
        self.assertIn("> 120", page.OPEN)
        self.assertIn('!= "ai"', page.OPEN)

    def test_the_jump_hooks_in_wherever_spelas_nu_does(self):
        page = load_page()
        take = {"lambda": "return panel_music_jump::take() && !id(night);"}
        inner = {"if": {"condition": take, "then": [], "else": [{"lvgl.page.show": {"id": "page_glass_v2"}}]}}
        outer = {"if": {"condition": take, "then": [], "else": [{"delay": "3s"}, inner]}}
        cfg = {"globals": [{"id": "night"}], "lvgl": {"pages": [{"id": "page_glass_v2"}, {"id": "page_hemmet"}]},
               "voice_assistant": {},
               "script": [{"id": "ai_jump", "then": []},
                          {"id": "voice_done", "then": [{"if": {"condition": {"lambda": "return true;"},
                                                                "then": [{"delay": "1s"}, outer]}}]},
                          {"id": "orb_leave", "then": [json.loads(json.dumps(inner))]}]}
        self.assertEqual(page.board(cfg), 3)
        text = json.dumps(cfg)
        self.assertEqual(text.count("panel_ai_jump::take()"), 3)
        self.assertEqual(text.count("panel_music_jump::take()"), 3)


if __name__ == "__main__":
    unittest.main()
