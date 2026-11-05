"""Authenticated native PCM from HA Cloud, avoiding a WAV24 -> WAV16 ffmpeg process per sentence."""

import io
import wave

from aiohttp import ClientError, web
from hass_nabucasa.voice import AudioOutput, VoiceError
from homeassistant.components.cloud.const import DATA_CLOUD
from homeassistant.components.http import HomeAssistantView


class JarvisCloudSpeechView(HomeAssistantView):
    url = "/api/jarvis_agent/tts"
    name = "api:jarvis_agent:tts"
    requires_auth = True

    def __init__(self, hass):
        self.hass = hass

    async def post(self, request):
        try:
            body = await request.json()
            message, language = body["message"], body["language"]
            voice = body["options"]["voice"]
            if (not isinstance(message, str) or not message.strip() or len(message) > 2048
                    or not isinstance(language, str) or language.split("-")[0] not in ("sv", "en")
                    or not isinstance(voice, str) or not voice):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            return web.json_response({"error": "invalid speech request"}, status=400)
        if (cloud := self.hass.data.get(DATA_CLOUD)) is None:
            return web.json_response({"error": "cloud unavailable"}, status=503)
        try:
            # Same account, voice and text as the Cloud TTS entity. RAW is natively 16 kHz/16-bit/mono.
            frames = await cloud.voice.process_tts(text=message, language=language, voice=voice, output=AudioOutput.RAW)
        except (VoiceError, ClientError, TimeoutError):
            return web.json_response({"error": "cloud speech failed"}, status=502)
        if not frames or len(frames) % 2:
            return web.json_response({"error": "invalid cloud audio"}, status=502)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wav:
            wav.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            wav.writeframes(frames)
        return web.Response(body=buf.getvalue(), content_type="audio/wav")
