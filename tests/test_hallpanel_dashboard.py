"""ha/hallpanel_dashboard.py keeps the old dashboard and never drops a card added by hand. No Home Assistant."""
from pathlib import Path
import unittest

SOURCE = (Path(__file__).resolve().parents[1] / "ha/hallpanel_dashboard.py").read_text()
ns = {}
exec(SOURCE.replace("import aiohttp", "aiohttp = None").split('if __name__ == "__main__"')[0], ns)


class DashboardTest(unittest.TestCase):
    def test_a_card_added_by_hand_stops_the_save(self):
        config = ns["CONFIG"]
        self.assertEqual(ns["lost_cards"](config, config), [])
        by_hand = {"views": [{"cards": [*config["views"][0]["cards"], {"type": "entities", "title": "Mitt kort"}]}]}
        self.assertEqual(ns["lost_cards"](by_hand, config), ["Mitt kort"])
        self.assertIn("Månen", [c.get("title") for c in config["views"][0]["cards"]])


if __name__ == "__main__":
    unittest.main()
