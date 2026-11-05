"""server/show_satellite.py: Home Assistant's history as one time line in Swedish time. No network."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import show_satellite  # noqa: E402


class ShowSatelliteTest(unittest.TestCase):
    def test_series_are_merged_in_time_order_and_minimal_rows_keep_their_entity(self):
        history = [[{"entity_id": "assist_satellite.p", "state": "processing", "last_changed": "2026-10-28T19:05:09.345+00:00"},
                    {"state": "idle", "last_changed": "2026-10-28T19:05:14.025+00:00"}],
                   [{"entity_id": "media_player.jbl_spotify", "state": "playing", "last_changed": "2026-10-28T19:05:11.5+00:00"}]]
        self.assertEqual(show_satellite.lines(history), [
            "20:05:09.345  assist_satellite.p  processing",
            "20:05:11.500  media_player.jbl_spotify  playing",
            "20:05:14.025  assist_satellite.p  idle"])

    def test_no_history_is_no_lines(self):
        self.assertEqual(show_satellite.lines([]), [])


if __name__ == "__main__":
    unittest.main()
