#!/usr/bin/env python3
"""The one way to Home Assistant (server/agents/hass.py): the token is sent, the first refusal stops every
call so Home Assistant never bans 127.0.0.1, and a new token in the file starts them again.
Run: python3 -m unittest tests/test_hass.py"""
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
import home_status  # noqa: E402
import radio  # noqa: E402


class FakeHA(BaseHTTPRequestHandler):
    """Accepts the token "good" only, and counts every request it gets."""
    def answer(self):
        self.server.seen.append((self.command, self.path, self.headers.get("Authorization")))
        ok = self.headers.get("Authorization") == "Bearer good"
        body = json.dumps([] if ok else {"message": "Unauthorized"}).encode()
        self.send_response(200 if ok else 401)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self.answer()

    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length", 0)))
        self.answer()

    def log_message(self, *args):
        pass


class HassTest(unittest.TestCase):
    def setUp(self):
        self.ha = ThreadingHTTPServer(("127.0.0.1", 0), FakeHA)
        self.ha.seen = []
        threading.Thread(target=self.ha.serve_forever, daemon=True).start()
        self.addCleanup(self.ha.shutdown)
        self.token = Path(tempfile.mkdtemp(prefix="hallpanel-hass-")) / "token"
        self.saved = {k: os.environ.get(k) for k in ("HA_TOKEN_FILE", "JARVIS_RADIO_PLAYER")}
        os.environ["HA_TOKEN_FILE"], os.environ["JARVIS_RADIO_PLAYER"] = str(self.token), "media_player.panel"
        hass.URL = f"http://127.0.0.1:{self.ha.server_port}"
        hass.reset()
        self.addCleanup(self.restore)

    def restore(self):
        hass.URL = None
        hass.reset()
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)

    def test_token_sent_and_json(self):
        self.token.write_text("good\n")
        self.assertEqual(hass.get_json("/api/states"), [])
        hass.call_service("notify", "phone", {"message": "hej"})
        self.assertEqual(self.ha.seen, [("GET", "/api/states", "Bearer good"),
                                        ("POST", "/api/services/notify/phone", "Bearer good")])

    def test_one_refusal_stops_every_part(self):
        self.token.write_text("old\n")
        with self.assertRaises(hass.Refused):
            hass.get_json("/api/states")
        self.assertTrue(hass.refused())
        # the radio, the home status and the rest stop asking: Home Assistant saw one refused request
        self.assertEqual(radio.stop(), (True, "Jarvis har ingen giltig token till Home Assistant."))
        self.assertIn("ingen giltig token", home_status.Home()._ha(None)[0])
        with self.assertRaises(PermissionError):         # an OSError, so callers' handlers catch it
            hass.request("/api/states")
        self.assertEqual(len(self.ha.seen), 1)
        # a new token in the file starts the calls again, without a restart
        self.token.write_text("good\n")
        self.assertFalse(hass.refused())
        self.assertEqual(hass.get_json("/api/states"), [])
        self.assertEqual(len(self.ha.seen), 2)

    def test_no_token_file_calls_nothing(self):
        with self.assertRaises(hass.Refused):
            hass.get_json("/api/states")
        self.assertEqual(self.ha.seen, [])


if __name__ == "__main__":
    unittest.main()
