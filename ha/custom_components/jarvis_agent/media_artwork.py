"""One bounded, prepared cover for this panel. Public reads never fetch a URL."""
import asyncio
import hashlib
import io
import logging
import re
from urllib.parse import urlsplit
import warnings

from aiohttp import ClientError, web
from PIL import Image, ImageOps
from homeassistant.components.http import HomeAssistantView
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.entity_component import DATA_INSTANCES

from .const import DOMAIN

SIZE = 288     # the Musik page's record; the panel makes the row's and sheet's 144 px copy (2026-10-28)
COVERS_KEPT = 3   # prepared Spotify covers by URL: the current one, the next one and the one before
MAX_BYTES = 4 * 1024 * 1024
_LOGGER = logging.getLogger(__name__)


def radio_image(value):
    """Only SR's official CDN; play_media metadata is not an arbitrary URL proxy."""
    try:
        parsed = urlsplit(str(value or ""))
        if (parsed.scheme == "https" and parsed.hostname == "static-cdn.sr.se"
                and not parsed.username and not parsed.password and parsed.port in (None, 443)
                and len(value) <= 2048):
            return value
    except (ValueError, TypeError):
        pass
    return ""


def spotify_image(value):
    """Only Spotify's image CDN, for the cover Soloist reports (docs/spotify.md)."""
    try:
        parsed = urlsplit(str(value or ""))
        if (parsed.scheme == "https" and parsed.hostname == "i.scdn.co" and parsed.path.startswith("/image/")
                and not parsed.username and not parsed.password and parsed.port in (None, 443) and len(value) <= 512):
            return value
    except (ValueError, TypeError):
        pass
    return ""


def prepare_image(data):
    """Decode/resize on an executor, never HA's event loop or the ESP UI task."""
    if not data or len(data) > MAX_BYTES:
        raise ValueError("Artwork exceeds image budget")
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > 4096 * 4096:
                raise ValueError("Artwork exceeds pixel budget")
            image = ImageOps.exif_transpose(image)
            rgba = ImageOps.fit(image.convert("RGBA"), (SIZE, SIZE), method=Image.Resampling.LANCZOS)
            background = Image.new("RGB", rgba.size, "#141826")
            background.paste(rgba, mask=rgba.getchannel("A"))
            output = io.BytesIO()
            background.save(output, format="QOI")
            return output.getvalue()


