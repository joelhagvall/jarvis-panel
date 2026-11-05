"""The music-cover gate finds the row's cover from the real widget geometry, read-only."""
import sys
from pathlib import Path
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from music_cover import music_row, check


def ui(title_x, art_hidden=False, row=True):
    widgets = [{'path': 'screen/8', 'type': 'object', 'x': 32, 'y': 204, 'width': 472, 'height': 52},
               {'path': 'screen/8/0', 'type': 'label', 'x': title_x, 'y': 212, 'text': 'FASTEST'},
               {'path': 'screen/8/2', 'type': 'button', 'x': 401, 'y': 209},
               {'path': 'screen/8/4', 'type': 'object', 'x': 39, 'y': 211, 'width': 40, 'height': 40,
                'hidden': art_hidden}]
    return {'widgets': widgets if row else widgets[:1]}


class Client:
    def __init__(self, uis): self.uis = list(uis)
    def ui(self): return self.uis.pop(0) if len(self.uis) > 1 else self.uis[0]
    def status(self): return {'artwork': {'requests': 2, 'errors': 0}}


class MusicCoverTest(unittest.TestCase):
    def test_title_under_the_cover_is_the_2026_10_29_bug(self):
        self.assertEqual(music_row(ui(91)), ('FASTEST', True))
        self.assertEqual(music_row(ui(47)), ('FASTEST', False))   # text at 14 while the cover shows
        self.assertIsNone(music_row(ui(47, row=False)))

    def test_waits_for_a_late_cover_then_fails_without_one(self):
        now = [0.0]
        tick = dict(clock=lambda: now[0], sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertTrue(check(Client([ui(47), ui(47), ui(91)]), 15, **tick)['ok'])
        now[0] = 0
        result = check(Client([ui(47)]), 2, **tick)
        self.assertFalse(result['ok'])
        self.assertIn('without its cover', result['failure'])
        self.assertTrue(check(Client([{'widgets': []}]), 2, **tick)['ok'])   # no music: nothing to check


if __name__ == '__main__':
    unittest.main()
