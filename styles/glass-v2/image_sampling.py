"""Checked ctypes boundary to the Rust image sampler built by tools/check.py."""
import ctypes
from pathlib import Path
import sys

import numpy as np


class RustSampler:
    def __init__(self, library):
        self.path = Path(library).resolve()
        self.library = ctypes.CDLL(str(self.path))
        abi = self.library.hallpanel_sampling_abi
        abi.argtypes, abi.restype = [], ctypes.c_uint32
        if abi() != 1:
            raise RuntimeError('unsupported image sampling ABI')
        self.call = self.library.hallpanel_sample
        f32 = np.ctypeslib.ndpointer(dtype=np.float32, flags=('C_CONTIGUOUS', 'ALIGNED'))
        f64 = np.ctypeslib.ndpointer(dtype=np.float64, flags=('C_CONTIGUOUS', 'ALIGNED'))
        self.call.argtypes = [f32, ctypes.c_size_t, ctypes.c_size_t, f64, f64, ctypes.c_size_t, f64]
        self.call.restype = ctypes.c_int

        qoi_abi = self.library.hallpanel_qoi_abi
        qoi_abi.argtypes, qoi_abi.restype = [], ctypes.c_uint32
        if qoi_abi() != 1:
            raise RuntimeError('unsupported QOI encoding ABI')
        u8 = np.ctypeslib.ndpointer(dtype=np.uint8, flags=('C_CONTIGUOUS', 'ALIGNED'))
        self.qoi_call = self.library.hallpanel_qoi_rgb
        self.qoi_call.argtypes = [u8, ctypes.c_size_t, ctypes.c_uint32, ctypes.c_uint32,
                                 u8, ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]
        self.qoi_call.restype = ctypes.c_int

        blur_abi = self.library.hallpanel_blur_abi
        blur_abi.argtypes, blur_abi.restype = [], ctypes.c_uint32
        if blur_abi() != 1:
            raise RuntimeError('unsupported Gaussian blur ABI')
        self.blur_calls = {}
        for dtype, pointer, name in ((np.float32, f32, 'hallpanel_blur_f32'),
                                     (np.float64, f64, 'hallpanel_blur_f64')):
            call = getattr(self.library, name)
            call.argtypes = [pointer, ctypes.c_size_t, ctypes.c_size_t, ctypes.c_size_t,
                             f64, ctypes.c_size_t, pointer, pointer]
            call.restype = ctypes.c_int
            self.blur_calls[np.dtype(dtype)] = call

    def gaussian_blur(self, image, weights):
        image = np.asarray(image)
        weights = np.asarray(weights, dtype=np.float64)
        if image.ndim not in (2, 3) or min(image.shape) < 1:
            raise ValueError('Gaussian blur requires a nonempty 2D or 3D array')
        if image.dtype not in self.blur_calls:
            raise TypeError('Gaussian blur requires float32 or float64 values')
        if weights.ndim != 1 or weights.size == 0 or weights.size % 2 == 0:
            raise ValueError('Gaussian blur requires a nonempty odd-length kernel')
        image = np.require(image, requirements=['C', 'A'])
        weights = np.require(weights, requirements=['C', 'A'])
        scratch, output = np.empty_like(image), np.empty_like(image)
        height, width = image.shape[:2]
        channels = image.shape[2] if image.ndim == 3 else 1
        status = self.blur_calls[image.dtype](image, width, height, channels, weights, weights.size, scratch, output)
        if status:
            raise ValueError(f'Rust Gaussian blur rejected input: {status}')
        return output

    def encode_qoi_rgb(self, pixels):
        pixels = np.asarray(pixels)
        if pixels.ndim != 3 or pixels.shape[2] != 3 or min(pixels.shape[:2]) < 1:
            raise ValueError('QOI encoding requires a nonempty RGB image')
        if pixels.dtype != np.uint8:
            raise TypeError('QOI encoding requires uint8 pixels')
        height, width = pixels.shape[:2]
        if max(width, height) > 0xffffffff:
            raise ValueError('QOI dimensions exceed uint32')
        pixels = np.require(pixels, requirements=['C', 'A'])
        output = np.empty(height * width * 4 + 22, dtype=np.uint8)
        written = ctypes.c_size_t()
        status = self.qoi_call(pixels, pixels.size, width, height, output, output.size, ctypes.byref(written))
        if status:
            raise ValueError(f'Rust QOI encoder rejected input: {status}')
        return output[:written.value].tobytes()

    def sample(self, image, sx, sy):
        """Sample borrowed arrays; callers must not mutate them during the call."""
        image = np.asarray(image)
        sx, sy = np.asarray(sx, dtype=np.float64), np.asarray(sy, dtype=np.float64)
        if image.ndim != 3 or image.shape[2] != 3 or min(image.shape[:2]) < 2:
            raise ValueError('sampling requires an RGB image at least 2 by 2')
        if image.dtype != np.float32:
            raise TypeError('sampling requires float32 image pixels')
        if sx.shape != sy.shape:
            raise ValueError('sampling coordinate shapes differ')
        # Both alignment and contiguity are part of Rust's slice contract.
        # A contiguous ndarray can still be unaligned (for example frombuffer).
        image = np.require(image, requirements=['C', 'A'])
        sx, sy = np.require(sx, requirements=['C', 'A']), np.require(sy, requirements=['C', 'A'])
        output = np.empty((*sx.shape, 3), dtype=np.float64)
        if not sx.size:
            return output
        status = self.call(image, image.shape[1], image.shape[0], sx, sy, sx.size, output)
        if status:
            raise ValueError(f'Rust image sampler rejected input: {status}')
        return output


_sampler = None


def native_sampler():
    global _sampler
    if _sampler is None:
        suffix = '.dylib' if sys.platform == 'darwin' else '.so'
        name = 'libhallpanel_image_sampling' + suffix
        here = Path(__file__).resolve().parent
        # Installed binaries sit with this module. A checkout uses the release
        # artifact built and tested by tools/check.py before the Python tests.
        installed = here / name
        development = here.parents[1] / 'native/image_sampling/target/release' / name
        library = installed if installed.is_file() else development
        if not library.is_file():
            raise RuntimeError('Rust image sampler is missing; run tools/check.py or deploy_jarvis.sh')
        _sampler = RustSampler(library)
    return _sampler


def sample(image, sx, sy):
    return native_sampler().sample(image, sx, sy)


def encode_qoi_rgb(pixels):
    return native_sampler().encode_qoi_rgb(pixels)


def gaussian_blur(image, weights):
    return native_sampler().gaussian_blur(image, weights)


def backend():
    return {'name': 'rust', 'abi': 1, 'library': str(native_sampler().path)}
