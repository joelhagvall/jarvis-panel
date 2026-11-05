"""Every Home Assistant action the panel's firmware calls must accept a call that asks for no response.

ESPHome's homeassistant.action sends a plain call. A jarvis_agent service registered with SupportsResponse.ONLY
rejects that before it runs, silently: the panel's Spotify volume slider did nothing (2026-10-28) while Jarvis's
voice tool, which asks for the response, worked."""
from pathlib import Path
import re
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PanelActionsTest(unittest.TestCase):
    def test_panel_actions_are_not_response_only(self):
        init = (ROOT / "ha/custom_components/jarvis_agent/__init__.py").read_text()
        registered = dict(re.findall(r'async_register\(\s*DOMAIN,\s*"(\w+)",[^)]*?supports_response=SupportsResponse\.(\w+)',
                                     init, re.S))
        sources = [*(ROOT / "styles/glass-v2").glob("*.py"), ROOT / "board.py"]
        called = set()
        for path in sources:
            called |= set(re.findall(r'"action":\s*f?"jarvis_agent\.(\w+)"', path.read_text()))
        self.assertIn("speaker_volume", called)
        self.assertIn("media_control", called)
        for name in sorted(called):
            with self.subTest(service=name):
                self.assertNotEqual(registered.get(name), "ONLY", f"jarvis_agent.{name} is response-only")


    def test_quick_timers_and_wakeup_buttons_call_registered_actions(self):
        # 2026-11-02: Hemmet's 5/10/15 min and Avbryt, and Snooze/Stopp by the tab bar.
        init = (ROOT / "ha/custom_components/jarvis_agent/__init__.py").read_text()
        services = (ROOT / "ha/custom_components/jarvis_agent/services.yaml").read_text()
        gen = (ROOT / "styles/glass-v2/gen.py").read_text()
        for name in ("timer_start", "timer_cancel", "wakeup_snooze", "wakeup_stop"):
            with self.subTest(service=name):
                self.assertIn(f'"jarvis_agent.{name}"', gen)
                self.assertRegex(init, rf'"{name}"|\b{name.split("_")[0]}_\w+')
                self.assertIn(f"{name}:", services)
        self.assertIn('async_register(DOMAIN, "timer_start"', init)
        self.assertIn('async_register(DOMAIN, "timer_cancel"', init)

    def test_quick_timer_widths_agree(self):
        # gen.py sets h_timer's first width, voice.py's QUICK_SHOW every later one: the same two numbers.
        import importlib.util
        import sys
        sys.path.insert(0, str(ROOT))
        from board_config import voice
        spec = importlib.util.spec_from_file_location("gen_widths", ROOT / "styles/glass-v2/gen.py")
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        self.assertIn(f"running ? {gen.TIMER_TEXT_W[False]} : {gen.TIMER_TEXT_W[True]}", voice.QUICK_SHOW)


if __name__ == "__main__":
    unittest.main()
