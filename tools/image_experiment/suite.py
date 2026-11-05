#!/usr/bin/env python3
"""Interleave cold image variants on Jarvis. Run inside the isolated scratch root."""
import argparse
import json
from pathlib import Path
import platform
import subprocess
import sys
import time


def hardware():
    result = {}
    for command in (['vcgencmd', 'get_throttled'], ['vcgencmd', 'measure_temp'], ['uptime']):
        proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5)
        result[' '.join(command)] = proc.stdout.strip()
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', default='main')
    parser.add_argument('--numpy', default='numpy')
    args = parser.parse_args()
    import numpy
    import PIL
    root = Path.cwd()
    out = root / 'results' / args.out
    out.mkdir(exist_ok=False)
    metadata = {'platform': platform.platform(), 'numpy': numpy.__version__, 'pillow': PIL.__version__,
                'start': hardware(), 'started_unix': time.time(), 'repeats': 3,
                'protocol': 'cold-image-five-sequential-processes-v1'}
    (out / 'environment.json').write_text(json.dumps(metadata, indent=2))
    variants = ['baseline', 'numpy', 'rust']
    for index, image in enumerate(('almond', 'starry')):
        for repeat in range(3):
            order = variants[(repeat + index) % 3:] + variants[:(repeat + index) % 3]
            for variant in order:
                destination = out / f'{image}-{repeat}-{variant}'
                command = [sys.executable, str(root / 'tools/image_experiment/benchmark.py'),
                           '--root', str(root), '--bake', str(root / 'sources' / ('baseline' if variant == 'baseline' else args.numpy) / 'bake.py'),
                           '--input', str(root / 'art' / f'{image}.jpg'), '--out', str(destination), '--variant', variant]
                if variant == 'rust':
                    command += ['--library', str(root / 'native/image_sampling/target/release/libhallpanel_image_sampling.so')]
                before = hardware()
                proc = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=180)
                after = hardware()
                destination.mkdir(exist_ok=True)
                (destination / 'host.json').write_text(json.dumps({'before': before, 'after': after,
                    'stdout': proc.stdout, 'stderr': proc.stderr, 'returncode': proc.returncode}, indent=2))
                print(proc.stdout.strip(), flush=True)
                if proc.returncode:
                    raise RuntimeError(proc.stderr)
                if after['vcgencmd get_throttled'] != 'throttled=0x0':
                    raise RuntimeError('throttling detected; retain the run and investigate')
    metadata.update(end=hardware(), ended_unix=time.time())
    (out / 'environment.json').write_text(json.dumps(metadata, indent=2))


if __name__ == '__main__':
    main()
