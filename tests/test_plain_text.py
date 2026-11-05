"""Jarvis's replies reach the panel and the voice without emoji (server/plain_text.py)."""
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "server/agents")]

from plain_text import no_emoji  # noqa: E402


class NoEmojiTest(unittest.TestCase):
    def test_a_playlist_name_loses_its_emoji_not_its_words(self):
        # 2026-11-01: the panel showed "fall songs 2026 □ chill autumn vibes"
        self.assertEqual(no_emoji("Spelar fall songs 2026 🍂 chill autumn vibes med Filtr US."),
                         "Spelar fall songs 2026 chill autumn vibes med Filtr US.")
        self.assertEqual(no_emoji("Klart ✅ 👍🏽 ❤️ 🇸🇪 ⏩"), "Klart ")

    def test_text_the_fonts_have_stays(self):
        for text in ("8° ute, 3 s → under 0,15 s, 120 €", "Sigur Rós · Björk – “Jóga”", "rad ett\n  rad två", ""):
            self.assertEqual(no_emoji(text), text)

    def test_ask_agent_cleans_both_the_stream_and_the_reply(self):
        import hallpanel_server
        deltas = []

        def fake(history, text, on_delta=None, *rest):
            on_delta("Spelar 🍂 hösten.")
            return "Spelar 🍂 hösten.", {}

        with mock.patch.object(hallpanel_server, "ask_reply", fake):
            reply, _ = hallpanel_server.ask_agent(None, "spela något", deltas.append)
        self.assertEqual((reply, deltas), ("Spelar hösten.", ["Spelar hösten."]))


if __name__ == "__main__":
    unittest.main()
