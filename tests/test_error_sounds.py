"""A failed voice turn is spoken, not only shown (Joel, 2026-11-03: "Jag uppfattade inte det." was text only and the
silent wake felt like nothing happened). The panel plays the reason from its own flash, picked like the text."""
import json
import subprocess
import sys
import unittest
import wave
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from board_config.voice import ERROR_SOUNDS  # noqa: E402


class ErrorSoundsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        out = subprocess.run([sys.executable, str(ROOT / "board.py"), "--voice", "jarvis_orb.py", "--remote",
                              "http://example.invalid/assets"], cwd=ROOT / "styles/glass-v2",
                             check=True, capture_output=True, text=True).stdout
        cls.cfg = json.loads(out)

    def test_every_error_sound_is_a_panel_format_file_on_the_panel(self):
        files = {f["id"]: f["file"]["path"] for f in self.cfg["media_player"][0]["files"]}
        for name, (filename, _) in ERROR_SOUNDS.items():
            self.assertEqual(Path(files[name]).name, filename)
            with wave.open(str(ROOT / "assets" / "sounds" / filename)) as w:
                self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2), filename)
                self.assertGreater(w.getnframes() / 16000, 0.5, filename)        # a phrase, not a blip

    def test_on_error_speaks_the_reason_it_shows(self):
        on_error = json.dumps(self.cfg["voice_assistant"]["on_error"], ensure_ascii=False)
        for name, (_, text) in ERROR_SOUNDS.items():
            self.assertIn(f'"media_file": "{name}"', on_error)
            self.assertIn(text, on_error)
        # the sound comes before voice_done, whose wait for the announcement keeps the voice page up while it plays
        self.assertLess(on_error.index('"media_file": "error_missed"'), on_error.index('"script.execute": "voice_done"'))


if __name__ == "__main__":
    unittest.main()
