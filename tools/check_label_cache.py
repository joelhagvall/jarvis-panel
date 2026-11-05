#!/usr/bin/env python3
"""Prove label_cache.h against actual LVGL on the Mac; never contacts or flashes the panel.

    .venv/bin/python tools/check_label_cache.py --out NEW_DIR [--seeds 12] [--steps 300]

Builds LVGL from the firmware's own source (as tools/check_screen_swap.py does) and runs tests/native/label_cache.cpp:
random plain labels (overlapping glyphs, placeholders, wrapping, opacities, alignments) redrawn over random partial
areas. Every presented frame with the cache must be bit-identical to LVGL's own per-glyph drawing of the same
sequence, and the cache must actually have drawn labels (hits > 0). Host timings are not P4 timings.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from check_screen_swap import compile_lvgl, find_lvgl  # noqa: E402


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--lvgl-dir', type=Path)
    parser.add_argument('--seeds', type=int, default=12)
    parser.add_argument('--steps', type=int, default=300)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    lvgl = (args.lvgl_dir or find_lvgl()).resolve()
    compiler = shutil.which('clang') or shutil.which('cc')
    library, conf = compile_lvgl(lvgl, args.out, compiler)
    binary = args.out / 'label-cache'
    command = [shutil.which('clang++') or shutil.which('c++'), '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
               '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-DLV_CONF_H=1',
               f'-DLV_CONF_PATH="{conf}"', '-I' + str(lvgl), '-I' + str(ROOT / 'styles/glass-v2'),
               str(ROOT / 'tests/native/label_cache.cpp'), str(library), '-o', str(binary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    (args.out / 'compile.log').write_text(result.stdout + result.stderr)
    result.check_returncode()

    def run(mode, seed):
        result = subprocess.run([str(binary), str(mode), str(seed), str(args.steps)], capture_output=True, text=True,
                                timeout=600)
        (args.out / f'mode-{mode}-seed-{seed}.log').write_text(result.stdout + result.stderr)
        result.check_returncode()
        lines = result.stdout.splitlines()
        return [line for line in lines if line.startswith('frame ')], json.loads(lines[-1])

    cases, hits, builds, overlaps = [], 0, 0, 0
    for seed in range(1, args.seeds + 1):
        reference, _ = run(0, seed)
        cached, summary = run(1, seed)
        first = next((a for a, b in zip(reference, cached) if a != b), None)
        cases.append({'seed': seed, 'frames': len(reference), 'identical': reference == cached,
                      'first_difference': first, **summary})
        hits, builds, overlaps = hits + summary['hits'], builds + summary['builds'], overlaps + summary['overlaps']
    ok = all(c['identical'] for c in cases) and hits > 0
    report = {'ok': ok, 'frames_compared': sum(c['frames'] for c in cases), 'hits': hits, 'builds': builds,
              'overlaps_refused': overlaps, 'cases': cases}
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({k: report[k] for k in ('ok', 'frames_compared', 'hits', 'builds', 'overlaps_refused')}))
    if not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
