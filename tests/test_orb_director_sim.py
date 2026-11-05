"""tools/orb_director_sim.py runs the director on a reply's loudness without the panel."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import wave

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++ compiler unavailable")
class OrbDirectorSimTest(unittest.TestCase):
    def test_a_spoken_reply_never_cross_fades(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as tmp:
            reply = Path(tmp) / 'reply.wav'
            t = np.arange(16000 * 3) / 16000
            speech = (0.25 * np.sin(2 * np.pi * 220 * t) * (np.sin(2 * np.pi * 3 * t) > 0)).astype(np.float32)
            speech[16000:20000] = 0                       # a sentence break
            with wave.open(str(reply), 'wb') as w:
                w.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
                w.writeframes((speech * 32767).astype('<i2').tobytes())
            sys.path.insert(0, str(ROOT / 'tools'))
            import orb_director_sim
            result = orb_director_sim.run(reply)
        self.assertEqual([name for _, name in result['states']], ['think', 'swing', 'speak', 'hush', 'settle'])
        self.assertEqual(result['cross_fading_frames'], 0)
        self.assertLessEqual(result['largest_height_rise'], 0.14 + 1e-6)


if __name__ == '__main__':
    unittest.main()
