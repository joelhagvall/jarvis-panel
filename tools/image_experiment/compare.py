#!/usr/bin/env python3
"""Compare every output pixel after the timing processes have finished."""
import argparse
import json
from pathlib import Path
import statistics

import numpy as np
from PIL import Image


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('results', type=Path)
    args = parser.parse_args()
    reports, comparisons = [], []
    for path in sorted(args.results.glob('*/timing.json')):
        report = json.loads(path.read_text())
        reports.append(report)
        case = path.parent.name
        image, repeat, variant = case.rsplit('-', 2)
        if variant == 'baseline':
            continue
        reference = args.results / f'{image}-{repeat}-baseline'
        for target in sorted(path.parent.iterdir()):
            if target.suffix not in ('.png', '.qoi'):
                continue
            # Byte-identical lossless files necessarily have identical pixels.
            # Avoid spending seconds in Pillow's Python QOI decoder for these.
            if target.read_bytes() == (reference / target.name).read_bytes():
                comparisons.append({'case': case, 'file': target.name,
                    'changed_pixels': 0, 'max_channel_delta': 0, 'mean_channel_delta': 0.0})
                continue
            with Image.open(reference / target.name) as im:
                a = np.array(im.convert('RGB'), dtype=np.int16)
            with Image.open(target) as im:
                b = np.array(im.convert('RGB'), dtype=np.int16)
            difference = np.abs(a - b)
            comparisons.append({'case': case, 'file': target.name,
                'changed_pixels': int(np.any(difference, axis=2).sum()),
                'max_channel_delta': int(difference.max()),
                'mean_channel_delta': float(difference.mean())})
    summary = {}
    for image in ('almond', 'starry'):
        summary[image] = {}
        for variant in ('baseline', 'numpy', 'rust'):
            rows = [r for r in reports if Path(r['input']).stem == image and r['variant'] == variant]
            if rows:
                summary[image][variant] = {'n': len(rows), **{key: statistics.median(r[key] for r in rows)
                    for key in ('wall_s', 'cpu_s', 'peak_rss_kib')}}
    result = {'summary': summary, 'comparisons': comparisons, 'runs': len(reports)}
    (args.results / 'comparison.json').write_text(json.dumps(result, indent=2))
    print(json.dumps({'summary': summary, 'runs': len(reports),
                      'nonidentical': [r for r in comparisons if r['changed_pixels']]}, indent=2))


if __name__ == '__main__':
    main()
