"""tools/page_draw_summary.py groups page_draw log lines by page and area budget, duplicates counted once."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import page_draw_summary as summary  # noqa: E402

LINES = [
    "66560 1793357518.348 [I][page_draw:170]: to page_glass_v2 58.6 ms: [previous refresh 59.0 ms before, 9408 px; "
    "decision 4.3 frame wait 0.0 layout+sync 0.3 walk 0.1] widgets 4.8 ms/0/0kpx fill 8.2 ms/19/68kpx label 20.9 "
    "ms/85/274kpx image 22.0 ms/33/14928kpx",
    "66561 1793357518.349 [I][page_draw:172]:   swap budget 6 -> 6 areas; decision: layout 0.1 collect 0.6 reduce 1.3 ms",
    "67560 1793357519.348 [I][page_draw:170]: to page_glass_v2 40.0 ms: [previous refresh 57.3 ms before, 9408 px; "
    "decision 2.0 frame wait 0.0 layout+sync 0.3 walk 0.1] widgets 2.5 ms/0/0kpx fill 7.0 ms/19/68kpx label 13.1 "
    "ms/85/274kpx image 14.0 ms/33/14928kpx",
    "67561 1793357519.349 [I][page_draw:172]:   swap budget 6 -> 6 areas; decision: layout 0.1 collect 0.6 reduce 1.1 ms",
    "69684 1793356972.856 [I][page_draw:170]: to page_orb 31.8 ms: [previous refresh 1.6 ms before, 5376 px; "
    "decision 0.0 frame wait 12.0 layout+sync 0.7 walk 0.2] label 0.8 ms/1/22kpx image 19.0 ms/2/662kpx",
]


class PageDrawSummaryTest(unittest.TestCase):
    def test_medians_per_page_and_budget(self):
        found = summary.rows(LINES + LINES[:2])   # logs fetched twice
        self.assertEqual(len(found), 3)
        result = {(r['page'], r['budget']): r for r in summary.summary(found)}
        dash = result[('page_glass_v2', 6)]
        self.assertEqual((dash['n'], dash['total'], dash['label'], dash['reduce']), (2, 49.3, 17.0, 1.2))
        orb = result[('page_orb', None)]
        self.assertEqual((orb['n'], orb['frame wait'], orb['fill']), (1, 12.0, 0.0))
        self.assertEqual([r['page'] for r in summary.summary(found, 'page_orb')], ['page_orb'])


if __name__ == '__main__':
    unittest.main()
