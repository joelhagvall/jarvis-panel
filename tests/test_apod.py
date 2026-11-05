#!/usr/bin/env python3
"""NASA's picture of the day as the background (server/apod.py) without the network: a fake NASA gives
a picture day, then a video day, then a logo-sized picture. The real bake runs (needs numpy and Pillow).

  uv run --with-requirements server/requirements.txt python -m unittest tests/test_apod.py
"""
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
try:
    import numpy  # noqa: F401
    from PIL import Image
    import apod
except ImportError:
    apod = None


def jpeg(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (20, 60, 30)).save(buf, "JPEG")
    return buf.getvalue()


@unittest.skipUnless(apod, "needs numpy and Pillow")
class ApodTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out, self.cache = self.tmp / "www", self.tmp / "cache"
        self.out.mkdir()
        self.real_fetch, self.urls = apod.fetch, []
        self.real_state, apod.background.STATE = apod.background.STATE, self.tmp / "background.json"
        self.real_custom, apod.background.CUSTOM = apod.background.CUSTOM, self.tmp / "custom"
        self.addCleanup(setattr, sys, 'argv', sys.argv.copy())

    def tearDown(self):
        apod.fetch, apod.background.STATE = self.real_fetch, self.real_state
        apod.background.CUSTOM = self.real_custom

    def run_day(self, entry, picture):
        def fake(url, timeout=30):
            self.urls.append(url)
            return json.dumps(entry).encode() if "apod-basic" in url else picture
        apod.fetch = fake
        sys.argv = ["apod.py", "--out", str(self.out), "--cache", str(self.cache)]
        return apod.main()

    def test_picture_then_video_then_logo(self):
        day = {"date": "2026-10-22", "title": "The First <i>Green</i> Star", "media_type": "image",
               "credit": '<a href="https://markgarlick.com/">Mark A. Garlick</a>',
               "hdurl": "https://assets.science.nasa.gov/dynamicimage/x/green.jpg?w=3000&h=3000&fit=clip"}
        self.assertEqual(self.run_day(day, jpeg(1600, 1200)), 0)
        with Image.open(self.out / "bg.png") as im:
            self.assertEqual(im.size, (1024, 600))
        meta = json.loads((self.out / "apod.json").read_text())
        self.assertEqual((meta["title"], meta["credit"]), ("The First Green Star", "Mark A. Garlick"))
        self.assertIn("w=1600", self.urls[1])                      # asked NASA for a smaller rendition
        first = (self.out / "bg.png").read_bytes()
        with Image.open(self.out / "art.png") as im:                 # Tavla: the picture without glass
            self.assertEqual(im.size, (1024, 600))
        art = (self.out / "art.png").read_bytes()
        self.assertNotEqual(art, first)

        (self.out / "bg.png").write_bytes(b"van gogh")            # as after a deploy
        (self.out / "art.png").write_bytes(b"van gogh")
        video = {"date": "2026-10-23", "title": "A video", "media_type": "video", "url": "https://youtube.com/x"}
        self.assertEqual(self.run_day(video, b""), 0)
        self.assertEqual((self.out / "bg.png").read_bytes(), first)  # the last picture is back
        self.assertEqual((self.out / "art.png").read_bytes(), art)
        self.assertEqual(json.loads((self.out / "apod.json").read_text())["date"], "2026-10-22")

        # a painting chosen in Home Assistant stays, even on a new NASA day
        apod.background.STATE.write_text('{"choice": "Mandelblom"}')
        self.assertEqual(self.run_day({**day, "date": "2026-10-25"}, jpeg(1600, 1200)), 0)
        self.assertEqual((self.out / "art.png").read_bytes(),
                         apod.background.panel_image(apod.background.ASSETS / "art_almond.png").read_bytes())
        apod.background.STATE.write_text('{"choice": "NASA"}')

        logo = {**day, "date": "2026-10-24", "title": "NASA Science", "hdurl": "https://science.nasa.gov/logo.png"}
        self.assertEqual(self.run_day(logo, jpeg(121, 102)), 0)
        self.assertEqual((self.out / "bg.png").read_bytes(), first)  # a logo is not a background
        self.assertFalse((self.cache / "apod_2026-10-24.jpg").exists())

    def test_scheduled_poll_uses_new_york_day_and_stops_after_a_video(self):
        self.cache.mkdir()
        now = datetime.fromisoformat('2026-10-26T00:10:00-04:00')
        args = ['apod.py', '--scheduled', '--out', str(self.out), '--cache', str(self.cache)]
        self.out.joinpath('bg.png').write_bytes(b'keep the previous image')
        old = {'date': '2026-10-25', 'media_type': 'image', 'url': 'https://example.invalid/old.jpg'}
        video = {'date': '2026-10-26', 'media_type': 'video', 'url': 'https://example.invalid/video'}
        with patch.object(apod, 'new_york_now', return_value=now), patch.object(sys, 'argv', args):
            with patch.object(apod, 'fetch', return_value=json.dumps(old).encode()) as fetch:
                self.assertEqual(apod.main(), 0)
                self.assertIn('date=2026-10-26', fetch.call_args.args[0])
                self.assertEqual(fetch.call_count, 1)  # stale day must not download its image
            state = json.loads((self.cache / 'poll.json').read_text())
            self.assertEqual((state['attempts'], state['complete']), (1, False))
            with patch.object(apod, 'fetch', return_value=json.dumps(video).encode()) as fetch:
                self.assertEqual(apod.main(), 0)
                self.assertEqual(fetch.call_count, 1)
            with patch.object(apod, 'fetch') as fetch:
                self.assertEqual(apod.main(), 0)
                fetch.assert_not_called()
        self.assertEqual((self.out / 'bg.png').read_bytes(), b'keep the previous image')

    def test_scheduled_picture_publishes_then_later_ticks_do_not_refetch(self):
        self.cache.mkdir()
        day = '2026-10-26'
        (self.cache / f'apod_{day}.jpg').write_bytes(jpeg(1024, 600))
        Image.new('RGB', (1024, 600), (12, 24, 36)).save(self.cache / f'bg_{day}.png')
        Image.new('RGB', (1024, 600), (24, 48, 72)).save(self.cache / f'art_{day}.png')
        entry = {'date': day, 'media_type': 'image', 'title': 'New day', 'url': 'https://example.invalid/image.jpg'}
        now = datetime.fromisoformat(day + 'T00:25:00-04:00')
        args = ['apod.py', '--scheduled', '--out', str(self.out), '--cache', str(self.cache)]
        with patch.object(apod, 'new_york_now', return_value=now), patch.object(sys, 'argv', args), \
                patch.object(apod, 'fetch', return_value=json.dumps(entry).encode()) as fetch:
            self.assertEqual(apod.main(), 0)
            self.assertEqual(apod.main(), 0)
            self.assertEqual(fetch.call_count, 1)
        self.assertEqual(json.loads((self.out / 'apod.json').read_text())['date'], day)
        for name in ('bg.qoi', 'art.qoi', 'voice_bg.qoi'):
            self.assertTrue((self.out / name).is_file())
        state = json.loads((self.cache / 'poll.json').read_text())
        self.assertEqual((state['complete'], state['result']), (True, 'image'))

    def test_poll_budget_survives_failures_and_resets_only_on_new_york_day(self):
        self.cache.mkdir()
        before = datetime.fromisoformat('2026-10-26T00:09:00-04:00')
        self.assertIsNone(apod.begin_poll(self.cache, before))
        now = datetime.fromisoformat('2026-10-26T00:10:00-04:00')
        for attempt in range(1, apod.MAX_DAILY_POLLS + 1):
            self.assertEqual(apod.begin_poll(self.cache, now)['attempts'], attempt)
        self.assertIsNone(apod.begin_poll(self.cache, now))
        next_day = datetime.fromisoformat('2026-10-27T00:10:00-04:00')
        self.assertEqual(apod.begin_poll(self.cache, next_day)['attempts'], 1)

    def test_scheduled_start_handles_different_dst_transition_weeks(self):
        from zoneinfo import ZoneInfo
        cases = [('2026-10-26T05:10:00+01:00', '00:10', '2026-10-26'),
                 ('2026-11-02T06:10:00+01:00', '00:10', '2026-11-02')]
        for source, expected_time, day in cases:
            now = datetime.fromisoformat(source)
            self.assertEqual(now.astimezone(ZoneInfo('America/New_York')).strftime('%H:%M'), expected_time)
            self.cache.mkdir(exist_ok=True)
            self.assertEqual(apod.begin_poll(self.cache, now)['date'], day)

    def test_explicit_date_selects_the_requested_entry_from_a_list(self):
        entries = [{'date': '2026-10-26'}, {'date': '2026-10-25'}]
        with patch.object(apod, 'fetch', return_value=json.dumps(entries).encode()):
            self.assertEqual(apod.todays_entry('2026-10-25'), entries[1])


if __name__ == "__main__":
    unittest.main()
