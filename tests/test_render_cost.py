"""tools/panel render-cost: the window's cost from the lifetime counters. No device."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import render_cost  # noqa: E402


class RenderCostTest(unittest.TestCase):
    def test_cost_is_the_difference_over_the_window(self):
        # 150 redraws in 5 s that took 1 s in all: 30 a second, 6.67 ms each, 20 % of one core
        self.assertEqual(render_cost.cost((1000, 5_000_000, 10.0), (1150, 6_000_000, 15.0)),
                         {"redraws_per_s": 30.0, "ms_each": 6.67, "core_percent": 20.0, "seconds": 5.0})

    def test_a_still_screen_is_zero_not_a_division_error(self):
        self.assertEqual(render_cost.cost((5, 9, 1.0), (5, 9, 6.0))["ms_each"], 0.0)


if __name__ == "__main__":
    unittest.main()
