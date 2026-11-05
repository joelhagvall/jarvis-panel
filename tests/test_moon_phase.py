"""Jarvis's moon tool reads Home Assistant's moon sensor and answers in Swedish time. No network."""
from datetime import datetime
import json
import sys
from pathlib import Path
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "agents"))
import moon_phase  # noqa: E402


class MoonToolTest(unittest.TestCase):
    STATE = {"state": "Avtagande måne", "attributes": {"illumination": 88, "age_days": 18.1,
             "next_full": "2026-11-24T14:53+00:00", "next_new": "2026-11-09T07:02+00:00"}}

    def test_the_answer_is_in_swedish_time_with_days_left(self):
        now = datetime(2026, 10, 28, 20, 0, tzinfo=ZoneInfo("Europe/Stockholm"))
        out = moon_phase.summary(self.STATE, now)
        self.assertEqual(out["phase"], "Avtagande måne")
        self.assertEqual(out["next_full"], {"time": "2026-11-24 15:53", "weekday": "tisdag", "days_left": 26.8})
        self.assertEqual(out["next_new"]["time"], "2026-11-09 08:02")

    def test_a_missing_sensor_is_a_short_error(self):
        with mock.patch.object(moon_phase.hass, "get_json", side_effect=OSError("refused")):
            error, text = moon_phase.moon()
        self.assertTrue(error and text.startswith("Månen gick inte"))
        with mock.patch.object(moon_phase.hass, "get_json", return_value=self.STATE):
            error, text = moon_phase.moon()
        self.assertFalse(error); self.assertEqual(json.loads(text)["illumination_percent"], 88)


if __name__ == "__main__":
    unittest.main()
