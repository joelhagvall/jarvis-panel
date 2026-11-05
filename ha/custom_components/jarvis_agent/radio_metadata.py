"""Live SR text while music plays. No lookup on the playback critical path."""
import asyncio
import json
import re
import time


def timestamp(value):
    match = re.fullmatch(r'/Date\((\d+)\)/', str(value or ''))
    return int(match[1]) / 1000 if match else 0


def current(programme, playlist, now):
    """Only current, timestamped entries; never relabel previoussong as live."""
    def mapping(value):return value if isinstance(value, dict) else {}
    episode = mapping(mapping(mapping(programme).get('channel')).get('currentscheduledepisode'))
    song = mapping(mapping(playlist).get('playlist')).get('song')
    song = mapping(song)
    result = {}
    start, end = timestamp(episode.get('starttimeutc')), timestamp(episode.get('endtimeutc'))
    if start <= now < end and episode.get('title'):
        title = ' '.join(str(episode['title']).split())
        subtitle = ' '.join(str(episode.get('subtitle') or '').split())
        if subtitle and subtitle.casefold() not in title.casefold():
            title += ' ' + subtitle
        result = {'title': title, 'artist': subtitle,
                  'programme': title, 'description': episode.get('description') or '', 'until': end}
    start, end = timestamp(song.get('starttimeutc')), timestamp(song.get('stoptimeutc'))
    if start <= now < end and song.get('title'):
        result.update(title=song['title'], artist=song.get('artist') or '', until=end)
    return result


class RadioMetadata:
    def __init__(self, hass, updated):
        self.hass, self.updated = hass, updated
        self.request = None
        self.task = None
        self.documents = {}
        self.paused = {}

    def close(self):
        if self.task:
            self.task.cancel()
        self.request, self.documents, self.paused = None, {}, {}

    def select(self, channel, playing):
        if type(channel) is not int or not 0 < channel < 1000000:
            channel = None
        request = (channel, bool(playing)) if channel else None
        if request == self.request:
            return
        paused = self.read() if request and not playing else {}
        if self.task:
            self.task.cancel()
        if not request or not self.request or request[0] != self.request[0]:
            self.documents = {}
        self.request, self.paused = request, paused
        if request and playing:
            self.task = self.hass.async_create_task(self._loop(request), 'Jarvis SR now playing')

    def read(self):
        if not self.request:
            return {}
        if not self.request[1]:
            return self.paused
        now = time.time()
        def document(name):
            value, fetched = self.documents.get(name, ({}, 0))
            return value if now - fetched < 90 else {}
        return current(document('programme'), document('playlist'), now)

    async def _get(self, method, channel):
        from homeassistant.helpers.aiohttp_client import async_get_clientsession
        async with asyncio.timeout(5):
            async with async_get_clientsession(self.hass).get(
                    f'https://api.sr.se/api/v2/{method}/rightnow?channelid={channel}&format=json',
                    allow_redirects=False) as response:
                if response.status != 200:
                    raise ValueError('SR metadata unavailable')
                body = bytearray()
                async for chunk in response.content.iter_chunked(16384):
                    body.extend(chunk)
                    if len(body) > 262144:
                        raise ValueError('SR metadata exceeds budget')
                value = json.loads(body)
                if not isinstance(value, dict):
                    raise ValueError('Invalid SR metadata')
                return value

    async def _loop(self, request):
        while request == self.request:
            values = await asyncio.gather(self._get('scheduledepisodes', request[0]),
                                          self._get('playlists', request[0]), return_exceptions=True)
            if request != self.request:
                return
            now = time.time()
            for name, value in zip(('programme', 'playlist'), values):
                if isinstance(value, dict):
                    self.documents[name] = value, now
            self.updated()
            # A known song/program expiry also removes old text when SR is late.
            remaining = self.read().get('until', now + 30) - now
            await asyncio.sleep(max(1, min(30, remaining)))
            if request == self.request:
                self.updated()


async def channel(hass, channel_id):
    """SR's name and logo for a channel id: {"name", "image"}, or {} when SR does not answer."""
    from homeassistant.helpers.aiohttp_client import async_get_clientsession
    try:
        async with asyncio.timeout(5):
            async with async_get_clientsession(hass).get(
                    f'https://api.sr.se/api/v2/channels/{int(channel_id)}?format=json', allow_redirects=False) as response:
                if response.status != 200:
                    return {}
                value = (await response.json(content_type=None) or {}).get('channel') or {}
                return {'name': str(value.get('name') or ''), 'image': str(value.get('image') or '')}
    except (OSError, TimeoutError, ValueError):
        return {}
