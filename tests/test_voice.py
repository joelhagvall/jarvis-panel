#!/usr/bin/env python3
"""The voice (server/voice.py): without the internet, when Home Assistant Cloud does not answer, Piper reads the
text, the cloud is skipped for a minute, and Piper's 22.05 kHz becomes 16 kHz (Piper itself is faked here); the
finished sentences Jarvis says often come from a cache; and the AI tone in numpy gives what ffmpeg's filter chain
gave within the tested PCM tolerance, where ffmpeg is installed to compare with. No cloud calls.
Run: python3 -m unittest tests/test_voice.py"""
import array
import io
import json
import math
import os
import random
import shutil
import socket
import sys
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import voice  # noqa: E402
import hass  # noqa: E402  (voice puts server/agents on the path)


def wav16k(seconds=0.5):
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(bytes(int(16000 * seconds) * 2))
    return buf.getvalue()


class VoiceFallbackTest(unittest.TestCase):
    def setUp(self):
        with socket.socket() as s:                   # a port nobody listens on: the cloud is "down"
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        tmp = Path(tempfile.mkdtemp(prefix="hallpanel-voice-"))
        (tmp / "token").write_text("t\n")
        self.saved = os.environ.get("HA_TOKEN_FILE"), voice.piper_tts, voice.cloud_tts
        hass.URL, os.environ["HA_TOKEN_FILE"] = f"http://127.0.0.1:{port}", str(tmp / "token")
        hass.reset()
        voice._cloud_down_until[0] = 0.0
        self.addCleanup(self.restore)

    def restore(self):
        env, voice.piper_tts, voice.cloud_tts = self.saved
        hass.URL = None
        if env is None:
            os.environ.pop("HA_TOKEN_FILE", None)
        else:
            os.environ["HA_TOKEN_FILE"] = env
        voice._cloud_down_until[0] = 0.0

    def test_piper_when_the_cloud_is_down_then_cloud_skipped(self):
        cloud_calls, piper_calls = [], []
        real_cloud = voice.cloud_tts
        voice.cloud_tts = lambda text: cloud_calls.append(text) or real_cloud(text)
        voice.piper_tts = lambda text: piper_calls.append(text) or wav16k()
        self.assertEqual(voice.tts("Nätet är nere."), wav16k())
        self.assertEqual(voice.tts("Larmen fungerar."), wav16k())
        self.assertEqual(cloud_calls, ["Nätet är nere."])         # the second sentence skips the cloud
        self.assertEqual(piper_calls, ["Nätet är nere.", "Larmen fungerar."])

    def test_no_piper_is_an_error(self):
        voice.piper_tts = lambda text: None
        with self.assertRaises(OSError):
            voice.tts("Hej.")

    def test_resample_to_16k(self):
        ramp = array.array("h", range(22050)).tobytes()                      # one second at 22.05 kHz
        out = array.array("h", voice.resample(ramp, 22050, 16000))
        self.assertEqual(len(out), 16000)
        self.assertEqual(out[0], 0)
        self.assertAlmostEqual(out[8000], 11025, delta=2)                    # half a second in, half the ramp
        self.assertEqual(voice.resample(b"\x01\x00", 16000, 16000), b"\x01\x00")

    def test_missing_voice_file_means_no_piper(self):
        saved = voice.PIPER_VOICE
        voice.PIPER_VOICE = Path("/nonexistent/voice.onnx")
        self.addCleanup(setattr, voice, "PIPER_VOICE", saved)
        if not voice._piper:
            self.assertIsNone(voice.load_piper())

    def test_the_swedish_voice_never_says_charge(self):
        # Joel, 2026-10-28: "JBL Charge 4" read in Swedish sounded ugly; English may keep it.
        # 2026-10-30: just "högtalaren", never "JBL:en" or "JBL-högtalaren".
        for text, said in (("Spelar P3 på JBL Charge 4.", "Spelar P3 på högtalaren."),
                           ("Kopplad till JBL Charge 4", "Kopplad till högtalaren"),
                           ("JBL Charge 4:s volym är 40 procent.", "Högtalarens volym är 40 procent."),
                           ("Charge 4 är ansluten.", "Högtalaren är ansluten."),
                           ("jbl-charge 4 och JBL:en", "Högtalaren och högtalaren"),
                           ("Spelar Cocktails på JBL:en.", "Spelar Cocktails på högtalaren."),
                           ("JBL:en står på 40 procent. JBL-högtalarens batteri är fullt.",
                            "Högtalaren står på 40 procent. Högtalarens batteri är fullt."),
                           ("Det går på JBL-högtalaren nu", "Det går på högtalaren nu"),
                           ("Jag kopplade JBL:ens ljud", "Jag kopplade högtalarens ljud"),
                           ("Du har en JBL-högtalare.", "Du har en högtalare."),
                           ("Volymen på JBL är 30.", "Volymen på högtalaren är 30."),
                           ("Bluetooth-högtalaren JBL Charge 4 är kopplad.", "Bluetooth-högtalaren är kopplad."),
                           ("Laddningen (charge) är okej.", "Laddningen (charge) är okej.")):
            with self.subTest(text=text):
                self.assertEqual(voice.spoken_names(text), said)

    def test_english_uses_ryan_and_swedish_still_uses_mattias(self):
        requests = []

        def request(path, data=None, **kwargs):
            self.assertEqual(path, "/api/jarvis_agent/tts")
            requests.append(json.loads(data))
            return wav16k()

        with patch.object(voice, "_request", side_effect=request):
            voice.tts("Good morning.", language="en")
            voice.tts("God morgon.", language="sv")
        self.assertEqual([(r["language"], r["options"]["voice"]) for r in requests],
                         [("en-GB", "RyanNeural"), ("sv-SE", "MattiasNeural")])

    def test_english_cloud_failure_never_uses_swedish_piper(self):
        with patch.object(voice, "cloud_tts", side_effect=OSError("offline")), patch.object(voice, "piper_tts") as piper:
            with self.assertRaises(OSError):
                voice.tts("Hello.", language="en")
            piper.assert_not_called()

    @staticmethod
    def cloud(requests):
        """A fake Home Assistant for cloud_tts: notes the text and answers with half a second of silence."""
        def request(path, data=None, **kwargs):
            if path != "/api/jarvis_agent/tts":
                raise AssertionError("unexpected voice request")
            requests.append(json.loads(data)["message"])
            return wav16k()
        return request

    def test_speech_caches_the_cloud_voice_but_not_piper(self):
        requests = []
        voice._speech_cache.clear()
        self.addCleanup(voice._speech_cache.clear)
        with patch.object(voice, "_request", side_effect=self.cloud(requests)), patch.object(voice, "EFFECT", "none"):
            first, source = voice.speech("God morgon!")
            self.assertEqual(source, "cloud")
            self.assertEqual(voice.speech("God morgon!"), (first, "cache"))
            self.assertEqual(voice.speech("Good morning!", language="en")[1], "cloud")   # its own key
            self.assertEqual(requests, ["God morgon!", "Good morning!"])
        # the cloud is down (setUp's dead port): Piper reads, and that voice is not kept
        piper_calls = []
        voice.piper_tts = lambda text: piper_calls.append(text) or wav16k()
        with patch.object(voice, "EFFECT", "none"):
            self.assertEqual(voice.speech("Nätet är nere.")[1], "piper")
            self.assertEqual(voice.speech("Nätet är nere.")[1], "piper")
        self.assertEqual(piper_calls, ["Nätet är nere."] * 2)

    def test_speech_cache_keeps_the_latest_sentences_only(self):
        voice._speech_cache.clear()
        self.addCleanup(voice._speech_cache.clear)
        with patch.object(voice, "_request", side_effect=self.cloud([])), patch.object(voice, "EFFECT", "none"), \
                patch.object(voice, "SPEECH_CACHE", 2):
            for text in ("Ett.", "Två.", "Tre."):
                voice.speech(text)
            self.assertEqual(voice.speech("Tre.")[1], "cache")
            self.assertEqual(voice.speech("Ett.")[1], "cloud")                            # the oldest went


