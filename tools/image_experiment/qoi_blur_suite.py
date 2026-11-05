#!/usr/bin/env python3
"""Isolated local image comparison. Never queries or operates the panel or Jarvis."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import sys
import time


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--candidate', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    args.out.mkdir(parents=True, exist_ok=False)
    env = {'platform': platform.platform(), 'python': sys.version, 'started': time.time(),
           'priority': os.getpriority(os.PRIO_PROCESS, 0), 'load_start': os.getloadavg(),
           'protocol': 'cold-image-five-sequential-processes-v1', 'repeats': 3,
           'scope': 'Mac only; no Jarvis or panel access; not a Raspberry Pi speedup claim'}
    (args.out / 'environment.json').write_text(json.dumps(env, indent=2))
    rows, pairs = [], []
    for index, image in enumerate(('almond', 'starry')):
        for repeat in range(3):
            order = ('baseline', 'candidate') if (repeat + index) % 2 == 0 else ('candidate', 'baseline')
            for variant in order:
                source = args.baseline if variant == 'baseline' else args.candidate
                destination = args.out / f'{image}-{repeat}-{variant}'
                command = [sys.executable, str(root / 'tools/image_experiment/benchmark.py'),
                    '--root', str(source.resolve()), '--bake', str((source / 'styles/glass-v2/bake.py').resolve()),
                    '--input', str(root / 'art' / f'{image}.jpg'), '--out', str(destination), '--variant', variant]
                result = subprocess.run(command, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120)
                if result.returncode:
                    (args.out / 'failure.json').write_text(json.dumps({'command': command, 'stdout': result.stdout, 'stderr': result.stderr}))
                    raise RuntimeError(result.stderr)
                report = json.loads((destination / 'timing.json').read_text())
                report['image'] = image
                rows.append(report)
                print(image, repeat, variant, round(report['wall_s'], 3), round(report['cpu_s'], 3), report['peak_rss_kib'], flush=True)
            baseline = args.out / f'{image}-{repeat}-baseline'
            candidate = args.out / f'{image}-{repeat}-candidate'
            for path in baseline.iterdir():
                if path.suffix in ('.png', '.qoi'):
                    other = candidate / path.name
                    pairs.append({'case': f'{image}-{repeat}', 'file': path.name,
                        'byte_identical': path.read_bytes() == other.read_bytes(),
                        'baseline_sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
                        'candidate_sha256': hashlib.sha256(other.read_bytes()).hexdigest()})
    summary = {}
    for image in ('almond', 'starry'):
        summary[image] = {}
        for variant in ('baseline', 'candidate'):
            subset = [r for r in rows if r['image'] == image and r['variant'] == variant]
            summary[image][variant] = {key: statistics.median(r[key] for r in subset)
                                     for key in ('wall_s', 'cpu_s', 'peak_rss_kib')}
    env.update(ended=time.time(), load_end=os.getloadavg())
    (args.out / 'environment.json').write_text(json.dumps(env, indent=2))
    (args.out / 'comparison.json').write_text(json.dumps({'summary': summary, 'comparisons': pairs}, indent=2))
    print(json.dumps({'summary': summary, 'nonidentical': [p for p in pairs if not p['byte_identical']]}, indent=2))


if __name__ == '__main__':
    main()
