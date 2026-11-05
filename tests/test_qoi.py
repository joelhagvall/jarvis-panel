"""The native RGB encoder must retain Pillow's wire format and decoded pixels."""
import importlib.util
import io
from pathlib import Path
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('qoi_sampling', ROOT / 'styles/glass-v2/image_sampling.py')
sampling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sampling)


class QoiTest(unittest.TestCase):
    def assert_encoded(self, pixels):
        expected = io.BytesIO()
        Image.fromarray(pixels).save(expected, format='QOI')
        actual = sampling.encode_qoi_rgb(pixels)
        self.assertEqual(actual, expected.getvalue())
        with Image.open(io.BytesIO(actual)) as decoded:
            np.testing.assert_array_equal(np.asarray(decoded), pixels)

    def test_run_boundaries_and_one_pixel_images(self):
        for count in (1, 2, 61, 62, 63, 123, 124, 125):
            with self.subTest(count=count):
                self.assert_encoded(np.zeros((1, count, 3), dtype=np.uint8))
                self.assert_encoded(np.full((count, 1, 3), 255, dtype=np.uint8))

    def test_delta_wrap_index_collisions_and_rgb_opcodes(self):
        pixels = np.array([[0, 0, 0], [255, 255, 255], [1, 1, 1], [5, 5, 5],
                           [200, 100, 50], [1, 1, 1], [1, 1, 1], [0, 255, 0],
                           [255, 0, 255], [128, 0, 128], [0, 128, 0]], dtype=np.uint8)
        self.assert_encoded(pixels[None])
        rng = np.random.default_rng(20261025)
        for shape in ((3, 9, 3), (64, 256, 3), (17, 31, 3)):
            self.assert_encoded(rng.integers(0, 256, shape, dtype=np.uint8))

    def test_strided_image_and_all_channel_values(self):
        pixels = np.empty((3, 256, 3), dtype=np.uint8)
        pixels[:] = np.arange(256, dtype=np.uint8)[None, :, None]
        pixels[1, :, 1] = pixels[1, ::-1, 1]
        self.assert_encoded(pixels[:, ::-1])

    def test_rejects_empty_non_rgb_and_non_byte_input(self):
        for shape, dtype, error in (((0, 2, 3), np.uint8, ValueError),
                                    ((2, 2, 4), np.uint8, ValueError),
                                    ((2, 2, 3), np.float32, TypeError)):
            with self.subTest(shape=shape):
                with self.assertRaises(error):
                    sampling.encode_qoi_rgb(np.zeros(shape, dtype=dtype))


if __name__ == '__main__':
    unittest.main()