def speechlike(seconds, peak, seed=7):
    """A deterministic voice-like signal: bursts of harmonics with pauses, as 16-bit samples."""
    rnd = random.Random(seed)
    out = array.array("h")
    t = 0.0
    while t < seconds:
        if rnd.random() < 0.3:
            dur = 0.2 + rnd.random() * 0.4
            out.extend([0] * int(16000 * dur))
        else:
            dur = 0.15 + rnd.random() * 0.4
            f0 = 90 + rnd.random() * 80
            n = int(16000 * dur)
            amp = peak * (0.5 + 0.5 * rnd.random())
            for i in range(n):
                env = min(1.0, i / 300, (n - i) / 300)
                s = sum(math.sin(2 * math.pi * f0 * k * i / 16000 + k) / k for k in range(1, 9))
                out.append(int(max(-32768, min(32767, round(32768 * amp * env * s / 1.6)))))
        t += dur
    return out.tobytes()


class AiToneTest(unittest.TestCase):
    """The AI tone in numpy (voice._tone_numpy) against ffmpeg's filter chain and on its own."""

    def setUp(self):
        if voice.np is None:
            self.skipTest("no numpy")
        for name, value in (("EFFECT", "ai"), ("TONE", "numpy")):
            self.addCleanup(setattr, voice, name, getattr(voice, name))
            setattr(voice, name, value)

    def test_impulse_gets_the_direct_sound_a_flanged_copy_and_two_echoes(self):
        frames = array.array("h", [0] * 16000)
        frames[1600] = 16384
        out = array.array("h", voice._tone_numpy(frames.tobytes(), 16000))
        self.assertEqual(len(out), 16000 + 1120)                                 # the echo's 70 ms tail
        direct = 1600 + 79                                                       # the limiter's 5 ms lookahead
        self.assertEqual(set(out[:direct]), {0})
        # 0.5 × 2/3 (flanger) × 0.8 × 0.5 (echo) × 9.6 dB × 1/0.89 (the limiter's level)
        gain = 1 / 1.5 * 0.8 * 0.5 * 10 ** (9.6 / 20) / 0.89
        self.assertAlmostEqual(out[direct], 16384 * gain, delta=2)
        self.assertAlmostEqual(out[direct + 32] + out[direct + 33], 16384 * gain * (1 / 1.5 * 0.5 * 0.8) / (1 / 1.5), delta=4)
        self.assertAlmostEqual(out[direct + 560], 16384 * gain * 0.2 / 0.8, delta=2)
        self.assertAlmostEqual(out[direct + 1120], 16384 * gain * 0.12 / 0.8, delta=2)

    @unittest.skipUnless(shutil.which("ffmpeg"), "no ffmpeg to compare with")
    def test_matches_ffmpeg_to_the_sample(self):
        with wave.open(str(ROOT / "assets" / "sounds" / "listen.wav")) as w:
            chime = array.array("h", w.readframes(w.getnframes()))
        loud = array.array("h", (max(-32768, min(32767, 3 * s)) for s in chime))   # transients over the limit
        for name, frames in (("quiet speech", speechlike(2.0, 0.45)), ("loud speech", speechlike(2.0, 1.0, seed=3)),
                             ("chime", chime.tobytes()), ("loud chime", loud.tobytes())):
            with self.subTest(name):
                want = array.array("h", voice._tone_ffmpeg(frames, 16000, 1))
                got = array.array("h", voice._tone_numpy(frames, 16000))
                self.assertEqual(len(got), len(want))
                diff = [abs(a - b) for a, b in zip(want, got)]
                self.assertLessEqual(max(diff), 8)                                 # single samples at a limiter breakpoint
                self.assertLessEqual(sum(1 for d in diff if d), len(diff) // 1000 + 1)

    def test_ai_tone_uses_numpy_and_keeps_the_format(self):
        wav = wav16k(1.0)
        with patch.object(voice, "_tone_ffmpeg", side_effect=AssertionError("ffmpeg was run")):
            with wave.open(io.BytesIO(voice.ai_tone(wav))) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()), (16000, 1, 2, 16000 + 1120))
        voice.TONE = "ffmpeg"
        with patch.object(voice, "_tone_ffmpeg", return_value=b"\x00\x00" * 160) as ffmpeg:
            self.assertEqual(voice.duration(voice.ai_tone(wav)), 0.01)
            ffmpeg.assert_called_once()


class TrimTest(unittest.TestCase):
    def test_numpy_and_reference_trim_are_byte_identical_at_boundaries_and_partial_windows(self):
        for rate, channels in ((16000, 1), (24000, 1), (16000, 2)):
            for frames in (b"", b"\x00\x00" * 6401, b"\x00\x80" * 199,
                           speechlike(2.0, 0.45) + b"\x00\x00" * 137,
                           b"\x00\x00" * 16000 + speechlike(0.5, 0.8) + b"\x00\x00" * 12000):
                if channels == 2 and len(frames) % 4:
                    frames += b"\x00\x00"
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wav:
                    wav.setparams((channels, 2, rate, 0, "NONE", "not compressed"))
                    wav.writeframes(frames)
                candidate = voice.trim(buf.getvalue())
                with patch.object(voice, "np", None):
                    reference = voice.trim(buf.getvalue())
                self.assertEqual(candidate, reference, (rate, channels, len(frames)))


if __name__ == "__main__":
    unittest.main()
