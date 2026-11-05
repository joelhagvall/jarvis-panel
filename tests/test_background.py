#!/usr/bin/env python3
"""The panel's background chosen in Home Assistant (server/agents/background.py): NASA's picture or a painting,
published to where the panel downloads it, kept across a restart, and the plaque's title and credit.
Run: python3 -m unittest tests/test_background.py"""
import io
import json
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import background  # noqa: E402


class BackgroundTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hallpanel-bg-"))
        self.saved = (background.STATE, background.OUT, background.APOD_CACHE, background.UPLOADS, background.CUSTOM,
                      background.DAILY_DIR)
        background.DAILY_DIR = self.tmp / "daily"      # Jarvis's real daily pictures would add choices
        background.STATE, background.OUT = self.tmp / "background.json", self.tmp / "www"
        background.APOD_CACHE, background.UPLOADS = self.tmp / "apod", self.tmp / "uploads"
        background.CUSTOM = self.tmp / "custom"
        background.OUT.mkdir()
        background.APOD_CACHE.mkdir()
        from PIL import Image
        Image.new('RGB', (1024, 600), (13, 17, 23)).save(background.APOD_CACHE / "bg_2026-10-22.png")
        art = background.APOD_CACHE / "art_2026-10-22.png"
        Image.new('RGB', (1024, 600), (20, 60, 30)).save(art)
        self.nasa_art = background.panel_image(art).read_bytes()
        self.nasa_bg = background.panel_image(background.APOD_CACHE / "bg_2026-10-22.png").read_bytes()
        (background.APOD_CACHE / "latest.json").write_text(json.dumps(
            {"date": "2026-10-22", "file": "bg_2026-10-22.png", "art": "art_2026-10-22.png"}))
        (background.OUT / "apod.json").write_text(json.dumps({"title": "The First Green Star", "credit": "Mark A. Garlick"}))
        self.addCleanup(self.restore)

    def restore(self):
        (background.STATE, background.OUT, background.APOD_CACHE, background.UPLOADS, background.CUSTOM,
         background.DAILY_DIR) = self.saved
        shutil.rmtree(self.tmp)

    def test_nasa_by_default(self):
        self.assertEqual(background.current(), "NASA")
        self.assertEqual(background.publish(), "NASA")
        self.assertEqual((background.OUT / "bg.png").read_bytes(), self.nasa_bg)
        self.assertEqual((background.OUT / "art.png").read_bytes(), self.nasa_art)
        self.assertEqual(background.art(), ("The First Green Star", "Mark A. Garlick · NASA APOD"))   # its source
        self.assertTrue((background.OUT / 'voice_bg.png').is_file())

    def test_qoi_publication_tracks_pixels_choice_and_repairs_a_missing_cached_sidecar(self):
        from PIL import Image
        for choice in ('NASA', 'Mandelblom', 'NASA'):
            background.choose(choice)
            for name in ('bg', 'art', 'voice_bg'):
                with Image.open(background.OUT / f'{name}.png') as png, Image.open(background.OUT / f'{name}.qoi') as qoi:
                    self.assertEqual(qoi.size, png.size)
                    self.assertEqual(qoi.convert('RGB').tobytes(), png.convert('RGB').tobytes())
        cached = background.panel_image(background.APOD_CACHE / 'art_2026-10-22.png')
        cached.with_suffix('.qoi').unlink()
        background.publish()
        self.assertTrue(cached.with_suffix('.qoi').is_file())

    def test_voice_background_tracks_choice_and_reuses_cached_render(self):
        background.publish()
        first = (background.OUT / 'voice_bg.png').read_bytes()
        cached = background.voice_backdrop(background.APOD_CACHE / 'art_2026-10-22.png')
        before = cached.stat().st_mtime_ns
        background.publish()
        self.assertEqual(cached.stat().st_mtime_ns, before)
        background.choose('Mandelblom')
        self.assertNotEqual((background.OUT / 'voice_bg.png').read_bytes(), first)
        background.choose('NASA')
        self.assertEqual((background.OUT / 'voice_bg.png').read_bytes(), first)
        from PIL import Image
        with Image.open(background.OUT / 'voice_bg.png') as image:
            self.assertEqual(image.size, (1024, 600))
            self.assertLess(max(image.getpixel((0, 0))), 120)  # readable white text on the tinted glass
            # A flat source must stay flat: no frame, outlined panel or rectangular text plate.
            # Dithering repeats locally, rather than creating a rectangular plate.
            self.assertEqual(image.crop((0, 0, 8, 8)).tobytes(), image.crop((512, 312, 520, 320)).tobytes())
            self.assertEqual(image.crop((0, 0, 8, 8)).tobytes(), image.crop((72, 304, 80, 312)).tobytes())

    def test_tavla_only_change_invalidates_the_complete_image_set(self):
        from PIL import Image
        background.publish()
        first = background.version()
        old_bg = (background.OUT / "bg.png").read_bytes()
        old_art = (background.OUT / "art.png").read_bytes()
        background.publish()
        self.assertEqual(background.version(), first)
        Image.new("RGB", (1024, 600), (80, 20, 180)).save(background.APOD_CACHE / "art_2026-10-22.png")
        background.publish()
        self.assertEqual((background.OUT / "bg.png").read_bytes(), old_bg)
        self.assertNotEqual((background.OUT / "art.png").read_bytes(), old_art)
        self.assertNotEqual(background.version(), first)

    def test_a_painting_at_once_and_back(self):
        self.assertEqual(background.choose("Stjärnenatten"), "Stjärnenatten")
        self.assertEqual((background.OUT / "art.png").read_bytes(),
                         background.panel_image(background.ASSETS / "art_starry.png").read_bytes())
        self.assertEqual((background.OUT / "bg.png").read_bytes(), background.panel_image(background.ASSETS / "bg_starry.png").read_bytes())
        self.assertEqual(background.art(), ("Stjärnenatten", "Vincent van Gogh, 1889"))
        self.assertEqual(background.current(), "Stjärnenatten")          # kept in the file
        background.choose("NASA")
        self.assertEqual((background.OUT / "bg.png").read_bytes(), self.nasa_bg)

    def test_unknown_choice_and_missing_files(self):
        with self.assertRaises(ValueError):
            background.choose("Mona Lisa")
        self.assertEqual(background.current(), "NASA")
        (background.APOD_CACHE / "latest.json").unlink()                 # before NASA's first day
        (background.OUT / "bg.png").write_bytes(b"van gogh")
        background.publish()
        self.assertEqual((background.OUT / "bg.png").read_bytes(), b"van gogh")   # kept, not emptied


