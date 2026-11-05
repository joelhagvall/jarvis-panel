"""Event-driven music status and scoped controls. Announcements never create a music session."""
from urllib.parse import urlsplit
import asyncio
import logging
import re
import time

from homeassistant.components.media_player import MediaPlayerEntityFeature as Feature
from homeassistant.components.sensor import SensorEntity
from homeassistant.const import EVENT_CALL_SERVICE, EVENT_STATE_CHANGED
from homeassistant.core import callback
from homeassistant.exceptions import HomeAssistantError

from .entity import jarvis_device
from .glyphs import fold
from .media_artwork import MediaArtwork, radio_image, spotify_image
from .radio_metadata import RadioMetadata, channel as sr_channel
from .speaker_duck import mpd
from .speaker_title import SpeakerTitle, parse
from .album_year import AlbumYear
from .spotify_state import SoloistWatch


PLAYER = "media_player.hallpanel_hallpanel"
# The panel's own music pipeline state (styles/glass-v2/now_playing.py). HA reports the player's announcements
# (boot chime, Jarvis's replies) as "playing"; this sensor never does. Older firmware without it falls back.
# HA named it from the device ("Hallway hallpanel"), checked 2026-10-26; the short form is a fallback.
MUSIC_STATES = ("sensor.hallway_hallpanel_music_state", "sensor.hallpanel_music_state")
ACTIVE = {"playing", "paused"}
# Jarvis's Bluetooth speaker (docs/bluetooth-speaker.md): MPD on Jarvis, shown when the panel itself plays nothing.
# server/agents/radio.py fires SPEAKER_EVENT with the channel, since HA's MPD player knows no name or logo and
# polls slowly; its controls go straight to MPD, since HA's MPD player took 12.3 s to answer a play_media.
SPEAKER = "media_player.bluetooth_hogtalare"
SPEAKER_EVENT = "jarvis_agent_speaker"
# Spotify through Soloist on the same speaker (docs/spotify.md): shown when neither the panel nor the radio plays.
SPOTIFY = "spotify"
_LOGGER = logging.getLogger(__name__)
SPOTIFY_COMMANDS = {"next": "skip_next", "previous": "skip_prev", "stop": "pause"}


def spotify_progress(spot, now_s):
    """ "position ms|duration ms|1 while playing" for the panel's time line: Soloist's position moved on to now."""
    duration = int(spot.get("duration_ms") or 0)
    if duration <= 0:
        return ""
    position = int(spot.get("position_ms") or 0)
    playing = spot.get("status") == "playing"
    at = int(spot.get("position_at_ms") or 0)
    if playing and at:
        position += max(0, int(now_s * 1000) - at)
    return f"{min(max(position, 0), duration)}|{duration}|{int(playing)}"


def spotify_stop(stopped, status, now_s, wait_s=10):
    """A panel stop hides Spotify until it plays again: ("pausing", t) until Soloist says it is no longer playing,
    then ("paused", t) until it plays. A pause that never comes within wait_s shows the music again."""
    if not stopped:
        return None
    phase, since = stopped
    if phase == "pausing":
        if status != "playing":
            return ("paused", now_s)
        return None if now_s - since > wait_s else stopped
    return None if status == "playing" else stopped


def text(value, length=120):
    return fold(" ".join(str(value or "").replace("|", "·").split())[:length])


