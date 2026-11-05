#!/usr/bin/env python3
"""How much of a live WAV stream Home Assistant's ffmpeg holds before it writes anything (read-only, no audio).

HA converts every TTS stream for the panel with ffmpeg; ffmpeg 8.1 wrote nothing until more than 64 KiB arrived
(2026-10-30). ha/custom_components/jarvis_agent/speech.py PANEL_START_BYTES tops a reply up past that, so rerun
this after a Home Assistant update and compare (docs/voice-latency.md):

  sh tools/jarvis_ssh.sh user@jarvis.local 'docker exec -i homeassistant python3 -' < tools/ha_ffmpeg_hold.py
"""
import asyncio
import io
import wave

# HA's command for a live TTS stream (homeassistant/components/tts/__init__.py _async_convert_audio) as the panel
# asks for it: WAV, 16 kHz, mono, 16 bit.
COMMAND = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-f", "wav", "-probesize", "32", "-i", "pipe:0",
           "-f", "wav", "-ar", "16000", "-ac", "1", "-sample_fmt", "s16", "-fflags", "+bitexact", "pipe:1"]


def header():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setframerate(16000)
        w.setnchannels(1)
        w.setsampwidth(2)
    return out.getvalue()


async def passed(data_bytes):
    """Bytes ffmpeg writes within 0.8 s after header + data_bytes of audio, its stdin left open."""
    process = await asyncio.create_subprocess_exec(*COMMAND, stdin=asyncio.subprocess.PIPE,
                                                   stdout=asyncio.subprocess.PIPE)
    process.stdin.write(header() + b"\x01\x00" * (data_bytes // 2))
    await process.stdin.drain()
    total = 0
    try:
        while chunk := await asyncio.wait_for(process.stdout.read(65536), 0.8):
            total += len(chunk)
    except asyncio.TimeoutError:
        pass
    process.kill()
    await process.wait()
    return total


async def main():
    for kib in (16, 32, 48, 63, 64, 66, 80, 96):
        print(f"{kib:>3} KiB in -> {await passed(kib * 1024)} B out", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
