"""tools/panel visual-diff: changed pixels in the bar band and elsewhere. No device."""
import sys
import tempfile
from pathlib import Path
import unittest

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import visual_ref_diff  # noqa: E402


class VisualRefDiffTest(unittest.TestCase):
    def test_bar_band_and_elsewhere_are_counted_apart(self):
        with tempfile.TemporaryDirectory() as tmp:
            a, b = Path(tmp) / "a.png", Path(tmp) / "b.png"
            Image.new("RGB", (1024, 600), (10, 10, 10)).save(a)
            im = Image.new("RGB", (1024, 600), (10, 10, 10))
            for x in range(100, 110):
                im.putpixel((x, 520), (200, 200, 200))   # in the bar band
                im.putpixel((x, 50), (200, 200, 200))    # elsewhere
            im.save(b)
            self.assertEqual(visual_ref_diff.diff(a, b), {"band": 10, "elsewhere": 10, "elsewhere_box": [100, 50, 110, 51]})


if __name__ == "__main__":
    unittest.main()
