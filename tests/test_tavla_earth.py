#!/usr/bin/env python3
"""Tavla's NASA extras without the network (server/earth.py, server/apod.py's explanation, background.earth,
styles/glass-v2/tavla_earth.py): a fake EPIC and NeoWs, the panel's fields and the generated page."""
import io
import json
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "server" / "agents"))
sys.path.insert(0, str(ROOT / "styles" / "glass-v2"))
import apod  # noqa: E402
import background  # noqa: E402
import earth  # noqa: E402
from PIL import Image  # noqa: E402

NOW = datetime(2026, 10, 29, 12, tzinfo=timezone.utc)


def entry(image, date, lon, km=1.5e6):
    return {"image": image, "date": date, "centroid_coordinates": {"lat": -3.0, "lon": lon},
            "dscovr_j2000_position": {"x": km, "y": 0, "z": 0}}


def neo(name, lunar, low, high):
    return {"name": f"({name})", "estimated_diameter": {"meters": {"estimated_diameter_min": low,
                                                                   "estimated_diameter_max": high}},
            "close_approach_data": [{"orbiting_body": "Earth", "miss_distance": {"lunar": str(lunar)}}]}


def disc_jpeg():
    im = Image.new("RGB", (1080, 1080))
    from PIL import ImageDraw
    ImageDraw.Draw(im).ellipse((124, 125, 959, 959), fill=(40, 90, 160))
    buf = io.BytesIO()
    im.save(buf, "JPEG")
    return buf.getvalue()


class EarthServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.out, self.cache = self.tmp / "www", self.tmp / "cache"
        self.out.mkdir()
        self.calls = []
        self.epic = [entry("epic_1b_a", "2026-10-26 00:50:27", 154.2), entry("epic_1b_b", "2026-10-26 09:50:38", 19.0),
                     entry("epic_1b_c", "2026-10-26 11:38:41", -7.9)]
        self.feed = {"near_earth_objects": {"2026-10-29": [
            neo("2020 CB", 111.3, 8, 16), neo("2016 XB2", 67.8, 150, 270), neo("2017 UL44", 4.25, 50, 100),
            neo("1999 BIG", 300, 1500, 2500)]}}
        self.fail = set()

    def fake(self, url, timeout=30):
        self.calls.append(url)
        kind = "epic" if url == earth.EPIC else "neo" if "neo/rest" in url else "jpg"
        if kind in self.fail:
            raise OSError(f"{kind} is down")
        return {"epic": lambda: json.dumps(self.epic).encode(), "neo": lambda: json.dumps(self.feed).encode(),
                "jpg": disc_jpeg}[kind]()

    def run_earth(self, now=NOW):
        with patch.object(earth, "fetch", self.fake):
            return earth.main(["--out", str(self.out), "--cache", str(self.cache)], now=now)

    def test_the_europe_picture_and_the_three_closest_asteroids(self):
        self.assertEqual(self.run_earth(), 0)
        self.assertIn("/2026/10/26/jpg/epic_1b_b.jpg", self.calls[1])       # 19° E, nearest Sweden's 15°
        with Image.open(self.out / "earth.png") as im:
            self.assertEqual(im.size, (earth.EARTH_SIDE, earth.EARTH_SIDE))
        self.assertTrue((self.out / "earth.qoi").is_file())
        meta = json.loads((self.out / "earth.json").read_text())
        self.assertEqual(meta["title"], "Jorden 26 oktober")
        self.assertEqual(meta["line"], "NASA EPIC · kl 10:50 · 1,5 miljoner km bort")   # Stockholm time
        self.assertEqual(meta["neo_head"], "4 asteroider passerar i dag")
        self.assertEqual(meta["neo"], ["2017 UL44 · 4,2 månavstånd · ~75 m", "2016 XB2 · 68 månavstånd · ~210 m",
                                       "2020 CB · 111 månavstånd · ~12 m"])
        self.assertTrue(meta["version"])

    def test_neows_is_asked_once_a_day_and_a_failure_keeps_the_last(self):
        self.run_earth()
        first = json.loads((self.out / "earth.json").read_text())
        self.calls.clear()
        self.assertEqual(self.run_earth(), 0)                 # same day, same picture: only EPIC's list
        self.assertEqual(self.calls, [earth.EPIC])
        self.assertEqual(json.loads((self.out / "earth.json").read_text()), first)
        self.fail = {"epic", "neo"}
        self.assertEqual(self.run_earth(datetime(2026, 10, 30, 12, tzinfo=timezone.utc)), 1)
        kept = json.loads((self.out / "earth.json").read_text())
        self.assertEqual((kept["version"], kept["neo"]), (first["version"], first["neo"]))
        for _ in range(earth.MAX_NEO_TRIES + 2):              # a bounded number of NeoWs tries per day
            self.run_earth(datetime(2026, 10, 30, 13, tzinfo=timezone.utc))
        state = json.loads((self.cache / "state.json").read_text())
        self.assertEqual(state["neo_tries"], {"day": "2026-10-30", "n": earth.MAX_NEO_TRIES})

    def test_sizes_and_one_asteroid(self):
        self.assertEqual(earth.size_text(neo("x", 1, 900, 1500)), "~1,2 km")
        self.assertEqual(earth.size_text(neo("x", 1, 21, 47)), "~34 m")
        head, rows = earth.asteroids({"near_earth_objects": {"2026-10-29": [neo("2026 AB", 0.5, 4, 6)]}}, "2026-10-29")
        self.assertEqual((head, rows), ("1 asteroid passerar i dag", ["2026 AB · 0,5 månavstånd · ~5 m"]))
        self.assertEqual(earth.asteroids({}, "2026-10-29"), ("", []))
        # the longitude wraps round 180°: 170° W is 175° from Sweden, 150° E only 135°
        self.assertEqual(earth.facing_europe([entry("w", "", -170), entry("e", "", 150)])["image"], "e")


class PanelFieldsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.tmp)
        self.real_state, background.STATE = background.STATE, self.tmp / "background.json"
        self.addCleanup(setattr, background, "STATE", self.real_state)

    def test_the_explanation_only_for_nasa_and_the_earth_fields(self):
        (self.tmp / "apod.json").write_text(json.dumps({"title": "Venus", "explanation": "That's no moon. ¿Qué?"}))
        background.STATE.write_text('{"choice": "NASA"}')
        self.assertEqual(background.art_text(self.tmp), "That's no moon. ¿Qué?")
        background.STATE.write_text('{"choice": "Mandelblom"}')
        self.assertEqual(background.art_text(self.tmp), "")      # a painting has no NASA text
        self.assertEqual(background.earth(self.tmp)["earth_version"], "")   # no earth.json: the button stays hidden
        (self.tmp / "earth.json").write_text(json.dumps({"title": "Jorden 26 oktober", "line": "NASA EPIC",
                                                         "version": "abc", "neo_head": "1 asteroid passerar i dag",
                                                         "neo": ["2026 AB · 0,5 månavstånd · ~5 m"]}))
        f = background.earth(self.tmp)
        self.assertEqual((f["earth_version"], f["neo0"], f["neo1"], f["neo2"]),
                         ("abc", "2026 AB · 0,5 månavstånd · ~5 m", "", ""))

    def test_apod_explanation_is_plain_and_bounded(self):
        self.assertEqual(apod.explanation("<strong>Explanation:</strong> <a href='x'>That's no moon.</a> Hi."),
                         "That's no moon. Hi.")
        self.assertEqual(apod.explanation("Explanation: Venus has phases. Tomorrow's picture: What's next?"),
                         "Venus has phases.")
        long = apod.explanation("Explanation: " + "A sentence ends here. " * 100)
        self.assertLessEqual(len(long), apod.EXPLANATION_MAX)
        self.assertTrue(long.endswith("."))


class TavlaPageTest(unittest.TestCase):
    def test_the_page_lets_its_cards_take_their_taps_and_all_else_goes_back(self):
        import gen
        widgets = gen.tavla_page()["widgets"]
        ids = [next(iter(w.values())).get("id") for w in widgets]
        self.assertEqual(ids, ["tavla_art", None, "t_plaque", "t_earth_layer", "t_switch"])
        self.assertIn("lvgl.page.show", json.dumps(widgets[1]["obj"]["on_click"]))   # the back tap, under the rest
        plaque = json.dumps(widgets[2]["obj"]["on_click"])
        self.assertIn("lv_label_get_text(id(t_text))[0] != 0", plaque)      # opens the text, or goes back
        self.assertIn("lvgl.page.show", plaque)
        layer = widgets[3]["obj"]
        self.assertFalse(layer["clickable"])                                   # taps fall through to the back tap
        earth_img = layer["widgets"][0]["image"]
        self.assertEqual((earth_img["x"], earth_img["y"]), (272, 60))         # centred on 1024x600

    def test_explanation_font_has_the_plaque_glyphs(self):
        import gen
        import tavla_earth
        text = tavla_earth.explanation(gen._ui())["label"]
        self.assertIn(tuple(int(v) for v in text["text_font"][1:].split("_")), gen.PLAQUE_FONTS)


if __name__ == "__main__":
    unittest.main()
