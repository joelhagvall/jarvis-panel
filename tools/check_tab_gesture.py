#!/usr/bin/env python3
"""Probe actual native LVGL tab gesture delivery; never contacts or flashes the panel."""
import argparse
import importlib.util
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path,
                        default=ROOT/'styles/glass-v2/.esphome/build/style-glass-v2')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    spec = importlib.util.spec_from_file_location('gesture_config', ROOT/'styles/glass-v2/gen.py')
    gen = importlib.util.module_from_spec(spec); spec.loader.exec_module(gen)
    cfg = {'script': [], 'globals': [], 'esphome': {},
           'lvgl': {'pages': [{'id': name} for name in gen.SWIPE_ORDER], 'top_layer': {'widgets': []}}}
    gen.shared_bar(cfg)
    assert cfg['lvgl']['top_layer']['widgets'][0]['obj'].get('gesture_bubble') is False
    library = list(args.build_dir.glob('.pioenvs/*/lib*/liblvgl.a'))
    headers = list(args.build_dir.glob('.piolibdeps/*/lvgl/lvgl.h'))
    if len(library) != 1 or len(headers) != 1:
        raise RuntimeError('Build the glass-v2 native simulator first, or provide its --build-dir')
    binary = args.out/'tab-gesture'
    pointer_source = ROOT/'components/panel_debug/panel_debug.cpp'
    implementation = pointer_source.read_text().split('void PanelDebug::pointer_(', 1)[1].split('\n}\n', 1)[0]
    source = args.out/'tab-gesture.cpp'
    source.write_text((ROOT/'tests/native/tab_gesture.cpp').read_text().replace(
        '// ACTUAL_PANEL_POINTER_IMPL', 'void Probe::pointer_(' + implementation + '\n}'))
    command = [shutil.which('clang++') or shutil.which('g++') or 'c++', '-std=c++17',
               '-DLV_CONF_INCLUDE_SIMPLE', f'-DTABS_X={gen.RECTS["tabs"][0]}',
               f'-DTABS_Y={gen.RECTS["tabs"][1]}', f'-DTAB_WIDTH={gen.TAB_W}',
               '-I'+str(headers[0].parent), '-I'+str(args.build_dir/'src'),
               str(source), str(library[0]), '-o', str(binary)]
    result = subprocess.run(command, capture_output=True, text=True, timeout=60)
    (args.out/'compile.log').write_text(result.stdout+result.stderr)
    result.check_returncode()
    cases = []
    for bubble, driven in (('1','1'), ('0','0'), ('0','1')):
        result = subprocess.run([str(binary), bubble, driven], capture_output=True, text=True, timeout=10)
        (args.out/f'mode-{bubble}-{driven}.log').write_text(result.stdout+result.stderr)
        result.check_returncode()
        cases.append(json.loads(result.stdout.splitlines()[-1]))
    report = {'ok': True, 'actual_native_lvgl': True, 'physical_hardware': False, 'cases': cases,
              'sources': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (ROOT/'styles/glass-v2/gen.py', ROOT/'tests/native/tab_gesture.cpp',
                                       pointer_source, Path(__file__).resolve())}}
    (args.out/'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
