"""The year of the album Spotify plays, for the panel's Musik page ("Ghost Stories (2014)", Joel's wish 2026-10-28).

Soloist reports the album's name and URI but no date. Spotify's public album page carries it in
`<meta name="music:release_date" content="2014-05-16">`. This reads that page once per album, bounded in size and
time, caches the year, and asks the sensor to refresh when it arrives. Only open.spotify.com album pages built from a
validated album id are fetched. A failure is tried again after a minute, at most three times, and logged as a
warning (some singles had no year on 2026-10-28 and nothing said why), so a page that changed never causes a loop.
"""
import asyncio
import logging
import re
import time

_LOGGER = logging.getLogger(__name__)
ALBUM = re.compile(r"spotify:album:([A-Za-z0-9]{22})")
RELEASE = re.compile(rb'<meta\s+name="music:release_date"\s+content="(\d{4})(?:-\d{2}){0,2}"')
MAX_BYTES = 1024 * 1024
TIMEOUT_S = 8
RETRY_S = 60
TRIES = 3
KEEP = 128


def album_id(uri):
    match = ALBUM.fullmatch(str(uri or ""))
    return match.group(1) if match else ""


def release_year(page):
    """The release year in an album page's head, or ""."""
    match = RELEASE.search(page or b"")
    return match.group(1).decode() if match else ""


class AlbumYear:
    def __init__(self, hass, updated):
        self.hass, self.updated = hass, updated
        self.years = {}            # album id -> (year or "", when it was looked up, tries so far)
        self.busy = set()

    def year(self, uri):
        """The cached year of this album, or "" while it is looked up (the sensor refreshes when it is in)."""
        key = album_id(uri)
        if not key:
            return ""
        cached = self.years.get(key)
        if cached and (cached[0] or cached[2] >= TRIES or time.monotonic() - cached[1] < RETRY_S):
            return cached[0]
        if key not in self.busy:
            self.busy.add(key)
            self.hass.async_create_task(self._look_up(key), "jarvis_agent.album_year")
        return cached[0] if cached else ""

    async def _look_up(self, key):
        from aiohttp import ClientError
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        year, status = "", None
        try:
            session = async_get_clientsession(self.hass)
            async with asyncio.timeout(TIMEOUT_S):
                async with session.get(f"https://open.spotify.com/album/{key}", allow_redirects=False,
                                       headers={"User-Agent": "Mozilla/5.0 (Jarvis hallpanel)"}) as response:
                    if response.status == 200:
                        body = await response.content.read(MAX_BYTES)
                        year = release_year(body)
                    status = response.status
            if not year:
                _LOGGER.warning("No release year on Spotify's page for album %s (HTTP %s)", key, status)
        except (ClientError, OSError, asyncio.TimeoutError) as err:
            _LOGGER.warning("Spotify album page for %s unavailable: %s", key, err)
        finally:
            self.busy.discard(key)
        if len(self.years) >= KEEP:
            self.years.pop(next(iter(self.years)))
        tries = (self.years.get(key) or ("", 0, 0))[2] + 1
        self.years[key] = (year, time.monotonic(), tries)
        if year:
            self.updated()
