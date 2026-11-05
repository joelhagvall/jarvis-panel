"""Spotify through Spotify Soloist (server/agents/spotify.py) with a fake `soloist` binary: no audio, network or model."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server/agents"))
import spotify
import agent_core

ID = "0123456789abcdefghijkl"
FAKE = r'''#!/bin/sh
# Records its arguments; answers `now --json` (STATUS, ACTIVE: another device has the session when false) and fails
# with FAIL_CODE (soloist ctl: 2 connection failed, 3 server error) when FAIL is set.
printf '%s\n' "$*" >> "$CALLS"
[ -z "${FAIL:-}" ] || { echo "not logged in" >&2; exit "${FAIL_CODE:-3}"; }
# After `play URI` the fake reports that URI as the playing item, named "My Love" by Westlife.
[ "$4" != play ] || [ -z "${5:-}" ] || printf '%s' "$5" > "$CALLS.uri"
if [ "$4" = now ]; then
  if [ -s "$CALLS.uri" ]; then
    echo '{"type":"playback_state","status":"playing","is_active":true,"item":{"uri":"'"$(cat "$CALLS.uri")"'","decorations":{"identity":{"name":"My Love"}},"creators":[{"entity":{"decorations":{"identity":{"name":"Westlife"}}}}]},"volume":100}'
  else
    echo '{"type":"playback_state","status":"'"${STATUS:-playing}"'","is_active":'"${ACTIVE:-true}"',"item":{"uri":"spotify:track:bailandobailandobailan","decorations":{"identity":{"name":"Bailando"},"parent":{"entity":{"decorations":{"identity":{"name":"Bailando"}}}}},"creators":[{"entity":{"decorations":{"identity":{"name":"Paradisio"}}}}]},"context":{"decorations":{"identity":{"name":"Liked Songs"}}},"position":{"position_ms":32683},"volume":100}'
  fi
fi
'''
HITS = (False, "Webbsök efter \"x\":\n"
        f"- Flying Without Wings - song and lyrics by Westlife | Spotify: Listen (https://open.spotify.com/track/{ID})\n"
        f"- Westlife | Spotify: artist (https://open.spotify.com/artist/{ID[::-1]})\n"
        "- Westlife Wikipedia: a band (https://sv.wikipedia.org/wiki/Westlife)")


class ConfidentTest(unittest.TestCase):
    def test_page_titles_lose_listen_to(self):
        self.assertEqual(spotify._title("Listen to Ghost Stories med Coldplay"), "Ghost Stories med Coldplay")

    def test_every_word_of_the_request_must_be_in_the_match(self):
        found = [{"name": "Ghost Story med Coldplay", "uri": "a"}, {"name": "Ghost Stories med Coldplay", "uri": "b"}]
        self.assertEqual(spotify.confident("ghost stories av coldplay", found)["uri"], "b")
        self.assertIsNone(spotify.confident("ghost stories av keane", found))       # never the wrong music

    def test_quick_play_leaves_an_uncertain_search_to_the_model(self):
        self.assertIsNone(spotify.quick_play("helt okänd låt", websearch=lambda q: HITS))
        self.assertIsNone(spotify.quick_play("x", websearch=lambda q: (True, "search failed")))


class FakeSpeaker:
    class SpeakerError(Exception):
        pass

    def __init__(self, state="stop", connected=True):
        self.state, self.is_connected, self.calls = state, connected, []

    def configured(self): return True
    def mpd_state(self): return self.state
    def mpd(self, *commands): self.calls.append(("mpd",) + commands)
    def connected(self): return {"name": "JBL Charge 4"} if self.is_connected else None
    def control(self, action, name=None):
        self.calls.append(("control", action))
        return False, "Kopplad till JBL Charge 4."


class SpotifyTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        tmp = Path(self.tmp.name)
        (tmp / "soloist").write_text(FAKE); (tmp / "soloist").chmod(0o755)
        self.calls = tmp / "calls"
        env = {"JARVIS_SOLOIST_WS": "127.0.0.1:9431", "JARVIS_SOLOIST_BIN": str(tmp / "soloist"), "CALLS": str(self.calls)}
        p = patch.dict(os.environ, env); p.start(); self.addCleanup(p.stop)
        self.speaker = FakeSpeaker()
        p = patch.object(spotify, "speaker", self.speaker); p.start(); self.addCleanup(p.stop)

    def ctl(self):
        return [line.split(" ", 3)[3] for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def test_every_command_goes_to_the_loopback_websocket(self):
        spotify.control("pause")
        self.assertEqual(self.calls.read_text().splitlines(), ["ctl --ws 127.0.0.1:9431 now --json",
                                                               "ctl --ws 127.0.0.1:9431 pause"])

    def test_controls_leave_another_devices_spotify_alone(self):
        """2026-10-29: Joel's iPhone had the session, Soloist still said "playing", and a pause paused the phone."""
        with patch.dict(os.environ, {"ACTIVE": "false"}):
            for action, args in (("pause", {}), ("next", {}), ("previous", {}), ("volume", {"level": 20}),
                                 ("shuffle", {"enabled": True}), ("add_to_queue", {"uri": f"spotify:track:{ID}"})):
                self.assertEqual(spotify.control(action, **args), (True, spotify.ELSEWHERE), action)
            self.assertEqual(set(self.ctl()), {"now --json"})
            err, text = spotify.control("resume")           # the phone plays: it keeps the music
            self.assertTrue(err)
            self.assertNotIn("play", self.ctl())

    def test_resume_brings_a_paused_session_to_the_jbl(self):
        with patch.dict(os.environ, {"ACTIVE": "false", "STATUS": "paused"}):
            self.assertEqual(spotify.control("resume"), (False, "Fortsätter Spotify."))
        self.assertEqual([c for c in self.ctl() if c != "now --json"], ["activate", "play"])

    def test_shuffle_and_queue(self):
        self.assertEqual(spotify.control("shuffle", enabled=True), (False, "Blandar Spotify."))
        self.assertEqual(spotify.control("shuffle", enabled=False), (False, "Spelar Spotify i ordning."))
        self.assertTrue(spotify.control("shuffle")[0])
        err, text = spotify.control("add_to_queue", query="flying without wings", websearch=lambda q: HITS)
        self.assertEqual((err, text), (False, "Lägger Flying Without Wings med Westlife i kön."))
        self.assertEqual(spotify.control("add_to_queue", uri=f"https://open.spotify.com/track/{ID}"),
                         (False, "Lägger låten i kön."))
        self.assertEqual([c for c in self.ctl() if c != "now --json"],
                         ["shuffle on", "shuffle off", f"add-to-queue spotify:track:{ID}", f"add-to-queue spotify:track:{ID}"])
        # Soloist queues songs only
        self.assertEqual(spotify.control("add_to_queue", uri=f"spotify:album:{ID}"), (True, "Bara låtar kan läggas i kön."))
        self.assertTrue(spotify.control("add_to_queue")[0])
        self.assertEqual(agent_core.direct_reply("spotify", {"action": "shuffle"}, False, "Blandar Spotify."), "Blandar Spotify.")

    def test_play_by_query_plays_the_first_spotify_link_of_the_asked_type(self):
        searched = []
        err, text = spotify.control("play", query="Westlife Flying Without Wings",
                                    websearch=lambda q: searched.append(q) or HITS)
        self.assertFalse(err)
        # The spoken name is what Soloist reports playing, not the search hit's page title (2026-10-28).
        # Joel, 2026-10-30: just what plays, never where ("på högtalaren").
        self.assertEqual(text, "Spelar My Love med Westlife.")
        self.assertEqual(searched, ["open.spotify.com track Westlife Flying Without Wings"])
        self.assertEqual([c for c in self.ctl() if c != "now --json"], ["activate", "shuffle off", f"play spotify:track:{ID}"])

    def test_quick_play_plays_a_confident_match_without_the_model(self):
        confirmed = []
        with patch.object(spotify, "_confirm", lambda pick, before: confirmed.append(pick["uri"])), \
                patch.object(spotify, "_satellite_state", lambda: "idle"):
            err, text = spotify.quick_play("flying without wings westlife", websearch=lambda q: HITS)
        self.assertFalse(err)
        # typed (the panel is idle): played at once; said from the hit, the start only logged (Joel, 2026-10-28)
        self.assertEqual(text, "Spelar Flying Without Wings med Westlife.")
        self.assertEqual([c for c in self.ctl() if c != "now --json"], ["activate", "shuffle off", f"play spotify:track:{ID}"])
        self.assertEqual(confirmed, [f"spotify:track:{ID}"])

    def test_by_voice_the_music_starts_only_once_jarvis_has_said_it(self):
        states = iter(["processing", "responding", "responding", "idle"])
        seen = []

        def state():
            seen.append(next(states, "idle"))
            return seen[-1]
        with patch.object(spotify, "_confirm", lambda pick, before: None), \
                patch.object(spotify, "_satellite_state", state), patch.object(spotify, "AFTER_REPLY_POLL_S", 0), \
                patch.object(spotify, "_wake_link", lambda: None):
            gate = __import__("threading").Event()
            real_start = spotify._start
            with patch.object(spotify, "_start", lambda pick, *rest: (gate.wait(5), real_start(pick, *rest))):
                err, text = spotify.quick_play("flying without wings westlife", websearch=lambda q: HITS)
                self.assertEqual(text, "Spelar Flying Without Wings med Westlife.")
                self.assertNotIn(f"play spotify:track:{ID}", self.ctl())     # nothing plays while it is said
                gate.set()
                spotify._pending.join(5)
        self.assertEqual(seen, ["processing", "responding", "responding", "idle"])
        self.assertEqual([c for c in self.ctl() if c != "now --json"], ["activate", "shuffle off", f"play spotify:track:{ID}"])

    def test_the_models_play_by_voice_also_waits_for_the_reply(self):
        states = iter(["processing", "processing", "responding", "idle"])
        with patch.object(spotify, "_confirm", lambda pick, before: None), \
                patch.object(spotify, "_satellite_state", lambda: next(states, "idle")), \
                patch.object(spotify, "AFTER_REPLY_POLL_S", 0), patch.object(spotify, "_wake_link", lambda: None):
            gate = __import__("threading").Event()
            real_start = spotify._start
            with patch.object(spotify, "_start", lambda pick, *rest: (gate.wait(5), real_start(pick, *rest))):
                err, text = spotify.control("play", query="Westlife Flying Without Wings", websearch=lambda q: HITS)
                self.assertFalse(err)
                # spoken as it is in Swedish (agent_core.direct_reply): nothing but the answer itself
                self.assertEqual(text, "Spelar Flying Without Wings med Westlife.")
                self.assertEqual(agent_core.direct_reply("spotify", {"action": "play"}, err, text), text)
                self.assertNotIn(f"play spotify:track:{ID}", self.ctl())
                gate.set()
                spotify._pending.join(5)
        self.assertEqual([c for c in self.ctl() if c != "now --json"], ["activate", "shuffle off", f"play spotify:track:{ID}"])

    def voice(self, states):
        it = iter(states)
        return [patch.object(spotify, "_confirm", lambda pick, before: None),
                patch.object(spotify, "_satellite_state", lambda: next(it, "idle")),
                patch.object(spotify, "AFTER_REPLY_POLL_S", 0), patch.object(spotify, "_wake_link", lambda: None),
                patch.object(spotify.time, "sleep", lambda s: None)]

    def test_shuffle_my_liked_songs_by_voice_shuffles_after_the_list_has_started(self):
        """2026-11-01 14:36 "shuffla mina gillade låtar": shuffle was set during the reply, then the deferred play
        began Liked Songs at its first song, in order. One call, play with shuffle, shuffles after the start."""
        from contextlib import ExitStack
        with ExitStack() as stack:
            for p in self.voice(["processing", "responding", "idle"]):
                stack.enter_context(p)
            stack.enter_context(patch.dict(os.environ, {"JARVIS_SPOTIFY_USER": "spotify-user"}))
            err, text = spotify.control("play", query="mina gillade låtar", shuffle=True)
            self.assertEqual((err, text), (False, "Spelar dina gillade låtar blandat."))
            spotify._pending.join(5)
        calls = [c for c in self.ctl() if c != "now --json"]
        self.assertEqual(calls, ["activate", "play spotify:user:spotify-user:collection", "shuffle on", "next"])

    def test_a_separate_shuffle_call_by_voice_waits_for_the_play_of_the_same_turn(self):
        import threading
        spoken = threading.Event()                                   # Jarvis talks until the test says it is done
        with patch.object(spotify, "_confirm", lambda pick, before: None), \
                patch.object(spotify, "_satellite_state", lambda: "idle" if spoken.is_set() else "responding"), \
                patch.object(spotify, "AFTER_REPLY_POLL_S", 0.01), patch.object(spotify, "_wake_link", lambda: None), \
                patch.dict(os.environ, {"ACTIVE": "true", "STATUS": "playing", "JARVIS_SPOTIFY_USER": "spotify-user"}):
            shuffled = spotify.control("shuffle", enabled=True)      # the model may call it first
            spotify.control("play", query="mina gillade låtar")
            self.assertEqual(shuffled, (False, "Blandar Spotify."))
            self.assertFalse(any(c.startswith(("shuffle", "play ")) for c in self.ctl()))   # nothing during the reply
            spoken.set()
            spotify._pending.join(5)
            spotify._shuffle_pending.join(5)
        calls = [c for c in self.ctl() if c != "now --json"]
        self.assertEqual(calls, ["activate", "shuffle off", "play spotify:user:spotify-user:collection", "shuffle on", "next"])

    def test_a_new_play_is_in_order_unless_shuffle_is_asked(self):
        """2026-11-01 "shuffle ska ej va default": Spotify kept shuffle on from an earlier "shuffla", and "Spela
        Coldplay" began on a random song. Resume leaves shuffle as it is."""
        spotify.control("play", query="Westlife Flying Without Wings", websearch=lambda q: HITS)
        self.assertEqual([c for c in self.ctl() if c != "now --json"],
                         ["activate", "shuffle off", f"play spotify:track:{ID}"])
        with patch.dict(os.environ, {"ACTIVE": "true", "STATUS": "paused"}):
            before = len(self.ctl())
            spotify.control("resume")
            self.assertFalse(any(c.startswith("shuffle") for c in self.ctl()[before:]))

    def test_shuffle_while_music_already_plays_does_not_skip(self):
        self.assertEqual(spotify.control("shuffle", enabled=True), (False, "Blandar Spotify."))
        self.assertNotIn("next", self.ctl())

    def test_the_jbl_link_is_woken_while_jarvis_answers_and_let_go_after_the_start(self):
        events = []

        class Silence:
            def terminate(self):
                events.append("silence off")
        states = iter(["processing", "responding", "idle"])
        with patch.object(spotify, "_wake_link", lambda: events.append("silence on") or Silence()), \
                patch.object(spotify, "_satellite_state", lambda: next(states, "idle")), \
                patch.object(spotify, "AFTER_REPLY_POLL_S", 0):
            spotify._after_reply(lambda: events.append("spotify plays")).join(5)
        self.assertEqual(events, ["silence on", "spotify plays", "silence off"])

    def test_liked_songs_play_without_searching_on_both_paths(self):
        def no_search(q):
            raise AssertionError("searched for the liked songs")
        with patch.dict(os.environ, {"JARVIS_SPOTIFY_USER": "spotify-user"}), \
                patch.object(spotify, "_satellite_state", lambda: None), patch.object(spotify, "_confirm", lambda p, b: None):
            err, text = spotify.quick_play("mina gillade låtar", websearch=no_search)
            self.assertEqual(text, "Spelar dina gillade låtar.")
            self.assertIn("play spotify:user:spotify-user:collection", self.ctl())
            err, text = spotify.control("play", query="Mina gillade låtar", media_type="playlist", websearch=no_search)
            self.assertFalse(err)
        self.assertEqual([c for c in self.ctl() if c.startswith("play ")], ["play spotify:user:spotify-user:collection"] * 2)

    def test_a_failed_start_after_the_reply_is_logged(self):
        with patch.object(spotify, "_satellite_state", lambda: None), \
                patch.dict(os.environ, {"FAIL": "1"}), patch("builtins.print") as log:
            spotify._start({"uri": f"spotify:track:{ID}", "name": "X"})
        self.assertIn("direct play FAILED", log.call_args[0][0])

    def test_a_playing_radio_is_stopped_and_an_off_speaker_connected_before_spotify(self):
        self.speaker.state, self.speaker.is_connected = "play", False
        spotify.control("play", uri=f"https://open.spotify.com/intl-sv/album/{ID}?si=x")
        self.assertEqual(self.speaker.calls, [("mpd", "stop"), ("control", "connect")])
        self.assertEqual([c for c in self.ctl() if c != "now --json"], ["activate", "shuffle off", f"play spotify:album:{ID}"])

    def test_no_spotify_link_found_starts_nothing(self):
        err, text = spotify.control("play", query="okänd låt", websearch=lambda q: (False, "- Något: text (https://example.com/a)"))
        self.assertTrue(err)
        self.assertIn("hittade det inte", text)
        self.assertEqual(self.ctl(), [])

    def test_search_lists_matches_without_playing(self):
        err, text = spotify.control("search", query="Westlife", media_type="artist", websearch=lambda q: HITS)
        self.assertFalse(err)
        matches = json.loads(text)["matches"]
        self.assertEqual(matches[0]["uri"], f"spotify:artist:{ID[::-1]}")   # the asked type first
        self.assertEqual(self.ctl(), [])

    def test_controls_status_and_volume(self):
        for action, expected, ack in (("pause", "pause", "Pausar Spotify."), ("resume", "play", "Fortsätter Spotify."),
                                      ("next", "next", "Nästa låt."), ("previous", "prev", "Föregående låt.")):
            self.assertEqual(spotify.control(action), (False, ack))
            self.assertEqual(self.ctl()[-1], expected)
        err, text = spotify.control("status")
        self.assertEqual(json.loads(text), {"status": "playing", "track": "Bailando", "uri": "spotify:track:bailandobailandobailan", "artists": ["Paradisio"],
                                            "album": "Bailando", "from": "Liked Songs", "volume": 100, "active": True,
                                            "position_s": 33})
        self.assertEqual(spotify.control("volume", level=35), (False, "Spotify på 35 procent."))
        self.assertEqual(self.ctl()[-1], "volume 35")
        for bad in (None, -1, 101, True, "50"):
            self.assertTrue(spotify.control("volume", level=bad)[0])

    def test_links_only_to_spotify_items_and_bad_requests_run_nothing(self):
        for bad in ("https://evil.example/track/" + ID, "file:///etc/passwd", "spotify:user:abc", "spotify:track:short"):
            self.assertTrue(spotify.control("play", uri=bad)[0])
        self.assertTrue(spotify.control("pause", query="x")[0])
        self.assertTrue(spotify.control("dance")[0])
        self.assertTrue(spotify.control("play", query="x" * 301)[0])
        self.assertEqual(self.ctl(), [])

    def test_the_websocket_must_be_loopback(self):
        with patch.dict(os.environ, {"JARVIS_SOLOIST_WS": "jarvis.local:9431"}):
            err, text = spotify.control("pause")
        self.assertTrue(err)
        self.assertEqual(self.ctl(), [])

    def test_a_refusing_or_missing_soloist_is_a_short_error(self):
        with patch.dict(os.environ, {"FAIL": "1"}):
            err, text = spotify.control("pause")
        self.assertTrue(err); self.assertIn("not logged in", text)
        with patch.dict(os.environ, {"FAIL": "1", "FAIL_CODE": "2"}):
            self.assertEqual(spotify.control("next"), (True, "Spotify-högtalaren går inte att nå just nu."))
        with patch.dict(os.environ, {"JARVIS_SOLOIST_BIN": "/nonexistent/soloist"}):
            self.assertEqual(spotify.control("next"), (True, "Spotify-högtalaren är inte installerad på Jarvis."))

    def test_music_off_pauses_spotify_when_it_plays_else_stops_the_radio(self):
        brain = agent_core.ToolAgent(); brain._init_tools(False, None, None, "")
        ran = []
        with patch.object(spotify, "configured", return_value=True), patch.object(agent_core.lookup, "jina_key", return_value=None):
            brain._ensure_tools()
        brain.run_tool = lambda name, args: ran.append((name, args)) or (False, "ok")
        for state in ({"status": "playing", "active": True}, {"status": "paused", "active": True},
                      {"status": "playing", "active": False}):         # the last: Joel's phone plays, not the JBL
            with patch.object(spotify, "status", return_value=state):
                brain.command("music", {"action": "off"})
        self.assertEqual(ran, [("spotify", {"action": "pause"}), ("radio", {"action": "stop"}), ("radio", {"action": "stop"})])

    def test_not_registered_until_configured_and_not_an_early_tool(self):
        for enabled in (False, True):
            brain = agent_core.ToolAgent(); brain._init_tools(False, None, None, "")
            with patch.object(spotify, "configured", return_value=enabled), patch.object(agent_core.lookup, "jina_key", return_value=None):
                brain._ensure_tools()
            self.assertEqual("spotify" in brain.local_tools, enabled)
            self.assertNotIn("spotify", brain.early)
        self.assertEqual(agent_core.direct_reply("spotify", {"action": "pause"}, False, "Pausar Spotify."), "Pausar Spotify.")
        self.assertIsNone(agent_core.direct_reply("spotify", {"action": "pause"}, True, "Failed"))


