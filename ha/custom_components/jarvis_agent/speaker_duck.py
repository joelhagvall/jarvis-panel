"""Turn Jarvis's Bluetooth speaker down while the panel listens and answers (docs/bluetooth-speaker.md).

The panel lowers its own radio by itself, but it cannot reach music on the Bluetooth speaker (MPD on Jarvis,
127.0.0.1:6601). That music drowned Joel's question: the satellite listened 3-4 s and heard nothing. So while the
panel listens (and thinks), the music plays at a sixth of its volume (a tenth until 2026-10-28, when Joel wanted
a little more music); while Jarvis answers at 40 % (a quarter before), so it is heard under the reply. Then it
goes back.
MPD is asked directly: Home Assistant's MPD player answered play_media in 12.3 s.
"""
import asyncio
import logging

from homeassistant.core import callback
from homeassistant.helpers.event import async_track_state_change_event

_LOGGER = logging.getLogger(__name__)
SATELLITE = "assist_satellite.hallpanel_assist_satellite"
MPD = ("127.0.0.1", 6601)
# Spotify on the same speaker (docs/spotify.md): Soloist's WebSocket API, loopback only, no authentication there.
SOLOIST = "ws://127.0.0.1:9431"
QUIET = {"idle", "unavailable", "unknown"}
LISTEN_SHARE = 6                    # the volume is divided by this while the panel listens and thinks
SHARE = {"responding": 2.5}         # and by this while Jarvis answers
# Spotify (SoloistDuck): two thirds while he talks, full volume while Jarvis answers (Joel, 2026-11-01: "jag vill ha
# musiken rätt högt när jag pratar"). Without any ducking the radio once drowned the question (docs/bluetooth-speaker.md).
SPOTIFY_LISTEN_SHARE = 1.5
SPOTIFY_SHARE = {"responding": None}


async def mpd(*commands, address=MPD, timeout=2):
    """Each command's "key: value" lines as a dict. OSError when MPD is not there or refuses."""
    reader, writer = await asyncio.wait_for(asyncio.open_connection(*address), timeout)
    try:
        if not (await asyncio.wait_for(reader.readline(), timeout)).startswith(b"OK MPD"):
            raise OSError("not MPD")
        out = []
        for command in commands:
            writer.write(command.encode() + b"\n")
            await writer.drain()
            fields = {}
            while (line := await asyncio.wait_for(reader.readline(), timeout)) != b"OK\n":
                if not line or line.startswith(b"ACK"):
                    raise OSError(line.decode(errors="replace").strip() or "MPD closed")
                key, _, value = line.decode(errors="replace").rstrip("\n").partition(": ")
                fields[key] = value
            out.append(fields)
        return out
    finally:
        writer.close()


class SpeakerDuck:
    LISTEN_SHARE, SHARE = LISTEN_SHARE, SHARE      # the radio's (MPD's) ducking; SoloistDuck has its own

    def __init__(self, hass, address=MPD):
        self.hass, self.address = hass, address
        self.saved = None               # MPD's volume before it was turned down, while it is down
        self.share = None               # what it is divided by while down
        self.lock = asyncio.Lock()
        self.unsub = None

    def start(self):
        self.unsub = async_track_state_change_event(self.hass, [SATELLITE], self.changed)

    def close(self):
        if self.unsub:
            self.unsub()
            self.unsub = None

    @callback
    def changed(self, event):
        new = event.data.get("new_state")
        state = new.state if new is not None else "unavailable"
        self.hass.async_create_task(self.apply(None if state in QUIET else self.SHARE.get(state, self.LISTEN_SHARE)))

    async def apply(self, share):
        """share: divide the music's volume by this, or None to put it back."""
        async with self.lock:
            try:
                if share and self.saved is None:
                    status = (await mpd("status", address=self.address))[0]
                    volume = int(status.get("volume", -1))
                    if status.get("state") != "play" or volume <= 1:
                        return
                    self.saved = volume
                if share and self.saved is not None:
                    self.share = share
                    await mpd(f"setvol {max(1, round(self.saved / share))}", address=self.address)
                elif not share and self.saved is not None:
                    volume, self.saved, self.share = self.saved, None, None
                    await mpd(f"setvol {volume}", address=self.address)
            except (OSError, asyncio.TimeoutError, ValueError) as e:
                # No MPD (no speaker set up) is the normal case: nothing to turn down.
                _LOGGER.debug("Bluetooth speaker volume unchanged: %s", e)

    async def volume(self, level=None, step=None):
        """The speaker's own volume (0-100), set to level or moved by step first. Jarvis's volume tool asks it
        while the panel listens, so the music is down: then the volume it goes back to changes, or the
        change would be undone when Jarvis has answered."""
        async with self.lock:
            status = (await mpd("status", address=self.address))[0]
            current = self.saved if self.saved is not None else int(status.get("volume", -1))
            if current < 0:
                raise OSError("MPD has no volume (no output)")
            new = current if level is None and step is None else (
                max(0, min(100, int(level) if level is not None else current + int(step))))
            if self.saved is not None:
                self.saved = new
                await mpd(f"setvol {round(new / self.share) if new else 0}", address=self.address)
            elif new != current:
                await mpd(f"setvol {new}", address=self.address)
            return new