def jpeg(w=1600, h=1000, colour=(40, 90, 160)):
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), colour).save(buf, "JPEG")
    return buf.getvalue()


class Pictures(BaseHTTPRequestHandler):
    """/bild.jpg is a picture, /sida.html is not."""
    def do_GET(self):
        body = jpeg() if self.path == "/bild.jpg" else b"<html>nej</html>"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


@unittest.skipUnless(__import__("importlib").util.find_spec("numpy"), "needs numpy and Pillow")
class OwnPicturesTest(BackgroundTest):
    """Uploaded pictures and links; the glass is baked for real (a second or two on the Mac)."""
    test_nasa_by_default = test_a_painting_at_once_and_back = test_unknown_choice_and_missing_files = None
    test_voice_background_tracks_choice_and_reuses_cached_render = None

    def test_uploaded_picture_is_a_choice(self):
        background.UPLOADS.mkdir()
        (background.UPLOADS / "fjallen.jpg").write_bytes(jpeg())
        (background.UPLOADS / "anteckning.txt").write_text("inte en bild")
        self.assertEqual(background.choices(), ["NASA", "Stjärnenatten", "Mandelblom", "fjallen"])
        before = background.version()
        self.assertEqual(background.choose("fjallen"), "fjallen")
        self.assertNotEqual(background.version(), before)
        from PIL import Image
        for name in ("bg.png", "art.png"):
            with Image.open(background.OUT / name) as im:
                self.assertEqual(im.size, (1024, 600))
        self.assertEqual(background.art(), ("fjallen", "Uppladdad i Home Assistant"))
        (background.UPLOADS / "fjallen.jpg").unlink()                     # deleted in Home Assistant: NASA again
        self.assertEqual(background.current(), "NASA")

    def test_link(self):
        server = ThreadingHTTPServer(("127.0.0.1", 0), Pictures)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.shutdown)
        base = f"http://127.0.0.1:{server.server_port}"
        with self.assertRaisesRegex(ValueError, "inte på en bild"):
            background.from_link(base + "/sida.html")
        with self.assertRaisesRegex(ValueError, "https"):
            background.from_link("file:///etc/passwd")
        self.assertNotIn("Egen bild", background.choices())
        self.assertEqual(background.from_link(base + "/bild.jpg"), "Egen bild")
        self.assertIn("Egen bild", background.choices())
        self.assertEqual(background.art(), ("Egen bild", "127.0.0.1"))
        self.assertEqual((background.OUT / "art.png").read_bytes()[:4], b"\x89PNG")


if __name__ == "__main__":
    unittest.main()
