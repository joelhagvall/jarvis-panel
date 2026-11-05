"""The moon (ha/custom_components/jarvis_agent/moon.py) against published times. No Home Assistant."""
from datetime import datetime, timedelta, timezone
import importlib.util
from pathlib import Path
import unittest

spec = importlib.util.spec_from_file_location("moon", Path(__file__).resolve().parents[1] / "ha/custom_components/jarvis_agent/moon.py")
moon = importlib.util.module_from_spec(spec)
spec.loader.exec_module(moon)
UTC = timezone.utc


class MoonTest(unittest.TestCase):
    def test_full_and_new_moons_within_two_minutes_of_the_published_times(self):
        for after, want, fn in ((datetime(2024, 4, 20, tzinfo=UTC), datetime(2024, 4, 23, 23, 49, tzinfo=UTC), moon.next_full),
                                (datetime(2024, 4, 1, tzinfo=UTC), datetime(2024, 4, 8, 18, 21, tzinfo=UTC), moon.next_new),
                                (datetime(2025, 3, 10, tzinfo=UTC), datetime(2025, 3, 14, 6, 55, tzinfo=UTC), moon.next_full)):
            self.assertLess(abs(fn(after) - want), timedelta(minutes=2), (after, fn(after)))

    def test_the_phase_and_light_follow_the_month(self):
        full = datetime(2024, 4, 23, 23, 49, tzinfo=UTC)
        at_full = moon.now(full)
        self.assertEqual((at_full["phase"], at_full["illumination"]), ("Fullmåne", 100))
        self.assertEqual(moon.now(datetime(2024, 4, 8, 18, 30, tzinfo=UTC))["phase"], "Nymåne")
        self.assertEqual(moon.now(datetime(2024, 4, 13, tzinfo=UTC))["phase"], "Tilltagande månskära")
        self.assertGreater(moon.now(full - timedelta(days=1))["next_full"], full - timedelta(days=1))
        self.assertEqual(moon.next_full(full + timedelta(minutes=1)).month, 5)     # past it: the next one


if __name__ == "__main__":
    unittest.main()
