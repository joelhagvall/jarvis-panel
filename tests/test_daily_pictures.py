#!/usr/bin/env python3
"""A new background every day from free APIs (server/daily_pictures.py, Joel 2026-11-02): each source becomes a
choice once it has a picture, keeps only its latest, asks again only for a new day, keeps the last picture on a
portrait day, a duplicate or a failure, publishes at once when it is the chosen background, and its title,
credit and text reach Tavla's plaque. Växlande turns through the sources by day without repeating a picture.
No network: fetch is replaced by fake API answers.
Run: python3 -m unittest tests/test_daily_pictures.py"""
import io
import json
import shutil
import sys
import tempfile
import unittest
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
sys.path.insert(0, str(ROOT / "server"))
import background  # noqa: E402
import daily_pictures  # noqa: E402

NOW = datetime(2026, 11, 2, 12, 0, tzinfo=timezone.utc)


def jpeg(w, h, seed=0):
    """A picture with a pattern of its own per seed, so the duplicate check tells them apart."""
    from PIL import Image
    import random
    rng = random.Random(seed)
    small = Image.new("RGB", (16, 9))
    small.putdata([tuple(rng.randrange(256) for _ in range(3)) for _ in range(16 * 9)])
    buf = io.BytesIO()
    small.resize((w, h), Image.NEAREST).save(buf, "JPEG", quality=95)
    return buf.getvalue()


EARTH_ITEM = """<item><title>Pennsylvania&#8217;s Grand Canyon</title>
<link>https://science.nasa.gov/earth/earth-observatory/{slug}/</link><pubDate>{date}</pubDate>
<description><![CDATA[<p>Pine Creek Gorge forms a deep scar.</p> <p>The post <a href="x">it</a> appeared first on NASA Science.</p>]]></description>
<content:encoded><![CDATA[<figure><img src="https://assets.science.nasa.gov/dynamicimage/assets/eo/{slug}/tall.jpg?w=900&amp;h=2000" />
<img src="https://assets.science.nasa.gov/dynamicimage/assets/eo/{slug}/wide.jpg?w=2048&amp;h=1152" /></figure>]]></content:encoded></item>"""


class FakeApis:
    """Answers like the three APIs; `calls` records every URL asked for."""

    def __init__(self):
        self.calls = []
        self.paintings = [{"id": i, "title": f"Painting {i}", "creation_date": "1900", "url": f"https://cma/{i}",
                           "creators": [{"description": "Félix Vallotton (Swiss French, 1865–1925)"}],
                           "wall_description": "<p>Fields &amp; sky.</p>",
                           "images": {"print": {"url": f"https://cdn/{i}.jpg", "width": 3400, "height": 2000}}}
                          for i in range(1, 6)]
        self.photo = ("File:Snow_cemetery.jpg", 7000, 4000)
        self.bing_day = "20261102"
        self.earth = [("pennsylvania", "Mon, 02 Nov 2026 05:01:00 +0000")]
        self.broken = set()
        self.seeds = {}                     # url -> pattern seed; a new url gets a new pattern

    def picture(self, url, w, h):
        return jpeg(w, h, self.seeds.setdefault(url, len(self.seeds) + 1))

    def __call__(self, url, data=None, timeout=30, limit=0):
        self.calls.append(url)
        host = urllib.parse.urlsplit(url).hostname
        if host in self.broken:
            raise OSError("down")
        query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
        if url.startswith(daily_pictures.CLEVELAND):
            if query.get("fields") == "id":
                return json.dumps({"info": {"total": len(self.paintings)}, "data": []}).encode()
            return json.dumps({"info": {}, "data": self.paintings}).encode()
        if "api.wikimedia.org" in url:
            return json.dumps({"image": {"title": self.photo[0], "artist": {"text": "<a>Wilfredor</a>"},
                                         "license": {"type": "CC0"}, "description": {"text": "A quiet cemetery."},
                                         "file_page": "https://commons/x"}}).encode()
        if url.startswith(daily_pictures.COMMONS):
            title, w, h = self.photo
            return json.dumps({"query": {"pages": {"1": {"imageinfo": [{
                "mime": "image/jpeg", "url": "https://upload/x.jpg", "thumburl": f"https://thumb/{title}",
                "width": w, "height": h}]}}}}).encode()
        if "bing.com/HPImageArchive" in url:
            return json.dumps({"images": [{"startdate": self.bing_day, "url": f"/th?id=bear{self.bing_day}.jpg",
                                           "hsh": self.bing_day, "title": "Info",
                                           "copyright": "Polar bears by Hudson Bay (© Danita Delimont/Adobe Stock)"}]}).encode()
        if url.startswith(daily_pictures.EARTH_FEED):
            photojournal = "<item><title>Mars</title><link>https://science.nasa.gov/photojournal/mars/</link></item>"
            items = "".join(EARTH_ITEM.format(slug=slug, date=date) for slug, date in self.earth)
            return f"<rss><channel>{photojournal}{items}</channel></rss>".encode()
        if url.startswith("https://thumb/"):
            _, w, h = self.photo
            return self.picture(url, min(w, 2400), round(min(w, 2400) * h / w))
        if "/tall.jpg" in url:
            return self.picture(url, 900, 2000)
        return self.picture(url, 2400, 1400)


class DailyPicturesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hallpanel-daily-"))
        self.saved = (background.STATE, background.DAILY_DIR, background.UPLOADS, background.CUSTOM,
                      daily_pictures.fetch, background.publish, background.baked, background.APOD_CACHE)
        background.STATE, background.DAILY_DIR = self.tmp / "background.json", self.tmp / "daily"
        background.APOD_CACHE = self.tmp / "apod"
        background.UPLOADS, background.CUSTOM = self.tmp / "uploads", self.tmp / "custom"
        self.api = daily_pictures.fetch = FakeApis()
        self.published = []
        background.publish = lambda *a, **kw: self.published.append(background.current())
        background.baked = lambda picture: (Path(picture), Path(picture))     # the glass bake is test_background's
        self.addCleanup(self.restore)

    def restore(self):
        (background.STATE, background.DAILY_DIR, background.UPLOADS, background.CUSTOM,
         daily_pictures.fetch, background.publish, background.baked, background.APOD_CACHE) = self.saved
        shutil.rmtree(self.tmp)

    def run_once(self, now=NOW, *args):
        out = io.StringIO()
        old, sys.stdout = sys.stdout, out
        try:
            code = daily_pictures.main(["--dir", str(background.DAILY_DIR), *args], now=now)
        finally:
            sys.stdout = old
        return code, out.getvalue()

    def meta(self, slug):
        return json.loads((background.DAILY_DIR / slug / "meta.json").read_text())

    def test_each_source_becomes_a_choice_with_its_plaque(self):
        self.assertEqual(background.choices(), ["NASA", "Stjärnenatten", "Mandelblom"])
        code, log = self.run_once()
        self.assertEqual(code, 0, log)
        self.assertEqual(background.choices(), ["NASA", "Växlande", "Stjärnenatten", "Mandelblom", "Dagens målning",
                                                "Dagens foto", "Bing", "NASA Jordbild"])     # 8: all on the panel
        self.assertEqual(self.published, [])                    # NASA is the choice: nothing republished
        background._save(choice="Dagens målning")
        title, credit = background.art()
        self.assertTrue(title.startswith("Painting "))
        self.assertEqual(credit, "Félix Vallotton, 1900 · Cleveland Museum of Art")
        self.assertEqual(background.art_text(), "Fields & sky.")
        self.assertEqual(background.sources()[0].parent, background.DAILY_DIR / "painting")
        background._save(choice="Bing")
        self.assertEqual(background.art(), ("Polar bears by Hudson Bay", "© Danita Delimont/Adobe Stock · Bing"))
        background._save(choice="Dagens foto")
        self.assertEqual(background.art(), ("Snow cemetery", "Wilfredor · CC0 · Wikimedia Commons"))
        background._save(choice="NASA Jordbild")
        self.assertEqual(background.art(), ("Pennsylvania’s Grand Canyon", "NASA Earth Observatory"))
        self.assertEqual(background.art_text(), "Pine Creek Gorge forms a deep scar.")
        self.assertIn("/wide.jpg", self.api.calls[-1])          # the post's tall picture was skipped
        from PIL import Image
        with Image.open(background.DAILY_DIR / "photo" / self.meta("photo")["file"]) as im:
            self.assertEqual(im.width, daily_pictures.KEEP_WIDTH)        # kept small, not the 7000 px original

    def test_today_in_means_no_network_and_a_new_day_replaces_the_picture(self):
        self.run_once()
        self.api.calls.clear()
        code, log = self.run_once()
        self.assertEqual((code, self.api.calls), (0, []), log)
        background._save(choice="Dagens målning")
        first = self.meta("painting")["id"]
        self.api.bing_day = "20261103"
        self.api.earth.insert(0, ("vega-island", "Tue, 03 Nov 2026 04:01:00 +0000"))
        self.run_once(datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc))
        painting = self.meta("painting")
        self.assertNotEqual(painting["id"], first)
        self.assertEqual(painting["recent"][:2], [painting["id"], first])
        self.assertEqual([f.name for f in (background.DAILY_DIR / "painting").glob("*.jpg")], ["2026-11-03.jpg"])
        self.assertEqual(self.published, ["Dagens målning"])            # the chosen source publishes at once

    def test_recent_paintings_are_not_repeated(self):
        seen = set()
        for day in range(1, 6):
            self.run_once(datetime(2026, 11, day, 12, 0, tzinfo=timezone.utc), "--only", "painting")
            seen.add(self.meta("painting")["id"])
        self.assertEqual(len(seen), 5)

    def test_portrait_day_and_failure_keep_the_last_picture(self):
        self.run_once()
        kept = self.meta("photo")
        self.api.photo = ("File:Tall_window.jpg", 3000, 5000)
        code, log = self.run_once(datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc), "--only", "photo")
        self.assertIn("no landscape picture", log)
        self.assertEqual((self.meta("photo")["id"], self.meta("photo")["checked"]), (kept["id"], "2026-11-03"))
        self.api.broken.add("www.bing.com")
        code, log = self.run_once(datetime(2026, 11, 3, 12, 0, tzinfo=timezone.utc), "--only", "bing")
        self.assertEqual(code, 1)
        self.assertIn("failed, keeping the last picture", log)
        self.assertIn("Bing", background.choices())

    def test_bing_not_new_yet_asks_again(self):
        self.run_once(NOW, "--only", "bing")
        self.api.calls.clear()
        later = datetime(2026, 11, 3, 6, 0, tzinfo=timezone.utc)       # 07:00 Stockholm, Bing still on the 2nd
        code, log = self.run_once(later, "--only", "bing")
        self.assertIn("nothing new yet", log)
        self.api.bing_day = "20261103"
        code, log = self.run_once(later, "--only", "bing")
        self.assertEqual(self.meta("bing")["day"], "2026-11-03", log)

    def test_upload_named_like_a_daily_source_is_not_a_second_choice(self):
        background.UPLOADS.mkdir()
        (background.UPLOADS / "Bing.jpg").write_bytes(jpeg(1600, 900))
        self.run_once(NOW, "--only", "bing")
        self.assertEqual(background.choices().count("Bing"), 1)

    def test_the_same_picture_from_another_source_is_not_kept_again(self):
        self.run_once(NOW, "--only", "bing")
        bing_url = self.api.calls[-1]
        self.api.seeds["https://thumb/File:Snow_cemetery.jpg"] = self.api.seeds[bing_url]   # Wikimedia picks Bing's
        code, log = self.run_once(NOW, "--only", "photo")
        self.assertIn("looks like bing:", log)
        self.assertNotIn("Dagens foto", background.choices())             # nothing kept, so not a choice yet
        self.assertEqual(self.meta("photo")["checked"], "2026-11-02")       # and not asked again today

    def test_rotation_turns_by_day_without_repeating_and_publishes_at_once(self):
        self.run_once()
        background._save(choice="Växlande")
        picks = [background.shown(f"2026-11-{d:02d}") for d in range(2, 6)]
        self.assertEqual(sorted(picks), sorted(background.DAILY))            # four days, four sources
        self.assertEqual(background.shown("2026-11-05"), picks[-1])         # kept for the day
        self.assertEqual(background.shown("2026-11-06"), picks[0])          # all seen: the turn starts over
        self.published.clear()
        self.run_once(datetime(2026, 11, 7, 0, 0, tzinfo=timezone.utc))     # the timer on a new day
        self.assertEqual(self.published, ["Växlande"])
        state = json.loads(background.STATE.read_text())
        self.assertEqual(state["rotation_day"], "2026-11-07")
        pick = state["rotation_pick"]
        self.assertEqual(background.shown("2026-11-07"), pick)
        self.assertEqual(background.sources(pick)[0].parent.name, background.DAILY[pick])


if __name__ == "__main__":
    unittest.main()
