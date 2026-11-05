"""Real HA media feature flags, scoped service calls and announcement separation. Silent/free."""
import importlib
import importlib.util
from pathlib import Path
import sys
import types
import asyncio
import io
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock, Mock, patch

HAS_HA = importlib.util.find_spec("homeassistant") is not None
if HAS_HA:
    package = types.ModuleType("jarvis_media_test")
    package.__path__ = [str(Path(__file__).resolve().parents[1]/"ha/custom_components/jarvis_agent")]
    sys.modules[package.__name__] = package
    media = importlib.import_module(package.__name__+".media")
    duck_module = importlib.import_module(package.__name__+".speaker_duck")
    artwork_module = importlib.import_module(package.__name__+".media_artwork")
    spotify_state = importlib.import_module(package.__name__+".spotify_state")
    output_select = importlib.import_module(package.__name__+".select")
    output_button = importlib.import_module(package.__name__+".button")
    from PIL import Image
    from homeassistant.core import HassJob, HassJobType
    from homeassistant.exceptions import HomeAssistantError, ServiceValidationError


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class OutputResultTest(unittest.IsolatedAsyncioTestCase):
    async def test_backend_refusals_are_service_failures_that_esphome_returns(self):
        runtime=NS(speaker=AsyncMock(side_effect=HomeAssistantError('Bluetooth timed out')),
                   speaker_reconnect=AsyncMock(side_effect=HomeAssistantError('Bluetooth timed out')))
        entry=NS(entry_id='test',runtime_data=runtime)
        choice=output_select.JarvisSpeakerOutput(entry)
        choice.async_write_ha_state=Mock()
        with self.assertRaisesRegex(ServiceValidationError,'Bluetooth timed out'):
            await choice.async_select_option('JBL Charge 4')
        choice.async_write_ha_state.assert_not_called()
        button=output_button.JarvisSpeakerReconnect(entry)
        with self.assertRaisesRegex(ServiceValidationError,'Bluetooth timed out'):
            await button.async_press()
        runtime.speaker_reconnect.side_effect=None
        runtime.speaker_reconnect.return_value={'error':'Speaker unavailable'}
        with self.assertRaisesRegex(ServiceValidationError,'Speaker unavailable'):
            await button.async_press()
        runtime.speaker.return_value={'choice':'JBL Charge 4','options':['Panelen','JBL Charge 4']}
        runtime.speaker.side_effect=None
        await choice.async_select_option('JBL Charge 4')
        self.assertEqual(choice.current_option,'JBL Charge 4')
        choice.async_write_ha_state.assert_called_once()


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class MediaTest(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.states = {}
        self.entity = media.JarvisMedia(NS(entry_id="test"))
        self.entity.hass = NS(states=NS(get=self.states.get, async_all=lambda domain:list(self.states.values())),
                              services=NS(async_call=AsyncMock()))
        self.native("idle")

    def native(self, state):
        self.states[media.PLAYER] = NS(entity_id=media.PLAYER, state=state, attributes={})
        self.entity.refresh()

    def start_radio(self):
        self.entity.called(NS(data={"domain":"media_player", "service":"play_media", "service_data": {
            "entity_id":media.PLAYER, "media_content_type":"music", "media_content_id":"https://example.test/P3.mp3",
            "extra":{"metadata":{"title":"P3", "artist":"Sveriges Radio", "source":"Radio"}}}}))
        self.native("playing")

    def test_request_alone_is_hidden_then_real_music_keeps_channel_and_pause_remains_visible(self):
        self.start_radio()
        self.assertEqual(self.entity.native_value, "playing")
        self.assertEqual(self.entity.extra_state_attributes["title"], "P3")
        self.assertEqual(self.entity.extra_state_attributes["source"], "P3")
        self.native("paused")
        self.assertTrue(self.entity.extra_state_attributes["wire"].startswith("paused|P3|P3|"))
        self.native("idle")
        self.assertEqual(self.entity.extra_state_attributes["title"], "")
        self.native("playing")
        self.assertEqual(self.entity.extra_state_attributes["source"], "Musik")  # old P3 metadata must not return

    def speaker(self, state, **meta):
        self.entity.speaker_event(NS(data={"state": state, **meta}))

    def test_radio_on_the_bluetooth_speaker_shows_while_the_panel_is_idle_and_survives_a_reply(self):
        self.speaker("playing", title="P3", artist="Sveriges Radio", source="Radio", image="", channel_id=164)
        attrs = self.entity.extra_state_attributes
        self.assertEqual((self.entity.native_value, attrs["source"], attrs["control_entity"]),
                         ("playing", "P3", media.SPEAKER))
        self.assertEqual(attrs["wire"], "playing|P3|P3|Sveriges Radio|0|0|1")    # 7th field: not the panel's
        self.native("announcing")                                                  # Jarvis answers on the panel
        self.native("idle")
        self.assertTrue(self.entity.extra_state_attributes["wire"].startswith("playing|P3|"))
        self.native("playing")                                                     # the panel's own music wins
        self.assertEqual(self.entity.extra_state_attributes["control_entity"], media.PLAYER)
        self.assertFalse(self.entity.extra_state_attributes["wire"].endswith("|1"))
        self.native("idle")
        self.speaker("idle")
        self.assertEqual(self.entity.extra_state_attributes["wire"], "idle||||0|0")

    def spotify(self, status, **extra):
        self.entity.spotify = NS(state={"status": status, "title": "My Love", "artist": "Westlife",
                                        "uri": "spotify:track:5p0ietGkLNEqx1Z7ijkw5g",
                                        "cover": "https://i.scdn.co/image/ab67616d00001e02aaaa", "volume": 60, **extra},
                                 command=AsyncMock())
        self.entity.refresh()

    def test_spotify_on_the_speaker_shows_with_its_name_place_and_skips(self):
        """Spotify through Soloist (2026-10-28): shown when neither the panel nor the radio plays."""
        self.states["select.jarvis_ljud_ut"] = NS(entity_id="select.jarvis_ljud_ut", state="JBL Charge 4", attributes={})
        self.spotify("playing")
        attrs = self.entity.extra_state_attributes
        self.assertEqual(attrs["wire"], "playing|Spotify|My Love|Westlife|1|1|JBL Charge 4")
        self.assertEqual((attrs["control_entity"], attrs["speaker_volume"]), (media.SPOTIFY, 60))
        self.spotify("paused")
        self.assertTrue(self.entity.extra_state_attributes["wire"].startswith("paused|Spotify|My Love|"))
        self.speaker("playing", title="P3", artist="Sveriges Radio", source="Radio")   # the radio wins
        self.assertEqual(self.entity.extra_state_attributes["control_entity"], media.SPEAKER)
        self.speaker("idle")
        self.spotify("")                                                               # nothing loaded
        self.assertEqual(self.entity.extra_state_attributes["wire"], "idle||||0|0")

    def test_the_next_spotify_cover_is_prepared_ahead_under_its_future_key(self):
        """2026-10-30: Soloist's queue names the next track; its cover is prepared before the track changes."""
        self.entity.artwork = NS(select=Mock(return_value="a"*32), prefetch=Mock(return_value="b"*32))
        self.spotify("playing")
        self.entity.spotify.upcoming = {"cover": "https://i.scdn.co/image/next", "uri": "spotify:track:n"}
        self.entity.refresh()
        self.entity.artwork.prefetch.assert_called_with(("cover", "https://i.scdn.co/image/next", "spotify:track:n"))
        self.assertEqual(self.entity.extra_state_attributes["artwork_next"], "b"*32)
        self.speaker("playing", title="P3", artist="Sveriges Radio", source="Radio")   # not Spotify: nothing ahead
        self.entity.artwork.prefetch.assert_called_with(None)

    def test_soloists_queue_names_the_next_track_and_a_new_track_asks_again(self):
        item = {"uri": "spotify:track:n", "decorations": {"visual_identity": {"cover": [
            {"url": "https://i.scdn.co/image/small", "size": "small"}, {"url": "https://i.scdn.co/image/def", "size": "default"}]}}}
        self.assertEqual(spotify_state.upcoming({"type": "queue_changed", "upcoming": [{"item": item}]}),
                         {"cover": "https://i.scdn.co/image/def", "uri": "spotify:track:n"})
        self.assertEqual(spotify_state.upcoming({"type": "queue_changed", "upcoming": []}), {})
        updated = Mock()
        watch = spotify_state.SoloistWatch(NS(), updated)
        watch.handle({"type": "queue_changed", "upcoming": [{"item": item}]})
        self.assertEqual(watch.upcoming["uri"], "spotify:track:n")
        updated.assert_called_once()
        watch.ws = NS(closed=False)
        watch.hass = NS(async_create_task=Mock(side_effect=lambda coro, name: coro.close()))
        watch.handle({"type": "playback_state", "status": "playing", "item": {"uri": "spotify:track:a"}})
        watch.hass.async_create_task.assert_called_once()                 # the next track changes with the track

    def test_a_prepared_cover_gives_its_key_at_once_and_the_same_key_ahead(self):
        artwork = artwork_module.MediaArtwork(NS(async_create_task=Mock()), Mock())
        request = ("cover", "https://i.scdn.co/image/x", "spotify:track:x")
        artwork._keep(request[1], b"prepared")
        self.assertEqual(artwork.prefetch(request), artwork.key_for(b"prepared", request))
        self.assertEqual(artwork.select(request), artwork.next_key)       # the key the panel fetched ahead
        artwork.hass.async_create_task.assert_not_called()                 # no fetch, no resize
        for url in ("https://i.scdn.co/image/1", "https://i.scdn.co/image/2", "https://i.scdn.co/image/3"):
            artwork._keep(url, b"x")
        self.assertEqual(len(artwork.covers), artwork_module.COVERS_KEPT)
        self.assertNotIn(request[1], artwork.covers)

    async def test_spotify_controls_go_to_soloist(self):
        self.spotify("playing")
        for action, command in (("toggle", "pause"), ("next", "skip_next"), ("previous", "skip_prev")):   # stop: below
            await self.entity.control(action)
            self.entity.spotify.command.assert_awaited_with(command)
        self.spotify("paused")
        await self.entity.control("toggle")
        self.entity.spotify.command.assert_awaited_with("play")
        self.entity.hass.services.async_call.assert_not_awaited()

    async def test_a_spotify_stop_hides_it_until_it_plays_again(self):
        """Joel, 2026-10-30: stop blanks the row. Spotify only pauses (it keeps its queue) but reports idle."""
        self.spotify("playing")
        await self.entity.control("stop")
        self.entity.spotify.command.assert_awaited_with("pause")
        self.assertEqual(self.entity.extra_state_attributes["wire"], "idle||||0|0")   # Soloist's last "playing"
        self.spotify("paused")
        self.assertEqual(self.entity.extra_state_attributes["wire"], "idle||||0|0")
        self.spotify("playing")                                         # resumed from the phone or by voice
        self.assertEqual(self.entity.extra_state_attributes["wire"].split("|")[:2], ["playing", "Spotify"])
        self.spotify("paused")                                          # a later pause shows as paused again
        self.assertEqual(self.entity.extra_state_attributes["wire"].split("|")[0], "paused")
        self.assertIsNone(media.spotify_stop(("pausing", 0), "playing", 11))  # a pause that never came
        self.assertEqual(media.spotify_stop(("pausing", 0), "playing", 5), ("pausing", 0))

    def test_soloist_state_is_read_with_creators_in_decorations(self):
        state = spotify_state.parse({"type": "playback_state", "status": "playing", "volume": 55, "item": {
            "uri": "spotify:track:x", "decorations": {"identity": {"name": "Relationship (feat. Future)"},
            "visual_identity": {"cover": [{"url": "https://i.scdn.co/image/small", "size": "small"},
                                          {"url": "https://i.scdn.co/image/default", "size": "default"}]},
            "creators": [{"entity": {"decorations": {"identity": {"name": "Young Thug"}}}},
                         {"entity": {"decorations": {"identity": {"name": "Future"}}}}]}}})
        self.assertEqual(state, {"status": "playing", "title": "Relationship (feat. Future)", "artist": "Young Thug, Future",
                                 "album": "", "album_uri": "", "position_ms": 0, "position_at_ms": 0,
                                 "duration_ms": 0, "uri": "spotify:track:x",
                                 "cover": "https://i.scdn.co/image/default", "volume": 55})
        # The album for the Musik page, from the track's parent (Soloist 1.3.10).
        album = spotify_state.parse({"type": "playback_state", "status": "playing", "item": {"uri": "spotify:track:z",
            "decorations": {"identity": {"name": "Oceans"}, "parent": {"entity": {
                "uri": "spotify:album:2G4AUqfwxcV1UdQjm2ouYr", "entity_type": "album",
                "decorations": {"identity": {"name": "Ghost Stories"}}}}}}})
        self.assertEqual((album["album"], album["album_uri"]), ("Ghost Stories", "spotify:album:2G4AUqfwxcV1UdQjm2ouYr"))
        # A single whose parent is not typed "album", played from its own page: the URI and the context decide.
        single = spotify_state.parse({"type": "playback_state", "status": "playing", "item": {"uri": "spotify:track:s",
            "decorations": {"identity": {"name": "TIT FOR TAT"}, "parent": {"entity": {
                "uri": "spotify:album:1ks1k7zln4TyBjn1CF3vCz", "entity_type": "single",
                "decorations": {"identity": {"name": "TIT FOR TAT"}}}}}}})
        self.assertEqual(single["album_uri"], "spotify:album:1ks1k7zln4TyBjn1CF3vCz")
        from_context = spotify_state.parse({"type": "playback_state", "status": "playing", "item": {"uri": "spotify:track:c",
            "decorations": {"identity": {"name": "Song"}}}, "context": {"uri": "spotify:album:2G4AUqfwxcV1UdQjm2ouYr",
            "entity_type": "album", "decorations": {"identity": {"name": "Ghost Stories"}}}})
        self.assertEqual((from_context["album"], from_context["album_uri"]), ("Ghost Stories", "spotify:album:2G4AUqfwxcV1UdQjm2ouYr"))
        self.assertEqual(spotify_state.parse({"type": "playback_state", "status": "stopped"}), {})
        # Playing on another device of the account (the iPhone): not Jarvis's music.
        self.assertEqual(spotify_state.parse({"type": "playback_state", "status": "playing", "is_active": False,
                                              "item": {"uri": "spotify:track:x", "decorations": {}}}), {})
        # A track change reports buffering for about a second: still playing, so Spelas nu stays (2026-10-28).
        self.assertEqual(spotify_state.parse({"type": "playback_state", "status": "buffering",
                                              "item": {"uri": "spotify:track:y"}})["status"], "playing")

    async def test_45_lasts_one_song_and_only_spotify_playing_can_be_45(self):
        self.entity.hass.async_create_task = lambda coro, name=None: coro.close()
        self.entity._entry.runtime_data = NS(speed=AsyncMock(return_value={"rpm": 45}))
        self.entity.spotify = NS(state={"status": "playing", "uri": "spotify:track:a", "title": "A", "artist": "B",
                                        "cover": "", "volume": 50})
        self.entity.refresh()
        self.assertEqual(await self.entity.speed(), 45)
        self.assertEqual(self.entity._attr_extra_state_attributes["rpm"], 45)
        self.entity.spotify.state = {**self.entity.spotify.state, "uri": "spotify:track:next"}
        self.entity.refresh()                                  # the next song: back to 33
        self.assertEqual(self.entity._attr_extra_state_attributes["rpm"], 33)
        self.entity.spotify.state = {**self.entity.spotify.state, "status": "paused"}
        self.entity.refresh()
        self.assertEqual(await self.entity.speed(45), 33)      # paused: no 45

    def test_the_time_line_is_soloists_position_moved_on_to_now(self):
        spot = {"status": "playing", "duration_ms": 300000, "position_ms": 60000, "position_at_ms": 1_000_000}
        self.assertEqual(media.spotify_progress(spot, 1_002.5), "62500|300000|1")
        self.assertEqual(media.spotify_progress({**spot, "status": "paused"}, 1_002.5), "60000|300000|0")
        self.assertEqual(media.spotify_progress({**spot, "duration_ms": 0}, 1_002.5), "")

    async def test_a_short_gap_in_soloists_state_does_not_hide_spelas_nu(self):
        loop = asyncio.get_running_loop()
        shown = []
        watch = spotify_state.SoloistWatch(NS(loop=loop), lambda: shown.append(dict(watch.state)))
        playing = {"type": "playback_state", "status": "playing", "item": {"uri": "spotify:track:a"}}
        watch.handle(playing)
        watch.handle({"type": "playback_state", "status": "stopped"})       # a gap
        self.assertEqual(watch.state["status"], "playing")
        watch.handle({**playing, "item": {"uri": "spotify:track:b"}})      # the next song within HOLD_S
        self.assertEqual((watch.state["uri"], watch.hold), ("spotify:track:b", None))
        with patch.object(spotify_state, "HOLD_S", 0.01):
            watch.handle({"type": "playback_state", "status": "stopped"})   # really stopped
            await asyncio.sleep(0.05)
        self.assertEqual(watch.state, {})
        watch.handle({**playing, "status": "paused"})                       # a pause shows at once
        self.assertEqual(watch.state["status"], "paused")
        self.assertEqual(artwork_module.spotify_image("https://i.scdn.co/image/abc"), "https://i.scdn.co/image/abc")
        for bad in ("http://i.scdn.co/image/abc", "https://evil.example/image/abc", "https://i.scdn.co/other"):
            self.assertEqual(artwork_module.spotify_image(bad), "")

    def test_the_streams_own_title_names_the_song_on_the_speaker(self):
        """SR's API runs on broadcast time and is polled; the ICY title changes with what is heard."""
        title = media.SpeakerTitle(NS(), lambda: None)
        title.follow = Mock()
        self.entity.speaker_title = title
        self.speaker("playing", title="P3", artist="Sveriges Radio", source="Radio", channel_id=164)
        title.title = "P3 med Juan Havana, spelar nu Swedish House Mafia & Lykke Li - Happiness Is So Sad"
        self.entity.refresh()
        attrs = self.entity.extra_state_attributes
        self.assertEqual((attrs["title"], attrs["artist"], attrs["source"]),
                         ("Happiness Is So Sad", "Swedish House Mafia & Lykke Li", "P3"))
        title.follow.assert_called_with(True)
        title.title = "P3 med Juan Havana"                                       # between songs: the programme
        self.entity.refresh()
        self.assertEqual((self.entity.extra_state_attributes["title"], self.entity.extra_state_attributes["artist"]),
                         ("P3 med Juan Havana", ""))
        self.speaker("idle")
        title.follow.assert_called_with(False)
        self.assertEqual(media.parse("Ekot, spelar nu Okänd låt"), ("Ekot", "", "Okänd låt"))
        self.assertEqual(media.parse(""), ("", "", ""))

    async def test_radio_already_on_the_speaker_shows_again_after_a_restart(self):
        mpd = AsyncMock(return_value=[{"state": "play"}, {"file": "https://www.sverigesradio.se/topsy/direkt/srapi/164.mp3"}])
        sr = AsyncMock(return_value={"name": "P3", "image": "https://static-cdn.sr.se/images/164/p3.jpg"})
        with patch.object(media, "mpd", mpd), patch.object(media, "sr_channel", sr):
            await self.entity.recover_speaker()
        sr.assert_awaited_once()
        self.assertEqual(self.entity.extra_state_attributes["wire"], "playing|P3|P3|Sveriges Radio|0|0|1")
        # During HA's startup MPD did not answer in time at first: it asks again.
        self.speaker("idle")
        slow = AsyncMock(side_effect=[TimeoutError(), [{"state": "play"}, {"file": "https://sverigesradio.se/x/164.mp3"}]])
        with patch.object(media, "mpd", slow), patch.object(media, "sr_channel", sr):
            await self.entity.recover_speaker(wait_s=0)
        self.assertTrue(self.entity.extra_state_attributes["wire"].startswith("playing|P3|"))
        other = AsyncMock(return_value=[{"state": "play"}, {"file": "https://example.test/164.mp3"}])
        self.speaker("idle")
        with patch.object(media, "mpd", other), patch.object(media, "sr_channel", sr):
            await self.entity.recover_speaker()                                  # not SR's stream: nothing to name
        self.assertEqual(self.entity.native_value, "idle")

    def test_the_panel_hears_where_it_plays_its_volume_and_a_pause(self):
        self.speaker("playing", title="P3", artist="Sveriges Radio", source="Radio", place="JBL Charge 4")
        self.assertEqual(self.entity.extra_state_attributes["wire"], "playing|P3|P3|Sveriges Radio|0|0|JBL Charge 4")
        self.assertEqual(self.entity.extra_state_attributes["speaker_volume"], -1)          # not read yet
        self.entity.speaker_title = NS(title="", volume=55, follow=Mock())
        self.entity._entry = NS(runtime_data=NS(speaker_duck=NS(saved=None)))
        self.entity.refresh()
        self.assertEqual(self.entity.extra_state_attributes["speaker_volume"], 55)
        self.entity._entry.runtime_data.speaker_duck.saved = 75                            # down while Jarvis listens
        self.entity.refresh()
        self.assertEqual(self.entity.extra_state_attributes["speaker_volume"], 75)
        self.speaker("paused")
        self.assertTrue(self.entity.extra_state_attributes["wire"].startswith("paused|P3|P3|"))
        self.speaker("playing")                                                            # a resume keeps the channel
        self.assertTrue(self.entity.extra_state_attributes["wire"].endswith("|JBL Charge 4"))
        self.native("playing")                                                             # the panel's own music
        self.assertEqual(self.entity.extra_state_attributes["speaker_volume"], -1)

    async def test_speaker_controls_go_straight_to_mpd(self):
        self.speaker("playing", title="P1", source="Radio")
        with patch.object(media, "mpd", AsyncMock()) as mpd:
            await self.entity.control("toggle")
            self.assertTrue(self.entity.extra_state_attributes["wire"].startswith("paused|"))
            await self.entity.control("toggle")
            with self.assertRaises(media.HomeAssistantError):
                await self.entity.control("next")
            await self.entity.control("stop")
        self.assertEqual([c.args[0] for c in mpd.await_args_list], ["pause 1", "pause 0", "stop"])
        self.entity.hass.services.async_call.assert_not_awaited()
        self.assertEqual(self.entity.extra_state_attributes["wire"], "idle||||0|0")

    def test_ha_noticing_a_stop_on_the_speaker_hides_it_but_a_stale_poll_does_not(self):
        self.speaker("playing", title="P3", source="Radio")
        off = NS(state="off", attributes={})
        self.entity.changed(NS(data={"entity_id": media.SPEAKER, "old_state": off, "new_state": off}))
        self.assertEqual(self.entity.native_value, "playing")                     # HA has not polled yet
        playing = NS(state="playing", attributes={})
        self.entity.changed(NS(data={"entity_id": media.SPEAKER, "old_state": playing, "new_state": off}))
        self.assertEqual(self.entity.native_value, "idle")

    def test_pending_play_metadata_does_not_show_a_music_session_before_playback(self):
        self.entity.called(NS(data={"domain":"media_player", "service":"play_media", "service_data": {
            "entity_id":[media.PLAYER], "media_content_type":"music", "media_content_id":"https://radio.test/live",
            "extra":{"metadata":{"title":"Pending channel"}}}}))
        self.assertEqual(self.entity.native_value, "idle")
        self.native("playing")
        self.assertEqual(self.entity.extra_state_attributes["title"], "Pending channel")

    def test_voice_and_wakeup_announcements_do_not_create_music_or_overwrite_underlying_radio(self):
        self.native("announcing")
        self.assertEqual(self.entity.native_value, "idle")
        self.start_radio()
        self.entity.called(NS(data={"domain":"media_player", "service":"play_media", "service_data": {
            "entity_id":media.PLAYER, "media_content_type":"music", "announce":True,
            "extra":{"metadata":{"title":"God morgon"}}}}))
        self.native("announcing")
        self.assertEqual(self.entity.extra_state_attributes["title"], "P3")
        self.assertEqual(self.entity.native_value, "playing")

    def test_panel_music_state_hides_announcements_that_ha_reports_as_playing(self):
        # 2026-10-26: HA showed the boot chime as "playing", so "Spelas nu" appeared at every boot.
        self.states[media.MUSIC_STATES[0]] = NS(entity_id=media.MUSIC_STATES[0], state="idle", attributes={})
        self.native("playing")
        self.assertEqual(self.entity.native_value, "idle")
        self.assertTrue(self.entity.extra_state_attributes["wire"].startswith("idle|"))
        self.start_radio()                                   # real music: the panel says so
        self.states[media.MUSIC_STATES[0]] = NS(entity_id=media.MUSIC_STATES[0], state="playing", attributes={})
        self.entity.refresh()
        self.assertEqual(self.entity.native_value, "playing")
        self.assertEqual(self.entity.extra_state_attributes["title"], "P3")
        self.states[media.MUSIC_STATES[0]] = NS(entity_id=media.MUSIC_STATES[0], state="unavailable", attributes={})
        self.native("playing")                               # sensor gone: old behaviour
        self.assertEqual(self.entity.native_value, "playing")

    async def test_toggle_uses_explicit_pause_then_resume_and_stop_only_targets_music(self):
        self.start_radio()
        await self.entity.control("toggle")
        self.entity.hass.services.async_call.assert_awaited_with("media_player", "media_pause", {"entity_id":media.PLAYER}, blocking=True)
        self.native("paused")
        await self.entity.control("toggle")
        self.entity.hass.services.async_call.assert_awaited_with("media_player", "media_play", {"entity_id":media.PLAYER}, blocking=True)
        self.native("announcing")
        await self.entity.control("stop")
        self.entity.hass.services.async_call.assert_awaited_with("media_player", "media_stop", {"entity_id":media.PLAYER}, blocking=True)
        self.entity.refresh()
        self.assertEqual(self.entity.native_value, "idle")

    async def test_music_assistant_metadata_and_queue_controls_match_only_this_physical_player(self):
        self.start_radio()
        logical = "media_player.hallpanel_music_assistant"
        self.states[logical] = NS(entity_id=logical, state="playing", attributes={"active_queue":media.PLAYER,
            "media_title":"Song | title", "media_artist":"Artist", "media_content_id":"spotify://track/abc",
            "supported_features":int(media.Feature.NEXT_TRACK | media.Feature.PREVIOUS_TRACK)})
        self.entity.refresh()
        self.assertEqual(self.entity.extra_state_attributes["source"], "Spotify")
        self.assertEqual(self.entity.extra_state_attributes["title"], "Song · title")
        await self.entity.control("next")
        self.entity.hass.services.async_call.assert_awaited_with("media_player", "media_next_track", {"entity_id":logical}, blocking=True)
        await self.entity.control("stop")
        self.entity.hass.services.async_call.assert_awaited_with("media_player", "media_stop", {"entity_id":logical}, blocking=True)
        self.native("idle")  # local emergency stop can reach HA before its queue-level stop request
        await self.entity.control("stop")
        self.entity.hass.services.async_call.assert_awaited_with("media_player", "media_stop", {"entity_id":logical}, blocking=True)

    async def test_unrelated_players_do_not_take_control_and_radio_cannot_skip(self):
        self.start_radio()
        self.states["media_player.other"] = NS(entity_id="media_player.other", state="playing",
            attributes={"active_queue":"other", "supported_features":int(media.Feature.NEXT_TRACK), "media_title":"Other room"})
        self.entity.refresh()
        self.assertEqual(self.entity.extra_state_attributes["title"], "P3")
        with self.assertRaises(media.HomeAssistantError):
            await self.entity.control("next")
        self.entity.hass.services.async_call.assert_not_awaited()

    async def test_service_failure_does_not_report_success_or_clear_the_music(self):
        self.start_radio()
        self.entity.hass.services.async_call.side_effect = media.HomeAssistantError("offline")
        with self.assertRaises(media.HomeAssistantError):
            await self.entity.control("stop")
        self.assertEqual(self.entity.native_value, "playing")

    def test_missing_player_is_hidden_and_ha_callbacks_run_on_loop(self):
        self.start_radio(); self.native("unavailable")
        self.assertEqual(self.entity.native_value, "idle")
        self.assertEqual(HassJob(self.entity.called).job_type, HassJobType.Callback)
        self.assertEqual(HassJob(self.entity.changed).job_type, HassJobType.Callback)

    def test_radio_and_logical_player_artwork_follow_only_the_active_music(self):
        self.entity.artwork = NS(select=Mock(return_value="a"*32), prefetch=Mock(return_value=""))
        self.entity.called(NS(data={"domain":"media_player", "service":"play_media", "service_data": {
            "entity_id":media.PLAYER, "media_content_type":"music", "extra":{"metadata":{
                "title":"P3", "source":"Radio", "image":"https://static-cdn.sr.se/images/p3.jpg"}}}}))
        self.entity.artwork.select.assert_called_with(None)
        self.native("playing")
        self.entity.artwork.select.assert_called_with(("radio","https://static-cdn.sr.se/images/p3.jpg","panel"))
        # A new song on the same station keeps the logo's identity, so the panel keeps the logo it has.
        self.entity.radio = NS(select=Mock(), read=Mock(return_value={"title":"Happiness Is So Sad", "artist":"Lykke Li"}))
        self.entity.refresh()
        self.entity.artwork.select.assert_called_with(("radio","https://static-cdn.sr.se/images/p3.jpg","panel"))
        self.entity.radio = None
        logical="media_player.panel_queue"
        self.states[logical]=NS(entity_id=logical,state="playing",attributes={"active_queue":media.PLAYER,
            "entity_picture":"/api/media_player_proxy/panel_queue?token=private", "media_content_id":"spotify:track:1"})
        self.entity.refresh()
        request=self.entity.artwork.select.call_args.args[0]
        self.assertEqual(request[0:2],("player",logical))
        self.assertNotIn("private",self.entity.extra_state_attributes['artwork'])
        self.native("idle");self.entity.artwork.select.assert_called_with(None)

    def test_live_radio_text_keeps_station_and_stops_monitor_on_idle(self):
        self.entity.radio=NS(select=Mock(),read=Mock(return_value={
            'title':'Bara Mellan Oss','artist':'Oskar Linnros','programme':'P3 med'}))
        self.start_radio()
        attrs=self.entity.extra_state_attributes
        self.assertEqual((attrs['title'],attrs['artist'],attrs['source']),('Bara Mellan Oss','Oskar Linnros','P3'))
        self.assertEqual(attrs['radio_programme'],'P3 med')
        self.native('idle');self.entity.radio.select.assert_called_with(None,False)


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class ArtworkTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        b=io.BytesIO();Image.new("RGB",(200,100),"#00aa66").save(b,format="PNG");self.png=b.getvalue()
        self.player=NS(async_get_media_image=AsyncMock(return_value=(self.png,"image/png")))
        self.players={"media_player.panel_queue":self.player}
        self.hass=NS(data={artwork_module.DATA_INSTANCES:{"media_player":NS(get_entity=self.players.get)}},
            async_create_task=lambda coro,name:asyncio.create_task(coro,name=name),
            async_add_executor_job=AsyncMock(side_effect=lambda fn,*args:fn(*args)))
        self.updated=Mock();self.art=artwork_module.MediaArtwork(self.hass,self.updated)
        self.addCleanup(self.art.close)

    def test_encoder_is_bounded_square_qoi_and_radio_urls_are_restricted(self):
        data=artwork_module.prepare_image(self.png)
        with Image.open(io.BytesIO(data)) as image:
            self.assertEqual(image.format,"QOI");self.assertEqual(image.size,(288,288))
        for bad in (b"not an image", b"x"*(artwork_module.MAX_BYTES+1)):
            with self.assertRaises((OSError,ValueError)):artwork_module.prepare_image(bad)
        for url in ("http://static-cdn.sr.se/image", "https://static-cdn.sr.se.evil.test/image",
                    "https://user@static-cdn.sr.se/image", "http://127.0.0.1/private", "file:///secret"):
            self.assertEqual(artwork_module.radio_image(url),"")
        self.assertEqual(artwork_module.radio_image("https://static-cdn.sr.se/images/1.jpg"),
                         "https://static-cdn.sr.se/images/1.jpg")

    async def test_same_cover_is_cached_and_stop_invalidates_public_route(self):
        request=("player","media_player.panel_queue","picture","track")
        self.assertEqual(self.art.select(request),"");await self.art.task
        key=self.art.key;self.assertEqual(len(key),32)
        self.assertEqual(self.art.select(request),key);self.player.async_get_media_image.assert_awaited_once()
        self.hass.config_entries=NS(async_entries=lambda domain:[NS(runtime_data=NS(media=NS(artwork=self.art)))])
        view=artwork_module.JarvisMediaArtworkView(self.hass)
        response=await view.get(None,key);self.assertEqual(response.content_type,"image/qoi")
        self.assertEqual(response.body,self.art.data)
        self.art.select(None)
        with self.assertRaises(artwork_module.web.HTTPNotFound):await view.get(None,key)
        with self.assertRaises(artwork_module.web.HTTPNotFound):await view.get(None,"../../secret")
        self.player.async_get_media_image.assert_awaited_once()  # reads never trigger fetching

    async def test_stale_slow_cover_cannot_replace_new_track(self):
        started=asyncio.Event();release=asyncio.Event()
        async def slow():
            started.set()
            try:await release.wait()
            except asyncio.CancelledError:await release.wait()  # an upstream call that finishes despite cancellation
            return self.png,"image/png"
        self.players["media_player.slow"]=NS(async_get_media_image=slow)
        self.art.select(("player","media_player.slow","old","track1"));old=self.art.task
        await started.wait()
        self.art.select(("player","media_player.panel_queue","new","track2"));await self.art.task
        key=self.art.key;self.updated.assert_called_once()
        release.set();await old
        self.assertEqual(self.art.key,key);self.updated.assert_called_once()

    async def test_image_failure_keeps_text_playback_and_does_not_retry_forever(self):
        self.player.async_get_media_image.return_value=(b"invalid image","image/png")
        request=("player","media_player.panel_queue","picture","track")
        self.art.select(request);await self.art.task
        self.assertEqual(self.art.key,"");self.assertIsNone(self.art.data);self.updated.assert_not_called()
        self.art.select(request);self.player.async_get_media_image.assert_awaited_once()

    async def test_two_songs_sharing_album_cover_have_distinct_keys_for_ha_attribute_updates(self):
        self.art.select(("player","media_player.panel_queue","same cover","track1"));await self.art.task
        old=self.art.key
        self.art.select(("player","media_player.panel_queue","same cover","track2"));await self.art.task
        self.assertNotEqual(self.art.key,old)


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class SpeakerDuckTest(unittest.IsolatedAsyncioTestCase):
    """Music on the Bluetooth speaker drowned Joel's question (2026-10-27): it goes down to a tenth and back."""
    async def asyncSetUp(self):
        self.mpd = {"state": "play", "volume": 40}
        self.got = []

        async def serve(reader, writer):
            writer.write(b"OK MPD 0.24.0\n")
            while line := (await reader.readline()).decode().strip():
                self.got.append(line)
                if line == "status":
                    writer.write(f"volume: {self.mpd['volume']}\nstate: {self.mpd['state']}\n".encode())
                elif line.startswith("setvol "):
                    self.mpd["volume"] = int(line.split()[1])
                writer.write(b"OK\n")
                await writer.drain()
            writer.close()
        self.server = await asyncio.start_server(serve, "127.0.0.1", 0)
        self.duck = duck_module.SpeakerDuck(NS(), address=("127.0.0.1", self.server.sockets[0].getsockname()[1]))

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def test_down_while_the_panel_listens_softer_while_jarvis_answers_and_back_after(self):
        await self.duck.apply(10)                       # listening
        self.assertEqual(self.mpd["volume"], 4)
        await self.duck.apply(10)                       # processing: still a tenth of the original
        await self.duck.apply(4)                        # responding: the radio is heard under the reply
        self.assertEqual(self.mpd["volume"], 10)
        await self.duck.apply(None)
        self.assertEqual(self.mpd["volume"], 40)
        self.assertEqual([c for c in self.got if c.startswith("setvol")],
                         ["setvol 4", "setvol 4", "setvol 10", "setvol 40"])

    async def test_stopped_music_and_a_missing_mpd_are_left_alone(self):
        self.mpd["state"] = "stop"
        await self.duck.apply(10)
        await self.duck.apply(None)
        self.assertNotIn("setvol", " ".join(self.got))
        missing = duck_module.SpeakerDuck(NS(), address=("127.0.0.1", 1))
        await missing.apply(10)                       # no speaker set up: no error
        self.assertIsNone(missing.saved)

    async def test_the_panels_states_decide(self):
        self.duck.hass = NS(async_create_task=Mock())
        self.duck.apply = Mock(return_value=None)
        # A sixth while listening and 40 % while answering since 2026-10-28 (Joel wanted a little more music).
        for state, share in (("listening", 6), ("processing", 6), ("responding", 2.5), ("idle", None),
                             ("unavailable", None)):
            self.duck.changed(NS(data={"new_state": NS(state=state)}))
            self.duck.apply.assert_called_with(share)
        self.duck.changed(NS(data={"new_state": None}))
        self.duck.apply.assert_called_with(None)


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class SoloistDuckTest(unittest.IsolatedAsyncioTestCase):
    """Spotify through Soloist on the same speaker goes down and back like the radio (2026-10-28)."""
    async def asyncSetUp(self):
        try:
            from aiohttp import web
        except ImportError:
            self.skipTest("aiohttp unavailable")
        self.state = {"type": "playback_state", "status": "playing", "volume": 60}
        self.sent = []

        async def handler(request):
            ws = web.WebSocketResponse()
            await ws.prepare(request)
            await ws.send_json({"type": "auth_state", "logged_in": True})       # first on every connect
            await ws.send_json(self.state)
            async for message in ws:
                command = message.json()
                self.sent.append(command)
                if command.get("command") == "set_volume":
                    self.state["volume"] = command["volume"]
                    await ws.send_json({"type": "volume_changed", "volume": command["volume"]})
                    await ws.send_json({"type": "command_result", "command": "set_volume"})
            return ws
        app = web.Application()
        app.router.add_get("/", handler)
        self.runner = web.AppRunner(app)
        await self.runner.setup()
        site = web.TCPSite(self.runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.duck = duck_module.SoloistDuck(NS(), url=f"ws://127.0.0.1:{port}/")

    async def asyncTearDown(self):
        await self.runner.cleanup()

    async def test_spotify_milder_than_the_radio(self):
        # Joel, 2026-11-01: Spotify fairly loud while he talks (two thirds) and full while Jarvis answers.
        self.duck.hass = NS(async_create_task=Mock())
        self.duck.apply = Mock(return_value=None)
        for state, share in (("listening", 1.5), ("processing", 1.5), ("responding", None), ("idle", None)):
            self.duck.changed(NS(data={"new_state": NS(state=state)}))
            self.duck.apply.assert_called_with(share)
        await duck_module.SoloistDuck.apply(self.duck, 1.5)
        self.assertEqual(self.state["volume"], 40)
        await duck_module.SoloistDuck.apply(self.duck, None)
        self.assertEqual(self.state["volume"], 60)

    async def test_spotify_down_while_listening_softer_while_answering_and_back(self):
        await self.duck.apply(10)
        self.assertEqual(self.state["volume"], 6)
        await self.duck.apply(4)
        self.assertEqual(self.state["volume"], 15)
        await self.duck.apply(None)
        self.assertEqual(self.state["volume"], 60)
        self.assertEqual(self.sent, [{"type": "command", "command": "set_volume", "volume": v} for v in (6, 15, 60)])

    async def test_spotify_started_during_the_turn_goes_down_and_back(self):
        # "Spela Kent på Spotify": paused when the panel starts listening, playing once the direct play has run
        self.duck.WATCH_EVERY_S = 0.02
        self.state["status"] = "paused"
        await self.duck.apply(6)
        self.assertEqual(self.sent, [])
        self.state["status"] = "playing"
        await self.duck.apply(2.5)                      # Jarvis answers; the watch is still running
        for _ in range(100):
            if self.sent:
                break
            await asyncio.sleep(0.01)
        self.assertEqual(self.state["volume"], 24)      # 60 / 2.5, under "Spelar Kent"
        await self.duck.apply(None)
        self.assertEqual(self.state["volume"], 60)

    async def test_paused_spotify_and_a_missing_soloist_are_left_alone(self):
        self.state["status"] = "paused"
        await self.duck.apply(10)
        await self.duck.apply(None)
        self.assertEqual(self.sent, [])
        # Joel's iPhone plays: Soloist says "playing" with is_active false, and its volume is not Jarvis's to lower
        self.state.update(status="playing", is_active=False)
        await self.duck.apply(10)
        await self.duck.apply(None)
        self.assertEqual(self.sent, [])
        missing = duck_module.SoloistDuck(NS(), url="ws://127.0.0.1:1/")
        await missing.apply(10)
        self.assertIsNone(missing.saved)


@unittest.skipUnless(HAS_HA, "run inside Home Assistant")
class SpeakerTitleTest(unittest.IsolatedAsyncioTestCase):
    """The stream title follows a new song even if MPD's idle never reports it (not verified that it does)."""
    async def test_a_new_title_arrives_without_an_idle_event(self):
        titles = ["P3, spelar nu A - One"]

        async def serve(reader, writer):
            writer.write(b"OK MPD 0.24.0\n")
            while line := (await reader.readline()).decode().strip():
                if line == "currentsong":
                    writer.write(f"Title: {titles[0]}\nOK\n".encode())
                elif line == "status":
                    writer.write(b"volume: 40\nOK\n")
                elif line == "noidle":
                    writer.write(b"OK\n")
                # "idle" gets no answer: MPD that never reports title changes
                await writer.drain()
            writer.close()
        server = await asyncio.start_server(serve, "127.0.0.1", 0)
        seen = []
        hass = NS(async_create_background_task=lambda coro, name: asyncio.ensure_future(coro))
        title = media.SpeakerTitle(hass, lambda: seen.append(title.title),
                                   address=("127.0.0.1", server.sockets[0].getsockname()[1]))
        with patch.object(sys.modules[media.SpeakerTitle.__module__], "CHECK_S", 0.2):
            title.follow(True)
            await asyncio.sleep(0.1)
            titles[0] = "P3, spelar nu B - Two"
            await asyncio.sleep(0.6)
            title.close()
        server.close()
        await server.wait_closed()
        self.assertEqual(seen, ["P3, spelar nu A - One", "P3, spelar nu B - Two"])
        self.assertEqual(title.volume, 40)
        self.assertIsNone(title.task)
