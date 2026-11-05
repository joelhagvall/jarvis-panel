"""server/audio_health.py: the speaker's 5-minute audio sample is bounded, private and never fatal."""
import json
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server"))
import audio_health

TOP = """S   ID  QUANT   RATE    WAIT    BUSY   W/Q   B/Q  ERR FORMAT           NAME
R   32   2048  44100   5.5us  27.9us  0.00  0.00    0     S16P 2 44100 jbl
R   32   2048  44100   5.5us  27.9us  0.00  0.00    3     S16P 2 44100 jbl
R   47      0  44100  57.1us  14.2us  0.00  0.00    1    F32LE 2 44100  + spotify
"""


class AudioHealthTest(unittest.TestCase):
    def test_the_jbl_sinks_error_column_is_read(self):
        self.assertEqual(audio_health.sink_errors(TOP), 3)
        self.assertIsNone(audio_health.sink_errors(""))

    def test_soloist_reached_over_the_websocket_counts_as_running(self):
        out = "soloist: reachable\n  ws: listening on 127.0.0.1:9431\n  logged in: yes\n  active device: yes\n"
        with patch.object(audio_health, "_run", side_effect=[out, "soloist 1.3.10.22 build 1 (x)\n"]):
            self.assertEqual(audio_health.soloist(), {"running": True, "logged_in": True, "active": True,
                                                      "version": "soloist 1.3.10.22"})

    def test_the_log_keeps_the_newest_samples_privately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state/audio.jsonl"
            for i in range(5):
                audio_health.append({"time": i}, path, keep=3)
            self.assertEqual([json.loads(l)["time"] for l in path.read_text().splitlines()], [2, 3, 4])
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

    def test_a_failing_sample_is_logged_and_the_loop_goes_on(self):
        stop, said, calls = threading.Event(), [], []
        def sample(since, now=None):
            calls.append(since)
            if len(calls) == 1:
                raise OSError("pw-top missing")
            stop.set()
            return {"time": 1, "drops": 2, "sink_errors": 0, "speaker": True, "soloist": {"logged_in": True}}
        with tempfile.TemporaryDirectory() as tmp, patch.object(audio_health, "sample", sample):
            audio_health.run(stop, Path(tmp) / "audio.jsonl", every=0, log=said.append)
            self.assertTrue((Path(tmp) / "audio.jsonl").exists())
        self.assertIn("sample failed: OSError: pw-top missing", said[0])
        self.assertIn("2 BlueALSA drop(s)", said[1])

    def test_summary_counts_drops_per_hour(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "audio.jsonl"
            for t, d in ((1000, 0), (1300, 4), (1600, None)):
                audio_health.append({"time": t, "drops": d, "sink_errors": 0, "speaker": True,
                                     "soloist": {"logged_in": True}}, path)
            text = audio_health.summary(path, hours=1, now=2000)
        self.assertIn("3 samples", text)
        self.assertIn("drops 4 in all", text)


if __name__ == "__main__":
    unittest.main()
