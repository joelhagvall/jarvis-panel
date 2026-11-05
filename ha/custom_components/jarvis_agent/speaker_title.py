"""The song on Jarvis's Bluetooth speaker, from the stream itself (docs/bluetooth-speaker.md).

Sveriges Radio's streams carry the song as ICY metadata, which MPD shows as the current song's Title:
"P3 med Juan Havana, spelar nu Swedish House Mafia & Lykke Li - Happiness Is So Sad". It changes with the
audio, while SR's API is polled and runs on broadcast time. MPD's idle command says at once when it changes.
"""
import asyncio
import logging

from .speaker_duck import MPD

_LOGGER = logging.getLogger(__name__)
NOW = ", spelar nu "
# Longest wait for MPD's idle before asking again. Whether MPD reports a new stream title as a player event
# is not verified, so the title is also read at least this often.
CHECK_S = 5


def parse(title):
    """(programme, artist, song) from SR's stream title; artist and song are "" between songs."""
    title = " ".join(str(title or "").split())
    programme, sep, song = title.partition(NOW)
    if not sep:
        return programme, "", ""
    artist, sep, name = song.partition(" - ")
    return (programme, artist, name) if sep else (programme, "", song)


class SpeakerTitle:
    """Follows MPD's stream title while asked to; calls updated() when it changes."""

    def __init__(self, hass, updated, address=MPD):
        self.hass, self.updated, self.address = hass, updated, address
        self.title = ""
        self.volume = None              # MPD's own volume (0-100) as last read; the ducking can have lowered it
        self.task = None

    def follow(self, on):
        if on and self.task is None:
            self.task = self.hass.async_create_background_task(self._loop(), "jarvis_agent.speaker_title")
        elif not on and self.task is not None:
            self.task.cancel()
            self.task, self.title = None, ""

    def close(self):
        self.follow(False)

    async def _loop(self):
        while True:
            try:
                reader, writer = await asyncio.wait_for(asyncio.open_connection(*self.address), 2)
                try:
                    if not (await reader.readline()).startswith(b"OK MPD"):
                        raise OSError("not MPD")
                    while True:
                        writer.write(b"currentsong\nstatus\n")
                        await writer.drain()
                        title, volume = "", None
                        for _ in range(2):
                            while (line := await reader.readline()) != b"OK\n":
                                if not line or line.startswith(b"ACK"):
                                    raise OSError("MPD closed")
                                if line.startswith(b"Title: "):
                                    title = line[7:].decode(errors="replace").strip()
                                elif line.startswith(b"volume: "):
                                    volume = int(line[8:]) if line[8:].strip().isdigit() else None
                        if (title, volume) != (self.title, self.volume):
                            self.title, self.volume = title, volume
                            self.updated()
                        writer.write(b"idle player playlist\n")
                        await writer.drain()
                        try:
                            await asyncio.wait_for(self._ok(reader), CHECK_S)
                        except TimeoutError:
                            writer.write(b"noidle\n")      # MPD ends the idle with OK (and any change lines)
                            await writer.drain()
                            await asyncio.wait_for(self._ok(reader), 2)
                finally:
                    writer.close()
            except (OSError, TimeoutError) as err:
                _LOGGER.debug("Speaker title unavailable: %s", err)
                await asyncio.sleep(5)

    @staticmethod
    async def _ok(reader):
        while (line := await reader.readline()) != b"OK\n":
            if not line or line.startswith(b"ACK"):
                raise OSError("MPD closed")