class JarvisMedia(SensorEntity):
    _attr_name = "Jarvis uppspelning"
    _attr_icon = "mdi:music"
    _attr_should_poll = False
    _unrecorded_attributes = frozenset({"wire", "title", "artist", "source", "album", "progress", "rpm", "control_entity", "next", "previous", "artwork", "artwork_next",
                                       "radio_channel", "radio_programme", "radio_description", "speaker_volume"})

    def __init__(self, entry):
        self._entry = entry
        self._attr_unique_id = entry.entry_id + "-media"
        self._attr_device_info = jarvis_device(entry)
        self._music_state = "idle"
        self._metadata = {}
        self._pending_metadata = None
        self._target = PLAYER
        self._speaker_state = "idle"
        self._speaker_meta = {}
        self._publish = False
        self.artwork = None
        self.radio = None
        self.speaker_title = None
        self.spotify = None
        # Stop on the panel pauses Spotify (it keeps its queue) but hides it, here and on the panel, until it plays
        # again (Joel, 2026-10-30: stop blanks the row, on Hem too). None, or ("pausing", since) until Soloist
        # reports the pause (its last "playing" may still come after the command), then ("paused", since).
        self._spotify_stopped = None
        self._rpm, self._rpm_song = 33, None   # the turntable's speed; 45 only for the song it was set on
        self._attr_native_value = "idle"
        self._attr_extra_state_attributes = {"wire": "idle||||0|0"}

    async def async_added_to_hass(self):
        self._publish = True
        self.artwork = MediaArtwork(self.hass, self.refresh)
        self.async_on_remove(self.artwork.close)
        self.radio = RadioMetadata(self.hass, self.refresh)
        self.async_on_remove(self.radio.close)
        self.speaker_title = SpeakerTitle(self.hass, self.refresh)
        self.async_on_remove(self.speaker_title.close)
        self.spotify = SoloistWatch(self.hass, self.refresh)
        self.album_year = AlbumYear(self.hass, self.refresh)
        self.spotify.start()
        self.async_on_remove(self.spotify.close)
        self.async_on_remove(self.hass.bus.async_listen(EVENT_STATE_CHANGED, self.changed))
        self.async_on_remove(self.hass.bus.async_listen(EVENT_CALL_SERVICE, self.called))
        self.async_on_remove(self.hass.bus.async_listen(SPEAKER_EVENT, self.speaker_event))
        self.refresh()
        recovery = self.hass.async_create_background_task(self.recover_speaker(), "Jarvis speaker after restart")
        self.async_on_remove(recovery.cancel)

    async def recover_speaker(self, attempts=6, wait_s=10):
        """After Home Assistant restarts, radio already on the speaker shows again: the event that named it is gone,
        but MPD still plays SR's stream, whose URL holds the channel id (seen missing 2026-10-27). During HA's
        startup the first MPD question timed out while a later reload found it at once, so it asks a few times."""
        for attempt in range(attempts):
            try:
                status, song = await mpd("status", "currentsong")
                break
            except (OSError, TimeoutError):
                if attempt + 1 == attempts or self._speaker_meta:
                    return
                await asyncio.sleep(wait_s)
        state = {"play": "playing", "pause": "paused"}.get(status.get("state"))
        try:
            host = urlsplit(song.get("file", "")).hostname or ""
        except ValueError:
            host = ""
        match = re.search(r"/(\d+)(?:-[a-z]+)?\.mp3$", song.get("file", ""))
        if not state or not match or not host.endswith("sverigesradio.se") or self._speaker_meta:
            return
        channel_id = int(match.group(1))
        info = await sr_channel(self.hass, channel_id)
        if self._speaker_meta:                  # the radio said what plays while SR answered
            return
        self._speaker_state = state
        chosen = self.hass.states.get("select.jarvis_ljud_ut")      # the speaker's name, for "P3 på JBL Charge 4"
        place = chosen.state if chosen and chosen.state not in ("Panelen", "unknown", "unavailable") else ""
        self._speaker_meta = {"title": text(info.get("name")) or "Radio", "artist": "Sveriges Radio", "source": "Radio",
                              "image": radio_image(info.get("image")), "channel_id": channel_id, "place": text(place, 40)}
        self.refresh()

    @callback
    def speaker_event(self, event):
        """The radio started ("playing" with the channel) or stopped ("idle") on the Bluetooth speaker."""
        data = event.data or {}
        if data.get("state") == "playing":
            self._speaker_state = "playing"
            if data.get("title"):           # a new channel; "playing" alone is a resume
                self._speaker_meta = {"title": text(data.get("title")), "artist": text(data.get("artist")),
                                      "source": "Radio" if data.get("source") == "Radio" else "Musik",
                                      "image": radio_image(data.get("image")), "channel_id": data.get("channel_id"),
                                      "place": text(data.get("place"), 40)}
        elif data.get("state") == "paused" and self._speaker_meta:
            self._speaker_state = "paused"
        else:
            self._speaker_state, self._speaker_meta = "idle", {}
        self.refresh()

    @callback
    def changed(self, event):
        entity = event.data.get("entity_id", "")
        new = event.data.get("new_state")
        if entity == SPEAKER:
            # HA polls MPD: follow a pause, a resume or a stop made elsewhere, once it notices. Only a change of
            # state counts, so a poll that still says "off" right after a play does not hide the new radio.
            old = event.data.get("old_state")
            now = new.state if new is not None else "unavailable"
            if (old.state if old is not None else None) != now and self._speaker_meta:
                self._speaker_state = now if now in ACTIVE else "idle"
                self.refresh()
            return
        if entity in (PLAYER, self._target, *MUSIC_STATES) or (entity.startswith("media_player.") and new is not None
                                               and new.attributes.get("active_queue") == PLAYER):
            self.refresh()

    @callback
    def called(self, event):
        data = event.data
        if data.get("domain") != "media_player" or data.get("service") != "play_media":
            return
        payload = data.get("service_data") or {}
        entities = payload.get("entity_id") or []
        if isinstance(entities, str):
            entities = [entities]
        if PLAYER not in entities or payload.get("announce") in (True, "true", "True"):
            return
        if payload.get("media_content_type") not in ("music", "audio/mpeg", "audio/wav", "audio/flac", "url"):
            return
        metadata = (payload.get("extra") or {}).get("metadata") or {}
        if not isinstance(metadata, dict):
            metadata = {}
        try:
            hostname = urlsplit(str(payload.get("media_content_id") or "")).hostname or ""
        except ValueError:
            hostname = ""
        source = "Radio" if hostname == "sverigesradio.se" or hostname.endswith(".sverigesradio.se") else "Musik"
        if metadata.get("source") == "Radio":
            source = "Radio"
        self._pending_metadata = ({"title": text(metadata.get("title")), "artist": text(metadata.get("artist")), "source": source,
                                   "image": radio_image(metadata.get("image")), "channel_id": metadata.get("channel_id")}, time.monotonic())
        # A request alone is not playback: retain metadata but wait for a real playing/paused state.
        self.refresh()

    def manager(self):
        for state in self.hass.states.async_all("media_player"):
            if state.entity_id != PLAYER and state.attributes.get("active_queue") == PLAYER and state.state in ACTIVE:
                return state
        return None

    def music(self, player):
        """The player's music state: the panel's own sensor when it has one, else HA's player state."""
        for entity in MUSIC_STATES:
            native = self.hass.states.get(entity)
            if native is not None and native.state in ("idle", "playing", "paused"):
                return native.state
        return player.state

    @callback
    def refresh(self):
        player = self.hass.states.get(PLAYER)
        manager = self.manager()
        self._target = PLAYER
        music = self.music(player) if player is not None else None
        if player is None or music in ("unknown", "unavailable", "off", "idle"):
            self._music_state = "idle"
            self._metadata = {}
        elif music in ACTIVE:
            self._music_state = music
            if self._pending_metadata and time.monotonic() - self._pending_metadata[1] < 30:
                self._metadata = self._pending_metadata[0]
            self._pending_metadata = None
        # ANNOUNCING preserves an existing music session, but cannot create one.
        state, metadata = self._music_state, self._metadata
        remote = state not in ACTIVE and self._speaker_state in ACTIVE      # the panel itself plays nothing
        if remote:
            state, metadata, self._target = self._speaker_state, self._speaker_meta, SPEAKER
        spot = self.spotify.state if self.spotify else {}
        self._spotify_stopped = spotify_stop(self._spotify_stopped, spot.get("status"), time.monotonic())
        spotify = state not in ACTIVE and not remote and spot.get("status") in ACTIVE and not self._spotify_stopped
        if spotify:
            chosen = self.hass.states.get("select.jarvis_ljud_ut")      # "Bailando på JBL Charge 4"
            place = chosen.state if chosen and chosen.state not in ("Panelen", "unknown", "unavailable") else ""
            remote, state, self._target = True, spot["status"], SPOTIFY
            metadata = {"title": text(spot.get("title")), "artist": text(spot.get("artist")), "source": "Spotify",
                        "place": text(place, 40)}
            # The Spelas nu page's album line: "Ghost Stories (2014)" (Joel's choice), the year once Spotify's album
            # page gave it.
            year = self.album_year.year(spot.get("album_uri")) if getattr(self, "album_year", None) else ""
            album = text(spot.get("album"), 80)
            metadata["album"] = f"{album} ({year})" if album and year else album
        title = metadata.get("title") or ("Musik på högtalaren" if remote else "Musik på hallpanelen")
        artist = metadata.get("artist") or ""
        source = metadata.get("source") or "Musik"
        next_, previous = spotify, spotify
        # Where it plays is part of a station logo's identity, the song is not: moving the radio between the panel
        # and the speaker hands the panel a new key (it drops its cover when the source changes), a new song does not.
        image_request = ("radio", metadata["image"], "speaker" if remote else "panel") if metadata.get("image") else None
        if spotify:     # a cover belongs to its track, as a player's does
            image_request = ("cover", spot["cover"], spot.get("uri")) if spotify_image(spot.get("cover")) else None
        radio_channel = metadata.get("title", "") if source == "Radio" and state in ACTIVE and not manager else ""
        if radio_channel:
            source = radio_channel
        if self.radio:
            self.radio.select(metadata.get("channel_id") if radio_channel else None, state == "playing")
        info = self.radio.read() if self.radio and radio_channel else {}
        if info.get("title"):
            title, artist, source = text(info["title"]), text(info.get("artist")), radio_channel
        # On the speaker the stream's own title says the song as it is heard; SR's API is the fallback.
        if self.speaker_title:
            self.speaker_title.follow(bool(remote and radio_channel and state == "playing"))
        programme, singer, song = parse(self.speaker_title.title) if remote and self.speaker_title else ("", "", "")
        if radio_channel and programme:
            title, artist, source = text(song or programme), text(singer), radio_channel
        if state in ACTIVE and manager and not remote:
            self._target = manager.entity_id
            title = text(manager.attributes.get("media_title")) or title
            artist = text(manager.attributes.get("media_artist")) or artist
            uri = str(manager.attributes.get("media_content_id") or "")
            source = "Spotify" if uri.startswith(("spotify:", "spotify--")) else "Musik"
            features = int(manager.attributes.get("supported_features") or 0)
            picture = manager.attributes.get("entity_picture")
            image_request = ("player", manager.entity_id, picture, uri) if picture else None
            next_, previous = bool(features & Feature.NEXT_TRACK), bool(features & Feature.PREVIOUS_TRACK)
            if player.state != "announcing":
                self._music_state = state = manager.state
        album = metadata.get("album", "") if spotify else ""
        # 45 lasts one song: another song, a pause, a stop or another source puts the record back to 33.
        if self._rpm == 45 and not (spotify and state == "playing" and spot.get("uri") == self._rpm_song):
            self._rpm, self._rpm_song = 33, None
            self.hass.async_create_task(self._set_rpm(33), "jarvis_agent.turntable_33")
        progress = spotify_progress(spot, time.time()) if spotify else ""
        if state not in ACTIVE:
            title = artist = source = album = progress = ""
            image_request = None
            remote = False
        # A player's cover belongs to its track, so the track is part of its identity. A station's logo is the same
        # for every song: with the song in it the key changed at each song, the panel dropped the logo and fetched
        # it again, and it blinked away for about a second (seen 2026-10-27).
        if image_request and image_request[0] == "player":
            image_request = (*image_request, title, artist)
        artwork = self.artwork.select(image_request) if self.artwork else ""
        # The next Spotify track's cover, prepared before the track changes (Soloist's queue): the panel may fetch it
        # ahead under this key, which is the one `artwork` will have then.
        coming = (getattr(self.spotify, "upcoming", None) or {}) if spotify and state in ACTIVE else {}
        next_request = ("cover", coming["cover"], coming["uri"]) if coming.get("cover") and coming.get("uri") else None
        artwork_next = self.artwork.prefetch(next_request) if self.artwork else ""
        self._attr_native_value = state
        # A seventh field says the music is not the panel's own, so its player going idle must not hide it: the
        # speaker's name, shown as "P3 på JBL Charge 4", or "1" when the name is not known.
        place = (metadata.get("place") or "1") if remote else ""
        wire = [state, source, title, artist, str(int(next_)), str(int(previous)), *([place] if remote else [])]
        duck = getattr(getattr(self._entry, "runtime_data", None), "spotify_duck" if spotify else "speaker_duck", None)
        level = duck.saved if duck is not None and duck.saved is not None else (
            spot.get("volume") if spotify else self.speaker_title.volume if self.speaker_title else None)
        self._attr_extra_state_attributes = {"title": title, "artist": artist, "source": source, "album": album,
            "rpm": self._rpm,
            "progress": progress,
            "next": next_, "previous": previous, "control_entity": self._target, "artwork": artwork, "artwork_next": artwork_next,
            "radio_channel": radio_channel, "radio_programme": text(info.get("programme")),
            "radio_description": text(info.get("description"), 240),
            # The Bluetooth speaker's volume for the panel's slider while it plays there; -1 when not known.
            "speaker_volume": level if remote and level is not None else -1,
            "wire": "|".join(wire)}
        if self._publish:
            self.async_write_ha_state()

    async def _set_rpm(self, rpm):
        """Ask Jarvis for this speed; returns what it set (33 when it could not, e.g. no pitch filter)."""
        try:
            result = await self._entry.runtime_data.speed(rpm)
        except Exception as err:   # noqa: BLE001 - the knob must never break the music
            _LOGGER.warning("Turntable speed %s failed: %s", rpm, err)
            return 33
        if result.get("error"):
            _LOGGER.warning("Turntable speed %s: %s", rpm, result["error"])
        return 45 if result.get("rpm") == 45 else 33

    async def speed(self, rpm=None):
        """The panel's 33/45 knob: rpm, or the other speed. Only Spotify playing on the speaker can be 45."""
        spot = self.spotify.state if self.spotify else {}
        want = rpm or (33 if self._rpm == 45 else 45)
        if want == 45 and not (self._target == SPOTIFY and spot.get("status") == "playing"):
            want = 33
        self._rpm = await self._set_rpm(want)
        self._rpm_song = spot.get("uri") if self._rpm == 45 else None
        self.refresh()
        return self._rpm

    async def control(self, action):
        self.refresh()
        if action not in ("toggle", "stop", "next", "previous"):
            raise HomeAssistantError("Unknown music action")
        if action != "stop" and self._attr_native_value not in ACTIVE:
            raise HomeAssistantError("No music is playing on the hallway panel")
        if self._target == SPEAKER:
            await self.speaker_control(action)
            return
        if self._target == SPOTIFY:
            await self.spotify_control(action)
            return
        manager = self.manager()
        # The panel may already have stopped its local stream before this queue-level request arrives.
        target = manager.entity_id if action == "stop" and manager else self._target
        if action in ("next", "previous") and not self._attr_extra_state_attributes[action]:
            raise HomeAssistantError("This source does not support skipping tracks")
        if action == "toggle":
            service = "media_play" if self._music_state == "paused" else "media_pause"
        else:
            service = {"stop": "media_stop", "next": "media_next_track", "previous": "media_previous_track"}[action]
        # Native media_stop targets music only; do not set announcement=True or stop Assist.
        await self.hass.services.async_call("media_player", service, {"entity_id": target}, blocking=True)
        if action == "stop":
            self._music_state = "idle"
            self._metadata = {}
            self._pending_metadata = None
            if self.artwork:
                self.artwork.select(None)
            if self.radio:
                self.radio.select(None, False)
            self._attr_native_value = "idle"
            self._attr_extra_state_attributes = {"wire": "idle||||0|0"}
            if self._publish:
                self.async_write_ha_state()

    async def spotify_control(self, action):
        """Pause/resume, next, previous and stop (a pause: Spotify keeps its queue) on Soloist."""
        if action == "toggle":
            command = "pause" if self._attr_native_value == "playing" else "play"
        else:
            command = SPOTIFY_COMMANDS[action]
        try:
            await self.spotify.command(command)
        except OSError as err:
            raise HomeAssistantError(f"Spotify on the speaker did not answer: {err}") from err
        if action == "stop":
            _LOGGER.info("Spotify stopped from the panel: paused and hidden until it plays again")
            self._spotify_stopped = ("pausing", time.monotonic())
            self.refresh()

    async def speaker_control(self, action):
        """Pause, resume and stop on the Bluetooth speaker, straight to MPD (no skipping: the radio is live)."""
        if action in ("next", "previous"):
            raise HomeAssistantError("This source does not support skipping tracks")
        command = "stop" if action == "stop" else "pause 1" if self._speaker_state == "playing" else "pause 0"
        try:
            await mpd(command)
        except (OSError, TimeoutError) as err:
            raise HomeAssistantError(f"The Bluetooth speaker did not answer: {err}") from err
        if action == "stop":
            self._speaker_state, self._speaker_meta = "idle", {}
        else:
            self._speaker_state = "paused" if self._speaker_state == "playing" else "playing"
        self.refresh()
