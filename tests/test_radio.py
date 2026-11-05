#!/usr/bin/env python3
"""Sveriges Radio on the panel (server/agents/radio.py) without the network: fake SR answers and a
fake Home Assistant that records the one action it is asked for.

  python3 -m unittest tests/test_radio.py
"""
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import hass  # noqa: E402
import radio  # noqa: E402

CHANNELS = {"channels": [
    {"id": 132, "name": "P1", "liveaudio": {"url": "https://sr.example/132.mp3"}},
    {"id": 164, "name": "P3", "liveaudio": {"url": "https://sr.example/164.mp3"}},
    {"id": 2576, "name": "P3 Din gata", "liveaudio": {"url": "https://sr.example/2576.mp3"}},
    {"id": 701, "name": "P4 Stockholm", "liveaudio": {"url": "https://sr.example/701.mp3"}},
    {"id": 999, "name": "P4 Norrbotten", "liveaudio": {"url": "https://sr.example/999.mp3"}},
    {"id": 5, "name": "Utan ström", "liveaudio": {}}]}
EPISODE = {"channel": {"currentscheduledepisode": {"title": "P3 med", "subtitle": "Annie Widman",
                                                    "starttimeutc": "/Date(1792666920000)/",
                                                    "endtimeutc": "/Date(1792670400000)/"}}}
SONG = {"playlist": {"song": {"title": "Planetary (GO!)", "artist": "My Chemical Romance"}}}


class FakeHA(BaseHTTPRequestHandler):
    calls = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeHA.calls.append((self.path, self.headers.get("Authorization"), body))
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *args):
        pass


class RadioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.ha = ThreadingHTTPServer(("127.0.0.1", 0), FakeHA)
        threading.Thread(target=cls.ha.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.ha.shutdown()

    def setUp(self):
        self.real_get = radio._get
        radio._get = lambda url, keep_s, timeout=4: (CHANNELS if "/channels" in url else
                                                     EPISODE if "scheduledepisodes" in url else SONG)
        hass.URL = f"http://127.0.0.1:{self.ha.server_port}"
        hass.reset()
        token = Path(tempfile.mkdtemp()) / "token"
        token.write_text("ha-token\n")
        os.environ["JARVIS_HOME_TOKEN_FILE"] = str(token)
        os.environ.pop("JARVIS_RADIO_PLAYER", None)
        FakeHA.calls = []
        radio._playing.clear()
        radio.cancel_sleep()

    def tearDown(self):
        radio._get, hass.URL = self.real_get, None
        os.environ.pop("JARVIS_HOME_TOKEN_FILE", None)
        os.environ.pop("JARVIS_RADIO_PLAYER", None)

    def test_find(self):
        self.assertEqual(radio.find("p3")["name"], "P3")
        self.assertEqual(radio.find("P4")["name"], "P4 Stockholm")         # his local P4
        self.assertEqual(radio.find("din gata")["name"], "P3 Din gata")
        self.assertIsNone(radio.find("Utan ström"))                         # no stream, not offered
        self.assertIsNone(radio.find("Radio Luxemburg"))

    def test_now(self):
        error, text = radio.now("P3")
        self.assertFalse(error)
        self.assertIn("Programmet nu: P3 med, Annie Widman", text)
        self.assertIn("Låten nu: Planetary (GO!) med My Chemical Romance.", text)

    def test_play_needs_the_panel(self):
        error, text = radio.radio("play", "P3")
        self.assertTrue(error)
        self.assertIn("inte ansluten", text)
        self.assertEqual(FakeHA.calls, [])

    def test_play_and_stop_on_the_panel(self):
        os.environ["JARVIS_RADIO_PLAYER"] = "media_player.hallpanel"
        error, text = radio.radio("play", "P4")
        self.assertFalse(error)
        self.assertIn("P4 Stockholm", text)
        self.assertNotIn("Programmet", text)                  # no lookup before he can answer
        self.assertEqual(FakeHA.calls[0], ("/api/services/media_player/play_media", "Bearer ha-token",
                                           {"entity_id": "media_player.hallpanel",
                                            "media_content_id": "https://sr.example/701.mp3",
                                            "media_content_type": "music", "extra": {"metadata": {
                                                "title": "P4 Stockholm", "artist": "Sveriges Radio", "source": "Radio", "image": "", "channel_id": 701}}}))
        self.assertEqual(radio.radio("stop"), (False, "Radion är stoppad."))
        self.assertEqual(FakeHA.calls[1][0], "/api/services/media_player/media_stop")
        self.assertEqual(FakeHA.calls[1][2], {"entity_id": "media_player.hallpanel"})


    def test_pause_resume_and_the_sleep_timer_on_the_panel(self):
        os.environ["JARVIS_RADIO_PLAYER"] = "media_player.hallpanel"
        self.assertTrue(radio.radio("pause")[0])                       # nothing this process started
        radio.radio("play", "P3")
        self.assertEqual(radio.radio("pause"), (False, "Radion är pausad."))
        self.assertEqual(radio.radio("resume"), (False, "P3 fortsätter."))
        self.assertEqual([c[0] for c in FakeHA.calls[1:]],
                         ["/api/services/media_player/media_pause", "/api/services/media_player/media_play"])
        error, text = radio.radio("sleep", minutes=30)
        self.assertFalse(error)
        self.assertRegex(text, r"^Radion stängs av klockan \d\d:\d\d\.$")
        self.assertTrue(radio._sleep["timer"].is_alive())
        timer = radio._sleep["timer"]
        self.assertEqual(radio.radio("sleep", minutes=0), (False, "Insomningstimern är av."))
        timer.join(1)
        self.assertFalse(timer.is_alive())
        radio.radio("sleep", minutes=5)
        timer = radio._sleep["timer"]
        radio.radio("stop")                                             # a stop takes the timer with it
        timer.join(1)
        self.assertFalse(timer.is_alive() or radio._sleep)


if __name__ == "__main__":
    unittest.main()
