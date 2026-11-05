"""tools/check.py prints ESPHome's reason for a failed config, not only the path of the kept log. No ESPHome run."""
import sys
from pathlib import Path
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import check  # noqa: E402


class ConfigErrorsTest(unittest.TestCase):
    def test_the_reason_after_thousands_of_echoed_glyphs(self):
        log = ("INFO Reading configuration\nFailed config\n\nfont: [source voice-board.yaml:4993]\n"
               + "      - x\n" * 3000 + "    ignore_missing_glyphs: False\n  \n"
               "  Found duplicate glyph: \U000F0141 (b'\\\\U000f0141').\n      https://cdn.example/font.ttf\n")
        self.assertEqual(check.config_errors(log), ["Found duplicate glyph: \U000F0141 (b'\\\\U000f0141')."])

    def test_lines_that_could_carry_private_values_are_left_out(self):
        log = "Failed config\nwifi:\n  Invalid ssid 'home-net'\n  Invalid password\nfont:\n  Unknown font 'x'\n"
        self.assertEqual(check.config_errors(log), ["Unknown font 'x'"])


if __name__ == "__main__":
    unittest.main()
