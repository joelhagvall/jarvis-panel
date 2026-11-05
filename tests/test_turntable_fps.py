"""tools/panel turntable-fps: the record's limits. No device."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import turntable_fps  # noqa: E402


class TurntableFpsTest(unittest.TestCase):
    def test_60_a_second_passes_and_the_plinth_regression_fails(self):
        self.assertEqual(turntable_fps.verdict({"redraws_per_s": 58.6, "ms_each": 7.5}), [])
        failures = turntable_fps.verdict({"redraws_per_s": 34.3, "ms_each": 14.9})   # one plinth picture under it
        self.assertEqual(len(failures), 2)


class TurntableModeTest(unittest.TestCase):
    def test_the_mode_is_allowed_everywhere_a_test_mode_is_named(self):
        root = Path(__file__).resolve().parents[1]
        self.assertIn('|turntable|', (root / "components/panel_debug/panel_debug.cpp").read_text())
        self.assertIn("'turntable'", (root / "tools/panel_remote.py").read_text())
        codegen = (root / "tools/panel_test_codegen.py").read_text()
        self.assertIn('mode == "turntable"', codegen)
        self.assertIn("panel_vinyl::page.test_spin(false);", codegen)          # restore stops it


if __name__ == "__main__":
    unittest.main()
