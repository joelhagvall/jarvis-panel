#!/usr/bin/env python3
"""server/stage.py keeps built orb atlases per machine; a kept atlas must equal a fresh build exactly."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import stage  # noqa: E402


class StageCacheTest(unittest.TestCase):
    def test_key_covers_atlas_code_frames_and_libraries(self):
        import PIL
        base = stage.atlas_key()
        real = Path.read_bytes
        frame = sorted((ROOT / 'orbs').glob('glass-jarvis*/*.png'))[0]
        for changed in (Path(stage.atlas.__file__), frame):
            with self.subTest(path=changed.name), mock.patch.object(
                    Path, 'read_bytes', lambda self, changed=changed: real(self) + b'#' if self == changed else real(self)):
                self.assertNotEqual(stage.atlas_key(), base)
        with mock.patch.object(PIL, '__version__', 'other'):
            self.assertNotEqual(stage.atlas_key(), base)
        with mock.patch.object(sys, 'byteorder', 'big'):
            self.assertNotEqual(stage.atlas_key(), base)

    def test_atlas_is_built_once_per_key_and_copied_exactly(self):
        calls = []

        def build(path):
            calls.append(path)
            path.write_bytes(b'atlas-bytes')
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(stage, 'ATLAS_CACHE', str(Path(tmp) / 'cache')):
            for name in ('one', 'two'):
                out = Path(tmp) / name
                out.mkdir()
                stage.cached(out / 'orb_atlas_v5.lz4', build, 'key')
                self.assertEqual((out / 'orb_atlas_v5.lz4').read_bytes(), b'atlas-bytes')
            self.assertEqual(len(calls), 1)
            stage.cached(Path(tmp) / 'one' / 'orb_atlas_v5.lz4', build, 'other-key')
            self.assertEqual(len(calls), 2)

    def test_empty_setting_always_builds(self):
        calls = []
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(stage, 'ATLAS_CACHE', ''):
            for _ in range(2):
                stage.cached(Path(tmp) / 'a.lz4', lambda path: (calls.append(path), path.write_bytes(b'x')), 'key')
        self.assertEqual(len(calls), 2)


if __name__ == '__main__':
    unittest.main()
