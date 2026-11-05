"""Recording boundaries with synthetic microphone chunks; no microphone, ssh or API calls."""
import contextlib
import io
import struct
import sys
import tempfile
import time
import unittest
import wave
from pathlib import Path
from unittest import mock

from tools import voice_loop as voice


class Mic:
    def __init__(self, sections):
        self.chunk_s = voice.CHUNK / voice.RATE
        self.chunks = iter(level for seconds, level in sections for _ in range(round(seconds / self.chunk_s)))
        self.index = 0

    def floor(self):
        return 0

    def get(self):
        level = next(self.chunks)
        self.index += 1
        return 1000 + self.index * self.chunk_s, int(level).to_bytes(2, "little", signed=True) * voice.CHUNK


class RecordingTest(unittest.TestCase):
    def test_thinking_pause_does_not_cut_the_question(self):
        mic = Mic([(0.3, 1000), (0.9, 0), (0.3, 1000), (2, 0)])
        pcm, ended = voice.record(mic, endpoint=voice.GROQ_SILENCE_END, max_speech=voice.GROQ_MAX_SPEECH)
        self.assertAlmostEqual(ended, 1001.5)
        self.assertGreater(len(pcm) / (voice.RATE * 2), 3)

    def test_question_can_continue_past_twenty_seconds(self):
        mic = Mic([(25, 1000), (2, 0)])
        pcm, ended = voice.record(mic, endpoint=voice.GROQ_SILENCE_END, max_speech=voice.GROQ_MAX_SPEECH)
        self.assertAlmostEqual(ended, 1025)
        self.assertGreater(len(pcm) / (voice.RATE * 2), 26)

    def test_custom_pause_waits_longer(self):
        mic = Mic([(0.3, 1000), (1.7, 0), (0.3, 1000), (2.2, 0)])
        _, ended = voice.record(mic, endpoint=2)
        self.assertAlmostEqual(ended, 1002.3)

    def test_time_limit_is_reported(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            pcm, _ = voice.record(Mic([(5, 1000)]), max_speech=3)
        duration = len(pcm) / (voice.RATE * 2)
        self.assertGreater(duration, 3)
        self.assertLessEqual(duration, 3 + 2 * voice.CHUNK / voice.RATE + 1e-6)
        self.assertIn("Maxlängden 3 s", output.getvalue())

    def test_chime_alone_is_not_speech(self):
        self.assertEqual(voice.record(Mic([(0.3, 1000), (5, 0)]), ignore_s=0.4), (None, None))


if __name__ == "__main__":
    unittest.main()


class PlayerTest(unittest.TestCase):
    def test_a_pause_in_the_reply_is_filled_with_silence_as_it_plays(self):
        """While Jarvis's tools run, ffplay gets silence at the pace it plays, not all at once."""
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "played.raw"
            copy = [sys.executable, "-c", f"import shutil, sys; shutil.copyfileobj(sys.stdin.buffer, open({str(out)!r}, 'wb'))"]
            real = voice.subprocess.Popen
            header = io.BytesIO()
            with wave.open(header, "wb") as w:
                w.setnchannels(1), w.setsampwidth(2), w.setframerate(16000)
            sentence = bytes(16000 * 2 // 5)                      # 0.2 s
            with mock.patch.object(voice.subprocess, "Popen", lambda cmd, **kw: real(copy, **kw)):
                player = voice.Player(header.getvalue() + sentence)
                time.sleep(1.0)                                   # the tools run
                player.write(sentence)
                player.close()
            played = out.stat().st_size / (16000 * 2)
            self.assertEqual(struct.unpack("<I", header.getvalue()[24:28])[0], 16000)
            self.assertGreater(played, 1.0)                       # the pause was filled
            self.assertLess(played, 1.0 + 0.2 + 0.3)              # but never far ahead of the clock