async def soloist(url=SOLOIST, volume=None, timeout=1.5, session=None):
    """Soloist's playback_state (it sends one on connect when logged in), after setting volume when given.
    None when nobody is logged in. OSError when Soloist is not there."""
    import aiohttp
    own = session is None
    session = session or aiohttp.ClientSession()
    try:
        async with session.ws_connect(url, timeout=aiohttp.ClientWSTimeout(ws_close=timeout)) as ws:
            state = None
            loop = asyncio.get_running_loop()
            end = loop.time() + timeout
            while state is None and loop.time() < end:
                try:
                    message = await asyncio.wait_for(ws.receive_json(), end - loop.time())
                except asyncio.TimeoutError:
                    break
                if message.get("type") == "playback_state":
                    state = message
            if volume is not None and state is not None:
                await ws.send_json({"type": "command", "command": "set_volume", "volume": int(volume)})
                while loop.time() < end + timeout:
                    message = await asyncio.wait_for(ws.receive_json(), timeout)
                    if message.get("type") == "error":
                        raise OSError(message.get("message") or "Soloist refused set_volume")
                    if message.get("type") == "command_result" and message.get("command") == "set_volume":
                        break
            return state
    except (aiohttp.ClientError, ValueError, TypeError) as e:
        raise OSError(str(e) or type(e).__name__) from None
    finally:
        if own:
            await session.close()


def playing_here(state):
    """Soloist plays on the JBL itself. Its playback_state mirrors the whole account, so it says "playing" while Joel's
    iPhone plays (is_active false), and set_volume then reaches the phone's session, as pause did (2026-10-29)."""
    return bool(state) and state.get("status") == "playing" and state.get("is_active") is not False


class SoloistDuck(SpeakerDuck):
    """The same quiet as SpeakerDuck, for Spotify playing through Soloist on the speaker, only milder: Joel wants
    Spotify fairly loud while he talks and at full volume while Jarvis answers (2026-11-01). The radio keeps a sixth."""

    LISTEN_SHARE = SPOTIFY_LISTEN_SHARE
    SHARE = SPOTIFY_SHARE

    WATCH_S, WATCH_EVERY_S = 10.0, 0.3

    def __init__(self, hass, url=SOLOIST, exchange=None):
        super().__init__(hass)
        self.url = url
        self.exchange = exchange or (lambda volume=None: soloist(self.url, volume))
        self.wanted = None              # the share the satellite's state asks for now
        self.watch = None

    async def apply(self, share):
        self.wanted = share
        async with self.lock:
            try:
                if share and self.saved is None:
                    state = await self.exchange()
                    volume = int((state or {}).get("volume", -1))
                    if not playing_here(state) or volume <= 1:
                        # "Spela Kent på Spotify" starts the music during the turn, after this check: it then
                        # played at full volume over "Spelar Kent" (Joel, 2026-10-28). Watch for it to start.
                        if state is not None and (self.watch is None or self.watch.done()):
                            self.watch = asyncio.get_running_loop().create_task(self._watch_start())
                        return
                    self.saved = volume
                if share and self.saved is not None:
                    self.share = share
                    await self.exchange(max(1, round(self.saved / share)))
                elif not share and self.saved is not None:
                    volume, self.saved, self.share = self.saved, None, None
                    await self.exchange(volume)
            except (OSError, asyncio.TimeoutError, ValueError) as e:
                _LOGGER.debug("Spotify volume unchanged: %s", e)

    async def _watch_start(self):
        """Turn Spotify down as soon as it starts playing within the turn, at most WATCH_S."""
        loop = asyncio.get_running_loop()
        end = loop.time() + self.WATCH_S
        while self.wanted and loop.time() < end:
            await asyncio.sleep(self.WATCH_EVERY_S)
            async with self.lock:
                if not self.wanted or self.saved is not None:
                    return
                try:
                    state = await self.exchange()
                    volume = int((state or {}).get("volume", -1))
                    if playing_here(state) and volume > 1:
                        self.saved, self.share = volume, self.wanted
                        await self.exchange(max(1, round(volume / self.wanted)))
                        _LOGGER.info("Spotify started during a voice turn: down from %s while Jarvis talks", volume)
                        return
                except (OSError, asyncio.TimeoutError, ValueError) as e:
                    _LOGGER.debug("Spotify volume unchanged: %s", e)
                    return

    async def volume(self, level=None, step=None):
        """Spotify's own volume (0-100), set to level or moved by step first, past the ducking (as SpeakerDuck)."""
        async with self.lock:
            state = await self.exchange()
            current = self.saved if self.saved is not None else int((state or {}).get("volume", -1))
            if current < 0:
                raise OSError("Spotify has no volume (nobody logged in)")
            new = current if level is None and step is None else (
                max(0, min(100, int(level) if level is not None else current + int(step))))
            if self.saved is not None:
                self.saved = new
                await self.exchange(round(new / self.share) if new else 0)
            elif new != current:
                await self.exchange(new)
            return new