class MediaArtwork:
    def __init__(self, hass, updated):
        self.hass, self.updated = hass, updated
        self.request = None
        self.key = ""
        self.data = None
        self.task = None
        self.radio_cache = None
        # Spotify covers prepared ahead (prefetch) or before, by URL, and the next track's key and image: the
        # panel may download that one before the track changes (docs/now-playing.md).
        self.covers = {}
        self.next_request, self.next_key, self.next_data, self.next_task = None, "", None, None

    def close(self):
        for task in (self.task, self.next_task):
            if task:
                task.cancel()
        self.request, self.key, self.data = None, "", None
        self.radio_cache = None
        self.covers = {}
        self.next_request, self.next_key, self.next_data, self.next_task = None, "", None, None

    @staticmethod
    def key_for(prepared, request):
        # HA forwards an attribute only when it changes. Include display/track
        # identity so two songs sharing one cover still republish a new key.
        return hashlib.sha256(prepared + repr(request).encode()).hexdigest()[:32]

    def _keep(self, url, prepared):
        self.covers.pop(url, None)
        self.covers[url] = prepared
        while len(self.covers) > COVERS_KEPT:
            self.covers.pop(next(iter(self.covers)))

    def select(self, request):
        if request == self.request:
            return self.key
        if self.task:
            self.task.cancel()
        self.request, self.key, self.data = request, "", None
        if request and request[0] == "cover" and request[1] in self.covers:
            # Prepared ahead: the key is there at once, without fetching or resizing again.
            self.data = self.covers[request[1]]
            self.key = self.key_for(self.data, request)
            return self.key
        if request:
            self.task = self.hass.async_create_task(self._load(request), "Jarvis media artwork")
        return ""

    def prefetch(self, request):
        """Prepare the next Spotify track's cover now; its key is `next_key` once ready. None forgets it."""
        if request == self.next_request:
            return self.next_key
        if self.next_task:
            self.next_task.cancel()
        self.next_request, self.next_key, self.next_data, self.next_task = request, "", None, None
        if request and request[0] == "cover" and spotify_image(request[1]):
            if request[1] in self.covers:
                self.next_data = self.covers[request[1]]
                self.next_key = self.key_for(self.next_data, request)
            else:
                self.next_task = self.hass.async_create_task(self._prefetch(request), "Jarvis next artwork")
        return self.next_key

    async def _prefetch(self, request):
        try:
            async with asyncio.timeout(8):
                prepared = await self.hass.async_add_executor_job(prepare_image, await self._fetch(request[1]))
            if request != self.next_request:
                return
            self._keep(request[1], prepared)
            self.next_data, self.next_key = prepared, self.key_for(prepared, request)
            self.updated()
        except (ClientError, TimeoutError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            _LOGGER.debug("Next media artwork unavailable")

    async def _fetch(self, url):
        async with async_get_clientsession(self.hass).get(url, allow_redirects=False) as response:
            if response.status != 200:
                raise OSError(f"artwork answered {response.status}")
            body = bytearray()
            async for chunk in response.content.iter_chunked(16384):
                body.extend(chunk)
                if len(body) > MAX_BYTES:
                    raise ValueError("Artwork exceeds image budget")
            return bytes(body)

    async def _load(self, request):
        try:
            prepared = None
            async with asyncio.timeout(8):
                if request[0] in ("radio", "cover"):
                    url = radio_image(request[1]) if request[0] == "radio" else spotify_image(request[1])
                    if not url:
                        return
                    if (request[0] == "cover" and self.next_task and not self.next_task.done()
                            and self.next_request and self.next_request[1] == url):
                        await asyncio.shield(self.next_task)   # the track came while its cover was being prepared
                    if self.radio_cache and self.radio_cache[0] == url:
                        prepared = self.radio_cache[1]
                    elif url in self.covers:
                        prepared = self.covers[url]
                    else:
                        async with async_get_clientsession(self.hass).get(url, allow_redirects=False) as response:
                            if response.status != 200:
                                return
                            body = bytearray()
                            async for chunk in response.content.iter_chunked(16384):
                                body.extend(chunk)
                                if len(body) > MAX_BYTES:
                                    return
                            data = bytes(body)
                else:
                    # Reuse the actual HA player and its image cache/auth, never a
                    # URL supplied by the public artwork endpoint or panel.
                    component = self.hass.data.get(DATA_INSTANCES, {}).get("media_player")
                    player = component.get_entity(request[1]) if component else None
                    if player is None:
                        return
                    data, _ = await player.async_get_media_image()
                if prepared is None:
                    prepared = await self.hass.async_add_executor_job(prepare_image, data)
            if request != self.request:
                return  # A slow old cover must never replace the current track.
            self.data = prepared
            if request[0] == "radio":
                self.radio_cache = request[1], prepared
            elif request[0] == "cover":
                self._keep(request[1], prepared)
            self.key = self.key_for(prepared, request)
            self.updated()
        except (ClientError, TimeoutError, OSError, ValueError, Image.DecompressionBombWarning, Image.DecompressionBombError):
            _LOGGER.debug("Current media artwork unavailable")


class JarvisMediaArtworkView(HomeAssistantView):
    url = "/api/jarvis_agent/media-art/{key}.qoi"
    name = "api:jarvis_agent:media-art"
    requires_auth = False

    def __init__(self, hass):
        self.hass = hass

    async def get(self, request, key):
        if not re.fullmatch(r"[a-f0-9]{32}", key):
            raise web.HTTPNotFound()
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            media = getattr(getattr(entry, "runtime_data", None), "media", None)
            artwork = getattr(media, "artwork", None)
            if artwork and artwork.key == key and artwork.data:
                return web.Response(body=artwork.data, content_type="image/qoi",
                                    headers={"Cache-Control": "public, max-age=86400, immutable"})
            if artwork and artwork.next_key == key and artwork.next_data:
                return web.Response(body=artwork.next_data, content_type="image/qoi",
                                    headers={"Cache-Control": "public, max-age=86400, immutable"})
        raise web.HTTPNotFound()
