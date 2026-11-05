"""tools/cover_change.py never takes Spotify from another device unless asked. No panel, no Jarvis."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import cover_change  # noqa: E402


class CoverChangePlanTest(unittest.TestCase):
    def test_plan(self):
        self.assertEqual(cover_change.plan({'active': True, 'status': 'playing'}, False), 'none')
        self.assertEqual(cover_change.plan({'active': True, 'status': 'paused'}, False), 'play')
        # Joel's phone has Spotify (2026-10-30): nothing is touched without --take-over.
        self.assertIsNone(cover_change.plan({'active': False, 'status': 'paused'}, False))
        self.assertIsNone(cover_change.plan({}, False))
        self.assertEqual(cover_change.plan({'active': False, 'status': 'playing'}, True), 'play')


class SummarizeTest(unittest.TestCase):
    def test_flow_line_carries_pacing_and_psram(self):
        explained = {'next_timeline': {'new_record_s': 0.42}, 'stages': {'next': {
            'repeated': 4, 'record_ms': {'max': 33.1}, 'psram_min': 7403712}}}
        line = cover_change.summarize(explained)
        self.assertEqual((line['new_record_s'], line['repeated'], line['record_max_ms'], line['psram_min']),
                         (0.42, 4, 33.1, 7403712))
        self.assertIsNone(cover_change.summarize({})['repeated'])

    def test_same_cover_and_slowest_render(self):
        rows = [{'stage': 'next', 'status': {'responsiveness': {'slowest': {'render_us': 9000, 'rects': [[0, 0, 1, 1]]}}}},
                {'stage': 'next', 'status': {'responsiveness': {'slowest': {'render_us': 16256, 'rects': [[456, 132, 967, 193]]}}}},
                {'stage': 'start', 'status': {'responsiveness': {'slowest': {'render_us': 99000}}}}]
        line = cover_change.summarize({'next_timeline': {'new_record_s': 0.4}}, rows)
        self.assertEqual((line['cover_changed'], line['slowest_ms'], line['slowest_rects']), (True, 16.3, [[456, 132, 967, 193]]))
        # Skipping inside one album (Mylo Xyloto, 2026-10-31): no new picture, so no cover change was measured.
        self.assertIs(cover_change.summarize({'next_timeline': {'new_record_s': None}})['cover_changed'], False)
        self.assertEqual(cover_change.summarize({}, [{'stage': 'next', 'status': {'uptime_ms': 301872}}])['uptime_s'], 302)

if __name__ == '__main__':
    unittest.main()


class CoverChangeLogTest(unittest.TestCase):
    def test_delays_from_the_panels_own_log(self):
        lines = [
            "1000 1.0 [I][music:301]: next sent",
            "1004 1.0 [I][vinyl:1001]: Page: next cover turns before the skip is confirmed",
            "1180 1.1 [I][music:520]: now playing: Cocktails",
            "5000 5.0 [I][music:301]: previous sent",
            "5150 5.1 [I][music:520]: now playing: My Love",
            "5160 5.1 [I][vinyl:406][vinyl-build]: Page: new cover turns at once (built before)",
        ]
        self.assertEqual(cover_change.delays(lines, 'next'),
                         {'press_ms': 1000, 'now_ms': 180, 'turns_ms': 4,
                          'how': 'next cover turns before the skip is confirmed'})
        self.assertEqual(cover_change.delays(lines, 'previous', 1000)['turns_ms'], 160)
        self.assertEqual(cover_change.delays(lines, 'next', 1000), {})        # no later press

    def test_button_is_the_enabled_one_with_the_icon(self):
        ui = {'widgets': [{'path': 'a', 'type': 'button', 'x': 10, 'y': 20, 'width': 40, 'height': 40},
                          {'path': 'a/0', 'text': cover_change.ICONS['previous']},
                          {'path': 'b', 'type': 'button', 'x': 0, 'y': 0, 'width': 10, 'height': 10, 'disabled': True},
                          {'path': 'b/0', 'text': cover_change.ICONS['next']}]}
        self.assertEqual(cover_change.button(ui, 'previous'), (30, 40))
        self.assertIsNone(cover_change.button(ui, 'next'))
