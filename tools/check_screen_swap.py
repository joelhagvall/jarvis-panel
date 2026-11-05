#!/usr/bin/env python3
"""Prove screen_swap.h against actual LVGL on the Mac; never contacts or flashes the panel.

Builds LVGL from the firmware's own source (the glass-v2 build's copy by default) with
tests/native/lv_conf_screen_swap.h, then runs tests/native/screen_swap.cpp: random page
changes, value updates, background repaints and tab highlights in double-buffered direct
mode. Every presented frame with the cover callback and the page-change areas must be
bit-identical to LVGL's own full redraw of the same sequence. Host timings are not P4 timings.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parents[1]
SKIP = ('/drivers/', '/others/vg_lite', '/thorvg/', '/draw/vg_lite/', '/draw/nxp/', '/draw/renesas/',
        '/draw/dma2d/', '/draw/opengles/', '/draw/sdl/', '/draw/espressif/', '/draw/nema', '/draw/eve/',
        '/draw/ppa/')


def find_lvgl():
    builds = ROOT / 'styles/glass-v2/.esphome/build'
    found = sorted(builds.glob('*/managed_components/lvgl__lvgl')) + sorted(builds.glob('*/.piolibdeps/*/lvgl'))
    found = [path for path in found if (path / 'lvgl.h').is_file()]
    if not found:
        raise RuntimeError('Build glass-v2 once (simulator or firmware) or provide --lvgl-dir')
    return found[0]


def compile_lvgl(lvgl, out, compiler):
    conf = ROOT / 'tests/native/lv_conf_screen_swap.h'
    sources = [path for path in sorted((lvgl / 'src').rglob('*.c'))
               if not any(part in path.as_posix() for part in SKIP)
               and ('/libs/' not in path.as_posix() or '/libs/bin_decoder/' in path.as_posix())]
    objects = out / 'lvgl'
    objects.mkdir()

    def build(source):
        target = objects / (hashlib.sha256(str(source).encode()).hexdigest()[:16] + '.o')
        subprocess.run([compiler, '-c', '-O2', '-DLV_CONF_H=1', f'-DLV_CONF_PATH="{conf}"', '-I' + str(lvgl),
                        str(source), '-o', str(target)], check=True, capture_output=True, timeout=120)
        return target

    with ThreadPoolExecutor(max_workers=os.cpu_count() or 4) as pool:
        built = list(pool.map(build, sources))
    library = out / 'liblvgl.a'
    subprocess.run(['ar', 'rcs', str(library), *map(str, built)], check=True)
    return library, conf


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--lvgl-dir', type=Path, help='LVGL source root (contains lvgl.h)')
    parser.add_argument('--seeds', type=int, default=20)
    parser.add_argument('--steps', type=int, default=2000)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    lvgl = (args.lvgl_dir or find_lvgl()).resolve()
    version = (lvgl / 'lv_version.h').read_text() if (lvgl / 'lv_version.h').is_file() else ''
    compiler = shutil.which('clang') or shutil.which('cc')
    library, conf = compile_lvgl(lvgl, args.out, compiler)
    binary = args.out / 'screen-swap'
    test = ROOT / 'tests/native/screen_swap.cpp'
    command = [shutil.which('clang++') or shutil.which('c++'), '-std=c++17', '-O1', '-g', '-Wall', '-Wextra',
               '-fsanitize=address,undefined', '-fno-sanitize-recover=all', '-DLV_CONF_H=1',
               f'-DLV_CONF_PATH="{conf}"', '-I' + str(lvgl), '-I' + str(ROOT / 'styles/glass-v2'),
               str(test), str(library), '-o', str(binary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=300)
    (args.out / 'compile.log').write_text(result.stdout + result.stderr)
    result.check_returncode()

    def run(mode, seed):
        result = subprocess.run([str(binary), str(mode), str(seed), str(args.steps)],
                                capture_output=True, text=True, timeout=600)
        (args.out / f'mode-{mode}-seed-{seed}.log').write_text(result.stdout + result.stderr)
        result.check_returncode()
        lines = result.stdout.splitlines()
        return [line for line in lines if line.startswith('frame ')], json.loads(lines[-1])

    cases, applied = [], 0
    for seed in range(1, args.seeds + 1):
        reference, base = run(0, seed)
        optimized, summary = run(3, seed)
        identical = reference == optimized
        first = next((a for a, b in zip(reference, optimized) if a != b), None)
        cases.append({'seed': seed, 'frames': len(reference), 'identical': identical, 'first_difference': first,
                      'swaps': summary['swaps'], 'applied': summary['applied'], 'fallbacks': summary['fallbacks'],
                      'pixels_per_applied': summary['pixels_per_applied'],
                      'reference_screen_fill_px': base['screen_fill_px'],
                      'host_swap_refresh_ms': {'reference': base['swap_refresh_ms_mean'],
                                               'optimized': summary['swap_refresh_ms_mean']}})
        applied += summary['applied']
    ok = all(case['identical'] for case in cases) and applied > 0
    report = {'ok': ok, 'actual_native_lvgl': True, 'physical_hardware': False,
              'lvgl': str(lvgl), 'lvgl_version': ' '.join(line.split()[-1] for line in version.splitlines()
                                                         if line.startswith('#define LVGL_VERSION_M')
                                                         or line.startswith('#define LVGL_VERSION_P')),
              'frames_compared': sum(case['frames'] for case in cases), 'applied_page_changes': applied,
              'cases': cases,
              'sources': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (ROOT / 'styles/glass-v2/screen_swap.h', test, conf, Path(__file__).resolve())}}
    (args.out / 'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps({key: report[key] for key in ('ok', 'lvgl_version', 'frames_compared', 'applied_page_changes')}))
    if not ok:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