class FakeSpotifyApi:
    """urlopen for accounts.spotify.com and api.spotify.com: a token, then canned search answers."""
    def __init__(self, *answers):
        self.answers, self.urls = list(answers), []

    def __call__(self, request, timeout=None):
        import io
        self.urls.append(request.full_url)
        if "accounts.spotify.com" in request.full_url:
            return io.BytesIO(b'{"access_token": "t", "expires_in": 3600}')
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return io.BytesIO(json.dumps(answer).encode())


API_ANSWER = {
    "tracks": {"items": [{"name": "Flying Without Wings", "uri": f"spotify:track:{ID}", "artists": [{"name": "Westlife"}]}]},
    "albums": {"items": [{"name": "Ghost Stories", "uri": f"spotify:album:{ID}", "artists": [{"name": "Coldplay"}]}]},
    "artists": {"items": [{"name": "Westlife", "uri": f"spotify:artist:{ID[::-1]}"}, None]},
    "playlists": {"items": [None, {"name": "Lugnt", "uri": "spotify:playlist:not-a-real-id", "owner": {"display_name": "x"}}]}}


class LikedSongsTest(unittest.TestCase):
    """2026-10-28: "spela mina gillade låtar" played someone else's public playlist named "Mina gillade låtar"."""
    def test_the_liked_songs_are_the_accounts_collection_never_a_search(self):
        with patch.dict(os.environ, {"JARVIS_SPOTIFY_USER": "spotify-user"}):
            for query in ("mina gillade låtar", "Gillade låtar", "liked songs", "my liked songs", "mina sparade låtar",
                          "mina favoritlåtar."):
                self.assertEqual(spotify.liked(query), "spotify:user:spotify-user:collection", query)
            for query in ("gillade låtar av kent", "Mina gillade låtar 2019", "kent", ""):
                self.assertIsNone(spotify.liked(query), query)
        with patch.dict(os.environ, {"JARVIS_SPOTIFY_USER": "bad user;rm"}):
            self.assertIsNone(spotify.liked("mina gillade låtar"))        # no account name, no collection


