#!/usr/bin/env python3
"""Real LVGL scheduling experiment with fixed synthetic render cost; no device or model calls."""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def run():
    build = ROOT / 'styles/glass-v2/.esphome/build/style-glass-v2'
    library = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
    headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
    if len(library) != 1 or len(headers) != 1:
        raise RuntimeError('Build the native glass-v2 simulator first')
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp)
        stub = folder / 'esphome/core/application.h'
        stub.parent.mkdir(parents=True)
        stub.write_text('#pragma once\nnamespace esphome { struct Application { unsigned interval=16; '
                        'void set_loop_interval(unsigned value) { interval=value; } }; inline Application App; }\n')
        binary = folder / 'pacing'
        subprocess.run(['c++', '-std=c++17', '-DLV_CONF_INCLUDE_SIMPLE', '-I'+str(folder), '-I'+str(ROOT),
                        '-I'+str(headers[0].parent), '-I'+str(build / 'src'),
                        str(ROOT / 'tests/native/frame_pacing.cpp'), str(library[0]), '-o', str(binary)],
                       check=True, capture_output=True, timeout=60)
        cases = [json.loads(subprocess.check_output([str(binary), str(period)], timeout=10).splitlines()[-1])
                 for period in (16, 5, 1)]
    return {'ok': cases[0]['jank_percent'] > cases[-1]['jank_percent'] and cases[-1]['p95_ms'] <= 50,
            'actual_native_lvgl': True, 'physical_hardware': False, 'synthetic_render_cost_ms': 17,
            'synthetic_other_loop_work_ms': 2,
            'limitations': 'Controlled scheduler model; excludes actual P4/PSRAM, scanout, audio and network contention.',
            'cases': cases, 'sources': {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                                      for p in (ROOT / 'bench/frame_pacing.h', ROOT / 'tests/native/frame_pacing.cpp', Path(__file__).resolve())}}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path)
    args = parser.parse_args()
    result = run()
    if args.out:
        args.out.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2) + '\n')
        args.out.chmod(0o600)
    print(json.dumps(result, indent=2))
    raise SystemExit(0 if result['ok'] else 1)
