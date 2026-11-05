"""panelen: Jarvis's tool for the panel's page, background and screen light, without Home Assistant.

  python3 -m unittest tests/test_panel_tool.py
"""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "agents"))
import panel  # noqa: E402


class PanelToolTest(unittest.TestCase):
    def test_spoken_page_names_find_their_page(self):
        for said, pid in (("hemskärmen", "page_glass_v2"), ("gå hemifrån", "page_glass_v2"), ("hem", "page_glass_v2"),
                          ("Hemmet", "page_hemmet"), ("spelas nu", "page_musik"), ("musiken", "page_musik"),
                          ("tavlan", "page_tavla"), ("natt", "page_natt"), ("vädret", "page_vader"),
                          ("kalendern", "page_kalender"), ("påminnelserna", "page_paminnelser"), ("ai", "page_ai")):
            with self.subTest(said=said):
                self.assertEqual(panel.page_for(said)[0], pid)
        self.assertIsNone(panel.page_for("tvättstugan"))
        self.assertIsNone(panel.page_for(""))

    def test_sida_calls_the_panel_through_home_assistant(self):
        with patch.object(panel.hass, "call_service") as call:
            self.assertEqual(panel.control("sida", sida="hemskärmen"), (False, "Panelen visar Gå hemifrån."))
        call.assert_called_once_with("esphome", "hallpanel_show_page", {"page": "page_glass_v2"}, timeout=8)
        error, text = panel.control("sida", sida="tvättstugan")
        self.assertTrue(error)
        self.assertIn("Ingen sida", text)

    def test_a_panel_without_the_action_is_said_plainly(self):
        import urllib.error
        with patch.object(panel.hass, "call_service", side_effect=urllib.error.HTTPError("u", 400, "bad", {}, None)):
            error, text = panel.control("sida", sida="hemmet")
        self.assertTrue(error)
        self.assertIn("firmware", text)

    def test_bakgrund_matches_loosely_and_changes_in_process(self):
        choices = ["NASA", "Växlande", "Stjärnenatten", "Mandelblom", "Dagens målning", "Dagens foto"]
        with patch.object(panel.background, "choices", return_value=choices), \
             patch.object(panel.background, "choose") as choose:
            self.assertEqual(panel.control("bakgrund", val="stjärnenatten"), (False, "Bakgrunden är nu Stjärnenatten."))
            choose.assert_called_once_with("Stjärnenatten")
            self.assertEqual(panel.background_for("mandelblom"), "Mandelblom")
            self.assertIsNone(panel.background_for("dagens"))          # two match: ask, do not guess
            error, text = panel.control("bakgrund", val="regnbåge")
        self.assertTrue(error)
        self.assertIn("Ingen bakgrund", text)

    def test_skarmljus_is_clamped_and_set_in_percent(self):
        with patch.object(panel.hass, "call_service") as call:
            self.assertEqual(panel.control("skarmljus", procent=140), (False, "Skärmljuset står på 100 procent."))
        call.assert_called_once_with("light", "turn_on", {"entity_id": "light.hallpanel_skarmljus", "brightness_pct": 100}, timeout=8)
        self.assertTrue(panel.control("skarmljus")[0])

    def test_status_reads_without_raising_when_home_assistant_is_away(self):
        with patch.object(panel.background, "current", return_value="NASA"), \
             patch.object(panel.background, "choices", return_value=["NASA"]), \
             patch.object(panel.hass, "get_json", side_effect=OSError("down")):
            error, text = panel.control("status")
        self.assertFalse(error)
        self.assertIn("Bakgrund: NASA", text)


if __name__ == "__main__":
    unittest.main()
