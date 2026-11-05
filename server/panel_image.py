#!/usr/bin/env python3
"""Prepare static images for the panel's RGB565 decoder, before publication.

Ordered dithering trades large coloured contour bands for a fixed, fine pattern.
Pixels are already on the RGB565 grid; the device only decodes the PNG as usual.
The source image is never changed. Run: python panel_image.py INPUT OUTPUT
"""
import argparse
import json
import sys
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'styles/glass-v2'))
from image_sampling import encode_qoi_rgb  # noqa: E402

VERSION = 'rgb565-v2'


def prepare(image):
    rgb = np.asarray(image.convert('RGB'))
    # Bayer 8x8, centred thresholds. Use the same threshold for all channels to
    # avoid adding independent coloured noise to neutral areas. No temporal noise.
    tile = np.array([[0]], dtype=np.uint16)
    for _ in range(3):
        tile = np.block([[4 * tile, 4 * tile + 2], [4 * tile + 3, 4 * tile + 1]])
    h, w = rgb.shape[:2]
    threshold = np.tile((tile + .5) / 64, ((h + 7) // 8, (w + 7) // 8))[:h, :w]
    pixels = np.empty_like(rgb)
    for channel, bits in enumerate((5, 6, 5)):
        count = (1 << bits) - 1
        # Match the actual RGB565 -> RGB888 expansion used by lossless capture.
        # Export expanded levels, not index << shift (which would darken the PNG).
        grid = np.arange(count + 1, dtype=np.uint16) * 255 // count
        value = rgb[:, :, channel]
        low = np.searchsorted(grid, value, side='right') - 1
        high = np.minimum(low + 1, count)
        span = grid[high] - grid[low]
        fraction = np.divide(value - grid[low], span, out=np.zeros(value.shape), where=span > 0)
        pixels[:, :, channel] = grid[low + (threshold < fraction)]
    return Image.fromarray(pixels)


def convert(source, target):
    with Image.open(source) as image:
        prepared = prepare(image)
        prepared.save(target, format='PNG')
        # QOI avoids PNG's DEFLATE/filter work on the P4. Both files carry the
        # same expanded RGB565 pixels; keep PNG for existing firmware clients.
        Path(target).with_suffix('.qoi').write_bytes(encode_qoi_rgb(np.asarray(prepared)))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    parser.add_argument('output', type=Path)
    args = parser.parse_args()
    convert(args.source, args.output)
    print(json.dumps({'ok': True, 'version': VERSION, 'output': str(args.output),
                      'bytes': args.output.stat().st_size}))