class ApiSearchTest(unittest.TestCase):
    def setUp(self):
        spotify._token.update(value=None, until=0.0)
        env = patch.dict(os.environ, {"SPOTIFY_CLIENT_ID": "id", "SPOTIFY_CLIENT_SECRET": "secret"}); env.start()
        self.addCleanup(env.stop)

    def test_names_read_like_the_web_search_and_the_asked_type_comes_first(self):
        api = FakeSpotifyApi(API_ANSWER)
        found = spotify.api_search("Ghost Stories av Coldplay", "album", urlopen=api)
        self.assertEqual([(f["name"], f["type"]) for f in found],
                         [("Ghost Stories med Coldplay", "album"), ("Flying Without Wings med Westlife", "track"),
                          ("Westlife", "artist")])      # the null item and the bad uri are skipped
        self.assertIn("q=Ghost+Stories+Coldplay", api.urls[1])      # "av" is not searched for
        self.assertEqual(spotify.confident("ghost stories av coldplay", found)["type"], "album")

    def test_a_bare_name_is_the_artist_not_a_song_by_it(self):
        found = spotify.api_search("Westlife", urlopen=FakeSpotifyApi(API_ANSWER))
        self.assertEqual(spotify.confident("westlife", found)["type"], "artist")

    def test_the_token_is_kept_and_renewed_once_after_401(self):
        import urllib.error
        expired = urllib.error.HTTPError("u", 401, "expired", {}, None)
        api = FakeSpotifyApi(API_ANSWER, expired, API_ANSWER)
        spotify.api_search("a", urlopen=api)
        spotify.api_search("b", urlopen=api)
        self.assertEqual(sum("accounts" in u for u in api.urls), 2)     # one token, then one new after the 401

    def test_a_failed_api_search_falls_back_to_the_web_search(self):
        import urllib.error
        searched = []
        with patch.object(spotify, "api_search", side_effect=spotify.SpotifyError("down")), \
                patch.object(spotify.lookup, "websearch", lambda q: searched.append(q) or HITS):
            found = spotify.search("westlife", "artist")
        self.assertEqual(searched, ["open.spotify.com artist westlife"])
        self.assertEqual(found[0]["type"], "artist")

    def test_spotifys_order_decides_measured_on_jarvis(self):
        def item(name, kind, n, by=()):
            return {"name": name, "uri": f"spotify:{kind}:{kind[0] * 21}{n}", "artists": [{"name": a} for a in by]}
        starboy = {"tracks": {"items": [item("Starboy", "track", 0, ["The Weeknd", "Daft Punk"])]},
                   "albums": {"items": [item("Starboy", "album", 0, ["The Weeknd"])]},
                   "artists": {"items": [item("The Weeknd", "artist", 0), item("StarBoy", "artist", 1)]}}
        found = spotify.api_search("starboy", urlopen=FakeSpotifyApi(starboy))
        self.assertEqual(spotify.confident("starboy", found)["name"], "Starboy med The Weeknd och Daft Punk")
        parachutes = {"tracks": {"items": [item("Yellow", "track", 0, ["Coldplay"]), item("Parachutes", "track", 1, ["Balance"])]},
                      "albums": {"items": [item("Parachutes", "album", 0, ["Coldplay"])]},
                      "artists": {"items": [item("Sleeping Parachuter", "artist", 0), item("Parachutes", "artist", 1)]}}
        found = spotify.api_search("parachutes", "album", urlopen=FakeSpotifyApi(parachutes))
        self.assertEqual(spotify.confident("parachutes", found, "album")["name"], "Parachutes med Coldplay")
        found = spotify.api_search("parachutes", urlopen=FakeSpotifyApi(parachutes))
        self.assertEqual(spotify.confident("parachutes", found)["type"], "album")      # rank 0 beats a rank-1 song

    def test_an_api_error_is_a_spotify_error(self):
        import urllib.error
        api = FakeSpotifyApi(urllib.error.HTTPError("u", 429, "slow down", {}, None))
        with self.assertRaises(spotify.SpotifyError):
            spotify.api_search("a", urlopen=api)
        with self.assertRaises(spotify.SpotifyError):
            spotify.api_search("a", urlopen=FakeSpotifyApi(OSError("no network")))


if __name__ == "__main__":
    unittest.main()
