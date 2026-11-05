"""quick_command: only exact radio-off and volume sentences skip the model; everything else reaches the agent.

  python3 -m unittest tests/test_quick_command.py
"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import quick_command  # noqa: E402


class QuickCommandTest(unittest.TestCase):
    def test_sentences_from_the_history_match(self):
        cases = {
            "Stäng av radion.": ("radio", {"action": "stop"}),
            "Kan du stänga av radion?": ("radio", {"action": "stop"}),
            "Jarvis, stoppa radion tack": ("radio", {"action": "stop"}),
            "Kan du höja volumen?": ("volym", {"action": "up"}),
            "Kan du sänka volymen?": ("volym", {"action": "down"}),
            "Kan du sänka volymen med 20%?": ("volym", {"action": "down", "step": 20}),
            "Höj volymen med 5 procent.": ("volym", {"action": "up", "step": 5}),
            "Sätt volymen på 40 procent": ("volym", {"action": "set", "percent": 40}),
            "Ställ volymen till 0%.": ("volym", {"action": "set", "percent": 0}),
            # Music controls (2026-10-28: "Kan du stänga av musiken?" waited 9.3 s on the model).
            "Kan du stänga av musiken?": ("music", {"action": "off"}),
            "Pausa låten.": ("music", {"action": "off"}),
            "Pausa Spotify": ("spotify", {"action": "pause"}),
            "Nästa låt": ("spotify", {"action": "next"}),
            "Kan du byta låt?": ("spotify", {"action": "next"}),
            "Hoppa över den här låten tack": ("spotify", {"action": "next"}),
            "Spela förra låten": ("spotify", {"action": "previous"}),
            "Fortsätt spela": ("spotify", {"action": "resume"}),
            "Spela vidare.": ("spotify", {"action": "resume"}),
        }
        for text, want in cases.items():
            self.assertEqual(quick_command.match(text), want, text)
            self.assertEqual(quick_command.match(text, "sv"), want, text)

    def test_plain_spotify_requests_play_directly_and_vague_ones_reach_the_agent(self):
        m = quick_command.match
        self.assertEqual(m("Spela Ghost Stories av Coldplay på Spotify."),
                         ("spotify_play", {"query": "ghost stories av coldplay", "media_type": "track"}))
        self.assertEqual(m("Kan du spela albumet Ghost Stories på Spotify?")[1]["media_type"], "album")
        self.assertEqual(m("Spela något med Westlife på Spotify."),
                         ("spotify_play", {"query": "westlife", "media_type": "artist"}))
        self.assertEqual(m("Spela spellistan Liked Songs på Spotify")[1]["media_type"], "playlist")
        for vague in ("Kan du spela Magnus Karlsson med C, C istället för K, på Spotify?",   # a correction
                      "Spela något lugnt på Spotify", "Spela Coldplay eller Keane på Spotify",
                      "Spela en låt som påminner om hösten när det regnar på Spotify tack"):
            self.assertIsNone(m(vague), vague)

    def test_bare_names_play_directly_and_radio_or_places_reach_the_agent(self):
        # 2026-10-30: "Spela Coldplay." and "Spela Anna Book." took 2.7-3.8 s through Sonnet to do the same.
        m = quick_command.match
        self.assertEqual(m("Spela Coldplay."), ("spotify_play", {"query": "coldplay", "media_type": "track"}))
        self.assertEqual(m("Späla Coldplay")[1]["query"], "coldplay")      # Whisper's spelling, 2026-10-30
        self.assertEqual(m("Kan du spela Westlife?"), ("spotify_play", {"query": "westlife", "media_type": "track"}))
        self.assertEqual(m("Spela albumet Parachutes")[1]["media_type"], "album")
        self.assertEqual(m("Spela mina gillade låtar")[1]["query"], "mina gillade låtar")
        for radio in ("Spela P3", "Spela P4 Stockholm", "Sätt på P1"):       # the radio's direct path, not Spotify
            self.assertEqual(m(radio)[0], "radio", radio)
        for other in ("Spela radio", "Spela Ekot", "Spela din gata",
                      "Spela upp nyheterna", "Spela musik", "Spela en låt", "Spela låten igen", "Spela nästa album",
                      "Spela Kent i köket", "Spela P3 på JBL-högtalaren", "Spela om det", "Spela något lugnt",
                      "Spela någonting som du tror jag tycker om", "Hur mår du idag?"):
            self.assertIsNone(m(other), other)
        self.assertIsNone(m("Play Coldplay", "en"))

    def test_radio_channels_and_any_spotify_music_skip_the_model(self):
        # 2026-10-23..30 history: these went through Sonnet in 1.2-3.6 s for one tool call each.
        m = quick_command.match
        cases = {"spela p3": ("radio", {"action": "play", "channel": "P3"}),
                 "Sätt på p fyra.": ("radio", {"action": "play", "channel": "P4"}),
                 "Sätt på P4 Stockholm": ("radio", {"action": "play", "channel": "P4 Stockholm"}),
                 "Stoppa P3.": ("radio", {"action": "stop"}), "Stäng av P3.": ("radio", {"action": "stop"}),
                 "Spela något på Spotify.": ("spotify_play", {"query": "mina gillade låtar", "media_type": "track"})}
        for text, want in cases.items():
            self.assertEqual(m(text), want, text)
        for other in ("Nej, jag sa spela P3.", "Spela P3 om tio minuter", "Vad spelas på P3?", "Spela P5",
                      "Spela något lugnt på Spotify", "Spela något"):
            self.assertIsNone(m(other), other)

    def test_skulle_du_kunna_and_lite_x_play_directly(self):
        # 2026-11-01 history: four "Skulle du kunna spela Coldplay på Spotify?" waited 2.0-2.6 s on the model,
        # and "Skulle du kunna sätta på lite Coldplay på Spotify?" 2.6 s; "Kan du spela ..." played in 0.5-1.0 s.
        m = quick_command.match
        self.assertEqual(m("Skulle du kunna spela Coldplay på Spotify?"),
                         ("spotify_play", {"query": "coldplay", "media_type": "track"}))
        self.assertEqual(m("Skulle du kunna sätta på lite Coldplay på Spotify?"),
                         ("spotify_play", {"query": "coldplay", "media_type": "artist"}))
        self.assertEqual(m("Skulle du kunna stänga av radion?"), ("radio", {"action": "stop"}))
        self.assertEqual(m("Skulle du kunna höja volymen?"), ("volym", {"action": "up"}))
        self.assertEqual(m("Sätt på lite musik på Spotify"),
                         ("spotify_play", {"query": "mina gillade låtar", "media_type": "track"}))
        for other in ("Skulle du kunna spela Spotify på...", "Skulle du kunna sätta på lite lugn musik på Spotify?",
                      "Skulle du kunna boka in två tvättstugepass till mig imorgon?",
                      "Sätt på lite Coldplay", "Skulle du kunna spela något du tror jag gillar på Spotify?"):
            self.assertIsNone(m(other), other)

    def test_places_channels_compounds_and_other_languages_reach_the_agent(self):
        for text in ("Stäng av radion om tio minuter", "Stäng av radion och sätt på P1", "Stäng av musiken om en timme",
                     "Fortsätt", "Nästa låt är bra", "Vilken låt är nästa?", "Spela nästa album",
                     "Höj volymen på JBL:en", "Sänk volymen lite", "Höj volymen med tjugo procent",
                     "Sätt volymen på 140 procent", "Höj volymen med 0 procent", "Vad är volymen?",
                     "Stäng av radion på panelen", "Varför höjde du volymen?", "Spela P3 på JBL-högtalaren"):
            self.assertIsNone(quick_command.match(text), text)
        self.assertIsNone(quick_command.match("Turn off the radio", "en"))
        self.assertIsNone(quick_command.match("Stäng av radion", "en"))


if __name__ == "__main__":
    unittest.main()
