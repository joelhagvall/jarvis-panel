"""Compare native blur to the original NumPy operations, including per-tap rounding."""
import importlib.util
from pathlib import Path
import unittest

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('blur_sampling', ROOT / 'styles/glass-v2/image_sampling.py')
sampling = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sampling)


def numpy_reference(image, kernel):
    output = image
    radius = len(kernel) // 2
    for axis in (0, 1):
        padding = [(0, 0)] * image.ndim
        padding[axis] = (radius, radius)
        padded = np.pad(output, padding, mode='edge')
        result = np.zeros_like(output)
        for index, weight in enumerate(kernel):
            region = [slice(None)] * image.ndim
            region[axis] = slice(index, index + output.shape[axis])
            result += weight * padded[tuple(region)]
        output = result
    return output


class BlurTest(unittest.TestCase):
    def test_matches_numpy_bits_for_both_precisions_edges_and_large_kernels(self):
        rng = np.random.default_rng(20261025)
        for dtype in (np.float32, np.float64):
            # 67 and 130 rows cross the native threading threshold (64 rows).
            for shape in ((1, 1), (1, 7, 3), (9, 1, 3), (7, 11), (13, 17, 3), (67, 5), (130, 19, 3)):
                image = rng.normal(size=shape).astype(dtype)
                for sigma in (0.5, 3, 12, 28):
                    radius = max(1, int(3 * sigma + .5))
                    kernel = np.exp(-np.arange(-radius, radius + 1) ** 2 / (2 * sigma * sigma))
                    kernel /= kernel.sum()
                    with self.subTest(dtype=dtype, shape=shape, sigma=sigma):
                        np.testing.assert_array_equal(sampling.gaussian_blur(image, kernel), numpy_reference(image, kernel))

    def test_strided_and_unaligned_inputs_retain_their_values(self):
        storage = bytearray(1 + 5 * 7 * 8)
        image = np.frombuffer(storage, dtype=np.float64, offset=1).reshape(5, 7)
        image[:] = np.arange(35).reshape(5, 7) / 35
        kernel = np.array([.25, .5, .25])
        for source in (image, image[::-1, ::2]):
            np.testing.assert_array_equal(sampling.gaussian_blur(source, kernel), numpy_reference(source, kernel))

    def test_invalid_shapes_precisions_and_weights_are_rejected(self):
        image = np.zeros((2, 2), dtype=np.float64)
        for source, kernel, error in ((image, [], ValueError), (image, [1, 1], ValueError),
            (image, [np.nan], ValueError), (image[:0], [1], ValueError),
            (image.astype(np.int32), [1], TypeError)):
            with self.subTest(error=error, shape=source.shape):
                with self.assertRaises(error):
                    sampling.gaussian_blur(source, kernel)


if __name__ == '__main__':
    unittest.main()
