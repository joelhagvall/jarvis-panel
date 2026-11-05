"""Intermediate PNGs that panel_image re-encodes use fast DEFLATE and keep their pixels.

Profiling on Jarvis (2026-10-26) showed optimize=True cost 2.5-3.3 s of a ~7 s cold pipeline for the
voice backdrop alone, with identical pixels at compress_level=1.
"""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
import voice_background  # noqa: E402


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class IntermediatePngTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old_gen = sys.modules.get('gen')
        old_path = sys.path[:]
        sys.modules['gen'] = load('gen', ROOT / 'styles/glass-v2/gen.py')
        try:
            cls.current = load('fast_png_bake', ROOT / 'styles/glass-v2/bake.py')
            cls.reference = load('fast_png_reference_bake', ROOT / 'tools/image_experiment/reference_bake.py')
        finally:
            sys.path[:] = old_path
            if old_gen is None:
                sys.modules.pop('gen', None)
            else:
                sys.modules['gen'] = old_gen

    def saves(self, call):
        """Run call and return the keyword arguments of every PIL save."""
        seen, real = [], Image.Image.save

        def spy(image, fp, format=None, **params):
            seen.append(params)
            return real(image, fp, format, **params)
        with mock.patch.object(Image.Image, 'save', spy):
            call()
        return seen

    def test_frame_and_voice_backdrop_skip_optimize_and_keep_pixels(self):
        source = ROOT / 'art/almond.jpg'
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            self.reference.frame(str(source), str(tmp / 'art_ref.png'))
            saves = self.saves(lambda: self.current.frame(str(source), str(tmp / 'art.png')))
            saves += self.saves(lambda: voice_background.make(tmp / 'art.png', tmp / 'voice.png'))
            self.assertEqual(len(saves), 2)
            for params in saves:
                self.assertNotIn('optimize', params)
                self.assertEqual(params.get('compress_level'), 1)
            with Image.open(tmp / 'art_ref.png') as a, Image.open(tmp / 'art.png') as b:
                np.testing.assert_array_equal(np.asarray(a), np.asarray(b))
            with Image.open(tmp / 'art.png') as art:
                glass = art.convert('RGB')
            from PIL import ImageFilter
            glass = glass.filter(ImageFilter.GaussianBlur(2)).point([round(v * 0.44 + 3) for v in range(256)] * 3)
            with Image.open(tmp / 'voice.png') as voice:
                np.testing.assert_array_equal(np.asarray(voice), np.asarray(glass))


if __name__ == '__main__':
    unittest.main()
