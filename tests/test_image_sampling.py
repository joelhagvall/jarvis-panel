"""Exercise the actual Rust ABI with the buffers the image renderer passes."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('native_image_sampling', ROOT / 'styles/glass-v2/image_sampling.py')
sampling = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sampling)


def numpy_reference(image, sx, sy):
    height, width = image.shape[:2]
    sx, sy = np.clip(sx, 0, width - 1.001), np.clip(sy, 0, height - 1.001)
    x, y = np.floor(sx).astype(int), np.floor(sy).astype(int)
    fx, fy = (sx - x)[..., None], (sy - y)[..., None]
    top = image[y, x] * (1 - fx) + image[y, x + 1] * fx
    bottom = image[y + 1, x] * (1 - fx) + image[y + 1, x + 1] * fx
    return top * (1 - fy) + bottom * fy


class ImageSamplingTest(unittest.TestCase):
    def test_real_library_matches_numpy_for_fractional_and_clamped_coordinates(self):
        rng = np.random.default_rng(20261025)
        image = rng.random((6, 8, 3), dtype=np.float32)
        sx, sy = rng.uniform(-3, 11, (11, 9)), rng.uniform(-3, 9, (11, 9))
        actual = sampling.sample(image, sx, sy)
        np.testing.assert_allclose(actual, numpy_reference(image, sx, sy), rtol=0, atol=1e-15)
        self.assertEqual(sampling.backend()['name'], 'rust')
        self.assertEqual(sampling.backend()['abi'], 1)

    def test_unaligned_and_strided_inputs_are_copied_before_crossing_ffi(self):
        raw = bytearray(1 + 6 * 8 * 3 * 4)
        image = np.frombuffer(raw, dtype=np.float32, offset=1).reshape(6, 8, 3)
        image[:] = np.arange(144).reshape(6, 8, 3) / 144
        self.assertFalse(image.flags.aligned)
        sx = np.arange(24, dtype=np.float64).reshape(4, 6)[:, ::2] / 4
        sy = sx[::-1]
        np.testing.assert_allclose(sampling.sample(image, sx, sy), numpy_reference(image, sx, sy), rtol=0, atol=1e-15)

    def test_invalid_shapes_types_and_nonfinite_coordinates_fail(self):
        image = np.zeros((2, 2, 3), dtype=np.float32)
        for pixels, sx, sy, error in (
            (image, [0], [0, 1], ValueError),
            (image[:1], [0], [0], ValueError),
            (image.astype(np.float64), [0], [0], TypeError),
            (image, [np.nan], [0], ValueError),
            (image, [0], [np.inf], ValueError),
        ):
            with self.subTest(error=error, shape=pixels.shape):
                with self.assertRaises(error):
                    sampling.sample(pixels, sx, sy)

    def test_empty_coordinate_arrays_do_not_call_rust_with_zero_length_slices(self):
        actual = sampling.sample(np.zeros((2, 2, 3), dtype=np.float32), np.empty((0, 2)), np.empty((0, 2)))
        self.assertEqual(actual.shape, (0, 2, 3))

    def test_incompatible_abi_is_rejected_before_any_sampling_call(self):
        library = SimpleNamespace(hallpanel_sampling_abi=Mock(return_value=999))
        with patch.object(sampling.ctypes, 'CDLL', return_value=library):
            with self.assertRaisesRegex(RuntimeError, 'unsupported image sampling ABI'):
                sampling.RustSampler('/tmp/not-a-real-image-library.so')


if __name__ == '__main__':
    unittest.main()
