"""Mac Groq experiment with fake HTTP responses: no keys or external API calls.
Run with aiohttp installed: python -m unittest tests/test_groq_voice.py
"""
import asyncio
import importlib.util
import io
import json
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


tool = load("ha_tool", ROOT / "ha/ha_tool.py")
speech = load("speech", ROOT / "ha/custom_components/jarvis_agent/speech.py")


def wav(samples=b"\x01\x00" * 160):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(samples)
    return buf.getvalue()


class Response:
    def __init__(self, status=200, payload=None, audio=None, content=None):
        self.status, self.payload, self.audio, self.content = status, payload, audio, content

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        pass

    async def json(self):
        return self.payload

    async def read(self):
        return self.audio


class Session:
    def __init__(self, language="english", status=200, voice_status=200, truncated=False, text="What's tomorrow?"):
        self.language, self.status, self.voice_status, self.text = language, status, voice_status, text
        self.truncated, self.calls = truncated, []
        self.spoken = asyncio.Event()

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if url.endswith('/transcribe'):
            return Response(self.status, {"text": self.text, "language": self.language})
        if url.endswith("/converse"):
            return Response(content=self.reply())
        if url.endswith("/speak"):
            self.spoken.set()
            # A later sentence can finish synthesizing first, but must be played second.
            first = kwargs["json"]["text"] == "First sentence."
            async def read():
                if first:
                    await asyncio.sleep(0.01)
                return wav((b"\x01\x00" if first else b"\x02\x00") * 160)
            response = Response(self.voice_status)
            response.read = read
            return response
        raise AssertionError(url)

    async def reply(self):
        yield json.dumps({"delta": "First sentence. Second "}).encode() + b"\n"
        # Fail if the implementation waits for the whole model answer before starting TTS.
        await asyncio.wait_for(self.spoken.wait(), timeout=1)
        yield json.dumps({"delta": "sentence?"}).encode() + b"\n"
        if not self.truncated:
            yield json.dumps({"done": True, "ok": True, "reply": "First sentence. Second sentence?"}).encode() + b"\n"


class GroqVoiceTest(unittest.IsolatedAsyncioTestCase):
    async def run_voice(self, session):
        frames = []
        reader = asyncio.StreamReader()
        reader.feed_data(wav())
        reader.feed_eof()
        args = {"groq_key": "fake-groq", "jarvis_token": "fake-jarvis", "conversation_id": "mac-test"}
        with patch.object(tool, "speech_helpers", return_value=speech), patch.object(
                tool, "frame", side_effect=lambda kind, data: frames.append((kind, data))):
            await tool.ask_groq(session, args, reader)
        return frames, json.loads(frames[-1][1])

    async def test_automatic_language_streaming_audio_order_and_followup(self):
        for detected, code in (("english", "en"), ("swedish", "sv")):
            with self.subTest(language=detected):
                session = Session(language=detected)
                frames, result = await self.run_voice(session)
                self.assertTrue(result["ok"])
                self.assertTrue(result["continue"])
                self.assertEqual(result["language"], code)
                self.assertEqual(result["conversation_id"], "mac-test")
                conversation = next(kw["json"] for url, kw in session.calls if url.endswith("/converse"))
                self.assertEqual(conversation["language"], code)
                self.assertEqual(conversation["text"], "What's tomorrow?")
                spoken = [kw["json"] for url, kw in session.calls if url.endswith("/speak")]
                self.assertEqual([s["language"] for s in spoken], [code, code])
                audio = [data for kind, data in frames if kind == b"A"]
                self.assertEqual(len(audio), 2)
                self.assertTrue(audio[0].startswith(b"RIFF"))
                self.assertTrue(audio[0].endswith(b"\x01\x00" * 160))
                self.assertTrue(audio[1].endswith(b"\x02\x00" * 160))
                self.assertIsNotNone(result["timing"]["first_audio_s"])
                call = session.calls[0][1]
                self.assertTrue(call['data'].startswith(b'RIFF'))
                self.assertEqual(call['headers']['X-Hallpanel-Token'], 'fake-jarvis')
                self.assertFalse(any('api.groq.com' in url for url, _ in session.calls))

    async def test_rate_limit_stops_before_the_agent(self):
        session = Session(status=429)
        frames, result = await self.run_voice(session)
        self.assertFalse(result["ok"])
        self.assertIn("HTTP 429", result["error"]["message"])
        self.assertEqual(len(session.calls), 1)
        self.assertFalse(any(kind == b"A" for kind, _ in frames))
        self.assertNotIn("fake-groq", json.dumps(result))

    async def test_empty_transcript_never_calls_the_agent(self):
        session = Session(text="")
        _, result = await self.run_voice(session)
        self.assertFalse(result["ok"])
        self.assertEqual(result["said"], "")
        self.assertEqual(len(session.calls), 1)

    async def test_unknown_language_never_calls_the_agent(self):
        session = Session(language="german")
        _, result = await self.run_voice(session)
        self.assertFalse(result["ok"])
        self.assertIn("Swedish or English", result["error"]["message"])
        self.assertEqual(len(session.calls), 1)

    async def test_tts_failure_is_reported(self):
        _, result = await self.run_voice(Session(voice_status=502))
        self.assertFalse(result["ok"])
        self.assertIn("voice returned HTTP 502", result["error"]["message"])

    async def test_truncated_model_stream_is_reported(self):
        _, result = await self.run_voice(Session(truncated=True))
        self.assertFalse(result["ok"])
        self.assertIn("before the answer was complete", result["error"]["message"])


if __name__ == "__main__":
    unittest.main()
