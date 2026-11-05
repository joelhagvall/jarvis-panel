"""README pictures: every one exists, is a full 1024x600 screen, and no picture-table cell is empty
(a cropped sheet shows smaller in GitHub's table; docs/public-export.md)."""
from pathlib import Path
import re
import unittest

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
README = (ROOT / "README.md").read_text()


class ReadmePicturesTest(unittest.TestCase):
    def test_every_picture_exists_and_is_a_full_screen(self):
        paths = re.findall(r"!\[[^\]]*\]\((docs/readme/[^)]+)\)", README)
        self.assertGreaterEqual(len(paths), 8)
        for path in paths:
            with self.subTest(path=path):
                self.assertTrue((ROOT / path).is_file(), path)
                with Image.open(ROOT / path) as image:
                    self.assertEqual(image.size, (1024, 600), path)

    def test_picture_table_rows_are_full(self):
        rows = [line for line in README.splitlines() if line.startswith("|") and "docs/readme/" in line]
        self.assertTrue(rows)
        for row in rows:
            cells = [c.strip() for c in row.strip().strip("|").split("|")]
            self.assertTrue(all(cells), row[:80])


if __name__ == "__main__":
    unittest.main()
