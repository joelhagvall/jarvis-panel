"""quick_router: the hand-weighted net picks the fast path for the sentences Joel has said, and 'agent' for the
compound, vague, English and garbled ones. Shadow mode only; see server/quick_router.py.

  python3 -m unittest tests/test_quick_router.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import quick_router  # noqa: E402
import show_router  # noqa: E402


class QuickRouterTest(unittest.TestCase):
    def test_sentences_from_the_history_get_their_fast_path(self):
        cases = {
            "Vad är klockan just nu?": "clock",
            "När går nästa tunnelbana från Stadshagen?": "metro",
            "Vad blir det för väder imorgon?": "weather",
            "Vad har jag på kalendern på lördag?": "calendar_day",
            "Sätt på p fyra.": "radio_play",
            "Stäng av radio.": "radio_off",
            "Stoppa P3.": "radio_off",
            "Kan du höja volumen?": "volume_up",
            "Kan du höja musiken lite?": "volume_up",
            "Kan du sänka volymen med 20%?": "volume_down",
            "Sätt volymen på 30 procent": "volume_set",
            "Hej Jarvis, stoppa musiken": "music_off",
            "Späla Coldplay": "spotify_play",
            "Skulle du kunna sätta på lite Coldplay på Spotify?": "spotify_play",
            "Spela något med Westlife på Spotify.": "spotify_play",
            "Spela något på Spotify.": "spotify_liked",
            "Kan du tjaffla mina gillade låtar på Spotify?": "spotify_liked",
            "Spela nästa låt": "spotify_next",
            "Byt låt": "spotify_next",
            "Fortsätt spela": "spotify_resume",
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(quick_router.classify(text)[0], expected)

    def test_what_belongs_to_the_model_stays_with_it(self):
        for text in (
            "spela någonting som du tror jag tycker om på Spotify.",
            "Spela senaste låten på Spotify",
            "Spela P3 på JBL-högtalen.",
            "Nej, jag sa spela P3.",
            "Det är bara bra. Skulle du kunna sätta på Magnus Karlsson på Spotify?",
            "Hur blir vädret imorgon och behöver jag paraply?",
            "Vad blir vädret på lördag",            # quick_reply answers today and tomorrow only
            "Hur blev vädret efter klockan fyra idag?",
            "Kan du lägga in i kalendern imorgon klockan 9",
            "Vad lader du in på kalendern på lördag?",
            "Ligger Westlife i annat i kalendern?",
            "Spelade Weekend på Spotify.",
            "Spila, Cope",
            "Skulle du kunna spela Spotify på...",
            "Hur mår du idag?",
            "Stäng av",
        ):
            with self.subTest(text=text):
                self.assertEqual(quick_router.classify(text)[0], "agent")

    def test_english_goes_to_the_model(self):
        self.assertEqual(quick_router.classify("Play Coldplay on Spotify", "en"), ("agent", 0.0))
        self.assertEqual(quick_router.classify("Play soccer with Kent.")[0], "agent")

    def test_shadow_line_compares_with_what_the_server_did(self):
        self.assertEqual(quick_router.shadow_line("Spela Coldplay på Spotify", "sv", "command:spotify_play"),
                         "quick-router: net=spotify_play 2.50 took=command:spotify_play agree text='Spela Coldplay på Spotify'")
        self.assertIn(" took=model disagree ", quick_router.shadow_line("Stäng av radio.", None, "model"))
        self.assertIn(" took=model agree ", quick_router.shadow_line("Hur mår du?", None, "model"))

    def test_show_router_reads_the_journal_lines(self):
        rows = show_router.parse([
            "Nov 04 12:00:00 jarvis python3[1]: " + quick_router.shadow_line("Stäng av radio.", None, "model"),
            quick_router.shadow_line("Hur mår du?", None, "model"),
            "home status: provider disruptions over home: 0",
        ])
        self.assertEqual([r["verdict"] for r in rows], ["disagree", "agree"])
        text = show_router.summary(rows)
        self.assertIn("2 questions, 1 agree, 1 disagree", text)
        self.assertIn("net=radio_off took=model", text)

    def test_odd_input_does_not_raise(self):
        for text in ("", ".", "   ", "BEEP", "🎵", "a" * 500):
            quick_router.classify(text)


if __name__ == "__main__":
    unittest.main()
