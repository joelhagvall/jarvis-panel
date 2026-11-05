"""Bounded glass fields must retain the full-frame renderer's approved pixels."""
import hashlib
import importlib.util
import os
from pathlib import Path
import platform
import sys
import tempfile
import unittest
import unittest.mock

import numpy as np
import PIL
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
# The frozen reference renderer is slow (most of the 27 s native deploy stage on Jarvis) and its output is a
# pure function of these inputs, so its pixels are kept per machine. An empty value turns the cache off.
REFERENCE_CACHE = os.environ.get('HALLPANEL_REFERENCE_CACHE', str(Path.home() / '.cache/hallpanel/reference-bake'))


def reference_key(source):
    digest = hashlib.sha256(b'reference-bake-v1\0')
    for path in (ROOT / 'tools/image_experiment/reference_bake.py', ROOT / 'styles/glass-v2/gen.py', source):
        digest.update(hashlib.sha256(Path(path).read_bytes()).digest())
    for part in (np.__version__, PIL.__version__, sys.version, platform.machine(), platform.system()):
        digest.update(part.encode() + b'\0')
    return digest.hexdigest()


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class BakeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        old_gen = sys.modules.get('gen')
        old_path = sys.path[:]
        sys.modules['gen'] = load('gen', ROOT / 'styles/glass-v2/gen.py')
        try:
            cls.current = load('bounded_bake', ROOT / 'styles/glass-v2/bake.py')
            cls.reference = load('reference_bake', ROOT / 'tools/image_experiment/reference_bake.py')
        finally:
            sys.path[:] = old_path
            if old_gen is None:
                sys.modules.pop('gen', None)
            else:
                sys.modules['gen'] = old_gen

    def reference_pixels(self, source, scratch):
        cached = Path(REFERENCE_CACHE) / f'{reference_key(source)}.npy' if REFERENCE_CACHE else None
        if cached is not None and cached.is_file():
            pixels = np.load(cached, allow_pickle=False)
            if pixels.dtype == np.uint8 and pixels.shape == (600, 1024, 3):
                return pixels
        self.reference.bake(str(source), str(scratch))
        with Image.open(scratch) as image:
            pixels = np.asarray(image).copy()
        if cached is not None:
            cached.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            partial = cached.with_name(f'.{cached.name}.{os.getpid()}.tmp')
            with partial.open('wb') as out:
                np.save(out, pixels, allow_pickle=False)
            os.replace(partial, cached)
        return pixels

    def test_reference_cache_key_covers_every_input_of_the_oracle(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'a.png'
            source.write_bytes(b'one')
            base = reference_key(source)
            for target, name in ((np, '__version__'), (PIL, '__version__'), (sys, 'version')):
                with self.subTest(name=f'{target.__name__}.{name}'), unittest.mock.patch.object(target, name, 'other'):
                    self.assertNotEqual(reference_key(source), base)
            for name in ('machine', 'system'):
                with self.subTest(name=name), unittest.mock.patch.object(platform, name, return_value='other'):
                    self.assertNotEqual(reference_key(source), base)
            for path in (ROOT / 'tools/image_experiment/reference_bake.py', ROOT / 'styles/glass-v2/gen.py'):
                real = Path.read_bytes
                changed = lambda self, path=path: real(self) + b'#' if self == path else real(self)
                with self.subTest(path=path.name), unittest.mock.patch.object(Path, 'read_bytes', changed):
                    self.assertNotEqual(reference_key(source), base)
            source.write_bytes(b'two')
            self.assertNotEqual(reference_key(source), base)

    def test_cached_reference_is_rendered_once_and_returned_exactly(self):
        pixels = np.random.default_rng(1).integers(0, 256, (600, 1024, 3), dtype=np.uint8)
        calls = []

        def render(source, out):
            calls.append(source)
            Image.fromarray(pixels).save(out)
        with tempfile.TemporaryDirectory() as tmp, \
                unittest.mock.patch(f'{__name__}.REFERENCE_CACHE', str(Path(tmp) / 'cache')), \
                unittest.mock.patch.object(self.reference, 'bake', render):
            source = ROOT / 'art/almond.jpg'
            for name in ('one.png', 'two.png'):
                np.testing.assert_array_equal(self.reference_pixels(source, Path(tmp) / name), pixels)
            self.assertEqual(len(calls), 1)
            # A damaged entry is rendered again, never trusted.
            np.save(Path(tmp) / 'cache' / f'{reference_key(source)}.npy', pixels[:10])
            np.testing.assert_array_equal(self.reference_pixels(source, Path(tmp) / 'three.png'), pixels)
            self.assertEqual(len(calls), 2)

    def test_bounded_fields_equal_full_fields_including_offscreen_clipping(self):
        bake = self.current
        shapes = list(bake.RECTS.values()) + [(-10, -20, 150, 100, 'squircle', 'dark')]
        for x, y, width, height, kind, _ in shapes:
            with self.subTest(shape=(x, y, width, height, kind)):
                bounds = max(0, x - 64), max(0, y - 64), min(bake.W, x + width + 64), min(bake.H, y + height + 64)
                left, top, right, bottom = bounds
                full = self.reference.shape_field(x, y, width, height, kind)
                bounded = bake.shape_field(x, y, width, height, kind, bounds)
                for expected, actual in zip(full, bounded):
                    np.testing.assert_array_equal(actual, expected[top:bottom, left:right])

    def test_bright_dark_and_textured_backgrounds_keep_every_source_pixel(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            y, x = np.mgrid[:240, :320]
            pattern = np.stack(((x * 17 + y * 7) % 256, (x * 3 + y * 29) % 256,
                                (x * 13 + y * 11) % 256), axis=2).astype(np.uint8)
            Image.fromarray(pattern).save(tmp / 'pattern.png')
            Image.new('RGB', (320, 240), (0, 0, 0)).save(tmp / 'black.png')
            Image.new('RGB', (320, 240), (255, 255, 255)).save(tmp / 'white.png')
            for source in (tmp / 'pattern.png', tmp / 'black.png', tmp / 'white.png', ROOT / 'art/almond.jpg'):
                with self.subTest(image=source.name):
                    actual = tmp / 'actual.png'
                    reference = self.reference_pixels(source, tmp / 'expected.png')
                    self.current.bake(str(source), str(actual))
                    with Image.open(actual) as image:
                        np.testing.assert_array_equal(np.asarray(image), reference)


if __name__ == '__main__':
    unittest.main()
