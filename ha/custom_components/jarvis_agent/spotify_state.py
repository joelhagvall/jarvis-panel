"""What Spotify plays on Jarvis's speaker, for the panel's Spelas nu (docs/spotify.md).

Spotify Soloist (the Spotify Connect speaker "Jarvis") pushes its state over its WebSocket API on 127.0.0.1:
playback_state on connect and on every change, volume_changed when the volume moves. This keeps one connection
open, reconnects every few seconds while Soloist is away, and sends the panel's pause/next/previous back on it.
The API has no authentication, so it is only ever reached on loopback.
"""
import asyncio
import logging

from .speaker_duck import SOLOIST

_LOGGER = logging.getLogger(__name__)
RETRY_S = 5
# Soloist reports "buffering" for about a second at every track change (traced 2026-10-28): that is still playing,
# or Spelas nu disappeared and came back at each new song. Any other gap is bridged for HOLD_S before it hides.
STATUS = {"buffering": "playing"}
SHOWN = {"playing", "paused"}
HOLD_S = 4
COVER_SIZES = ("default", "large", "small", "xlarge")      # the panel shows 144 px: default (300 px) is enough


def item_cover(item):
    """The cover URL Soloist gives a track (playback_state's item, or a queue entry's)."""
    decorations = (item or {}).get("decorations") or {}
    covers = {c.get("size"): c.get("url") for c in (decorations.get("visual_identity") or {}).get("cover") or []
              if isinstance(c, dict)}
    return next((covers[size] for size in COVER_SIZES if covers.get(size)), "")


def upcoming(message):
    """{cover, uri} of the next track in a queue_changed message, or {}."""
    entries = message.get("upcoming") or []
    item = (entries[0] or {}).get("item") if entries and isinstance(entries[0], dict) else None
    if not isinstance(item, dict) or not item.get("uri"):
        return {}
    return {"cover": item_cover(item), "uri": item["uri"]}


def parse(state):
    """{status, title, artist, album, album_uri, uri, cover, volume} from a playback_state message, or {} when
    nothing is loaded."""
    item = state.get("item") or {}
    # Spotify Connect shares the account's state with every device: playing on Joel's iPhone, Soloist reported
    # "playing" with is_active false and a nameless item, and Spelas nu showed "Musik på högtalaren" (2026-10-28).
    # Only Jarvis itself playing counts.
    if not item or state.get("is_active") is False:
        return {}
    decorations = item.get("decorations") or {}
    name = lambda node: (((node or {}).get("decorations") or {}).get("identity") or {}).get("name") or ""
    # Soloist 1.3.10 nests creators in decorations; read the item itself too.
    creators = item.get("creators") or decorations.get("creators") or []
    cover = item_cover(item)
    # The track's album: its parent when that is an album (a single is one too), else the context when it plays
    # from an album. Some singles had no year on the panel (Tate McRae, 2026-10-28): the URI decides, not the type.
    album = {}
    for node in ((decorations.get("parent") or {}).get("entity") or {}, state.get("context") or {}):
        if str(node.get("uri") or "").startswith("spotify:album:"):
            album = node
            break
    status = state.get("status") or ""
    return {"status": STATUS.get(status, status), "title": name(item),
            "artist": ", ".join(n for n in (name(c.get("entity")) for c in creators if isinstance(c, dict)) if n),
            "album": name(album), "album_uri": album.get("uri") or "",
            # Where the song is: Soloist's position at its own epoch timestamp, and the song's length.
            "position_ms": int((state.get("position") or {}).get("position_ms") or 0),
            "position_at_ms": int((state.get("position") or {}).get("timestamp_ms") or 0),
            "duration_ms": int(((decorations.get("playback") or {}).get("duration_ms")) or 0),
            "uri": item.get("uri") or "", "cover": cover,
            "volume": state.get("volume") if isinstance(state.get("volume"), int) else None}


class SoloistWatch:
    def __init__(self, hass, updated, url=SOLOIST):
        self.hass, self.updated, self.url = hass, updated, url
        self.state = {}
        self.ws = None
        self.task = None
        self.hold = None                # the timer that hides Spotify after a short gap
        self.pending = None
        # The next track in Soloist's queue, {cover, uri}: its cover is prepared ahead (media_artwork.prefetch).
        self.upcoming = {}

    def start(self):
        self.task = self.hass.async_create_background_task(self._loop(), "jarvis_agent.spotify_state")

    def close(self):
        if self.task:
            self.task.cancel()
            self.task = None
        self._cancel_hold()

    def _cancel_hold(self):
        if self.hold is not None:
            self.hold.cancel()
            self.hold = None

    def offer(self, state):
        """A new state from Soloist: shown at once, unless it would hide a shown one; that waits HOLD_S."""
        if state.get("status") in SHOWN or self.state.get("status") not in SHOWN:
            self._cancel_hold()
            self._set(state)
            return
        self.pending = state
        if self.hold is None:
            self.hold = self.hass.loop.call_later(HOLD_S, self._release)

    def _release(self):
        self.hold = None
        self._set(self.pending or {})

    def _set(self, state):
        if state != self.state:
            self.state = state
            self.updated()

    def handle(self, message):
        kind = message.get("type")
        if kind == "playback_state":
            before = self.state.get("uri")
            self.offer(parse(message))
            if parse(message).get("uri") != before:
                self.ask_queue()        # the next track changes with the track
        elif kind == "queue_changed":
            nxt = upcoming(message)
            if nxt != self.upcoming:
                self.upcoming = nxt
                self.updated()
        elif kind == "volume_changed" and self.state and isinstance(message.get("volume"), int):
            self._set({**self.state, "volume": message["volume"]})
        elif kind == "auth_state" and message.get("logged_in") is False:
            self._set({})

    async def _loop(self):
        from aiohttp import ClientError
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        session = async_get_clientsession(self.hass)
        while True:
            try:
                async with session.ws_connect(self.url, heartbeat=30) as ws:
                    self.ws = ws
                    self.ask_queue()
                    async for frame in ws:
                        try:
                            self.handle(frame.json())
                        except (ValueError, TypeError, AttributeError):
                            continue
            except (ClientError, OSError, asyncio.TimeoutError) as err:
                _LOGGER.debug("Spotify Soloist unavailable: %s", err)
            finally:
                self.ws = None
            self._set({})
            self.upcoming = {}
            await asyncio.sleep(RETRY_S)

    def ask_queue(self):
        """Ask Soloist for the next track; it answers with queue_changed. Nothing when it is not connected."""
        async def ask():
            try:
                await self.command("get_queue", limit=1)
            except OSError:
                pass
        if self.ws is not None and not self.ws.closed:
            self.hass.async_create_task(ask(), "jarvis_agent.spotify_queue")

    async def command(self, name, **fields):
        """Send a control command (pause, play, skip_next, skip_prev, set_volume). OSError without a connection."""
        if self.ws is None or self.ws.closed:
            raise OSError("Spotify Soloist is not connected")
        await self.ws.send_json({"type": "command", "command": name, **fields})
