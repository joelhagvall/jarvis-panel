"""Hemmet's reminder page (styles/glass-v2/reminders_page.py, 2026-11-03): its sizes agree with the server and the
C++ header, the Hemmet card opens it, and every action it sends by id is a registered, non-response-only service."""
from pathlib import Path
import importlib.util
import re
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
GLASS = ROOT / "styles/glass-v2"


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module              # gen.config() looks itself up there
    spec.loader.exec_module(module)
    return module


class ReminderPageTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        sys.path.insert(0, str(GLASS))
        sys.path.insert(0, str(ROOT / "server"))
        cls.page = load("reminders_page_test", GLASS / "reminders_page.py")
        cls.gen = load("gen_reminders_test", GLASS / "gen.py")
        import reminders
        cls.reminders = reminders

    def test_sizes_agree_with_the_server_and_the_header(self):
        header = (GLASS / "reminders_view.h").read_text()
        self.assertEqual(self.page.ROWS, self.reminders.PANEL_ROWS)
        self.assertIn(f"constexpr int ROWS = {self.page.ROWS};", header)
        self.assertIn(f"constexpr int TIMERS = {self.page.TIMERS};", header)
        self.assertEqual(self.page.QUICK, self.gen.QUICK_TIMERS)

    def test_the_hemmet_card_opens_the_page_and_the_page_is_built(self):
        cfg = self.gen.config("assets/bg_starry.png")
        pages = {p["id"]: p for p in cfg["lvgl"]["pages"]}
        self.assertIn(self.page.PAGE, pages)
        text = str(pages[self.gen.HOME])
        self.assertIn("reminders_entry", text)
        self.assertIn(f"'id': '{self.page.PAGE}'", text)
        built = str(pages[self.page.PAGE])
        for key in [f"rm_r{k}" for k in range(self.page.ROWS)] + [f"rm_t{i}_stop" for i in range(self.page.TIMERS)] \
                + ["rm_detail", "rm_d_remove", "rm_more", "rm_none", "rm_t_none"]:
            self.assertIn(f"'id': '{key}'", built)
        self.assertTrue(any(s["id"] == "rm_show" for s in cfg["script"]))
        self.assertTrue(any(s.get("id") == "hm_rem_list" for s in cfg["text_sensor"]))
        self.assertIn(str(GLASS / "reminders_view.h"), cfg["esphome"]["includes"])

    def test_actions_sent_by_id_are_registered_and_accept_a_plain_call(self):
        init = (ROOT / "ha/custom_components/jarvis_agent/__init__.py").read_text()
        services = (ROOT / "ha/custom_components/jarvis_agent/services.yaml").read_text()
        import json
        built = json.dumps(self.gen.config("assets/bg_starry.png"), ensure_ascii=False)
        sent = set(re.findall(r'reminders_view::call\(\\"jarvis_agent\.(\w+)', built))
        self.assertEqual(sent, {"reminder_cancel", "timer_add", "timer_cancel"})
        for name in sent:
            with self.subTest(service=name):
                self.assertRegex(init, rf'async_register\(DOMAIN, "{name}", \w+,\s*schema=')
                self.assertIn(f"{name}:", services)

    def test_board_fills_the_timer_rows_before_the_early_return(self):
        sys.path.insert(0, str(ROOT))
        from board_config import voice
        rows = voice.reminder_timer_rows()
        self.assertIn("reminders_view::timer_id[i] = t->id;", rows)
        self.assertIn("if (id(timer_ringing)) return;", voice.TIMERS_SHOW.splitlines()[0])


if __name__ == "__main__":
    unittest.main()
