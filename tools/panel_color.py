#!/usr/bin/env python3
"""Check a lossless dashboard snapshot against its served image and calendar payload.

Offline verification only; capture first with tools/panel inspect --lossless.
The explicit ROI must be a background-only rectangle chosen from that snapshot.
"""
import argparse
import json
import os
from pathlib import Path
import sys

import numpy as np
from PIL import Image


def rgb565(rgb):
    value = np.asarray(rgb, dtype=np.uint16)
    return ((value >> [3, 2, 3]) * [255, 255, 255] // [31, 63, 31]).astype(np.uint8)


def check(snapshot, source, prepared, roi, calendar=None):
    screen = np.array(Image.open(Path(snapshot) / 'screen.png').convert('RGB'))
    original = np.array(Image.open(source).convert('RGB'))
    device = np.array(Image.open(prepared).convert('RGB'))
    if screen.shape != original.shape or screen.shape != device.shape:
        raise ValueError('Image dimensions differ')
    x1, y1, x2, y2 = roi
    if not (0 <= x1 < x2 <= screen.shape[1] and 0 <= y1 < y2 <= screen.shape[0]):
        raise ValueError('Invalid background-only ROI')
    area = np.s_[y1:y2, x1:x2]
    expected = rgb565(device)
    errors = np.abs(screen[area].astype(int) - expected[area].astype(int))
    # Compare spatial averages: dithering deliberately increases per-pixel error.
    size = (max(1, (x2-x1)//8), max(1, (y2-y1)//8))
    def average(pixels):
        return np.array(Image.fromarray(pixels[area]).resize(size, Image.Resampling.BOX), dtype=float)
    fidelity = np.abs(average(screen) - average(original)).mean(axis=(0, 1))
    image = {'ok': bool(errors.max() == 0 and fidelity.max() < 1), 'roi': list(roi),
             'max_decode_error': int(errors.max()), 'block_average_rgb_mae': fidelity.tolist()}
    result = {'ok': image['ok'], 'background': image, 'calendar': []}
    if calendar is not None:
        ui = json.loads((Path(snapshot) / 'ui.json').read_text())
        if ui['page'] != 'page_glass_v2':
            raise ValueError('Calendar verification requires dashboard')
        widgets = {w['id']: w for w in ui['widgets']}
        for index in range(3):
            fields = calendar.get(f'cal{index}', '').split('|')
            if len(fields) < 4 or not fields[2]:
                continue
            colour = fields[3]
            if len(colour) != 7 or colour[0] != '#':
                raise ValueError('Missing calendar colour metadata')
            value = [int(colour[i:i+2], 16) for i in (1, 3, 5)]
            bar = widgets[f'screen/5/{1+3*index}']
            x, y = bar['x'] + bar['width']//2, bar['y'] + bar['height']//2
            actual = screen[y, x]
            expected_colour = rgb565(value)
            entry = {'slot': index, 'source': colour, 'expected_rgb565': expected_colour.tolist(),
                     'actual': actual.tolist(), 'ok': bool(np.array_equal(actual, expected_colour))}
            result['calendar'].append(entry)
            result['ok'] &= entry['ok']
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', required=True, type=Path)
    parser.add_argument('--source', required=True, type=Path, help='Original pre-RGB565 PNG')
    parser.add_argument('--prepared', required=True, type=Path, help='Exact PNG served to the panel')
    parser.add_argument('--roi', required=True, type=lambda s: tuple(map(int, s.split(','))), help='x1,y1,x2,y2')
    parser.add_argument('--calendar', type=Path, help='Same capture time /panel JSON with cal0..cal2')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    result = check(args.snapshot, args.source, args.prepared, args.roi,
                   json.loads(args.calendar.read_text()) if args.calendar else None)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as out:
        json.dump(result, out, indent=2)
    print(json.dumps(result))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    sys.exit(main())
