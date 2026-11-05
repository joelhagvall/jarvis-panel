"""tools/panel tap-cadence: redraws a second per window from status polls. No device."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import tap_cadence  # noqa: E402


class TapCadenceTest(unittest.TestCase):
    def test_a_window_counts_its_redraws_over_its_polled_time(self):
        rows = [(0, 100), (50, 103), (100, 106), (250, 106), (300, 109)]
        self.assertEqual(tap_cadence.windows(rows), [(0, 60.0), (250, 15.0)])   # 3 redraws over 200 ms

    def test_one_poll_is_no_window(self):
        self.assertEqual(tap_cadence.windows([(0, 5)]), [])


if __name__ == "__main__":
    unittest.main()
