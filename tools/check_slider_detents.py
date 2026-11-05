#!/usr/bin/env python3
"""Run generated slider handlers with actual native LVGL pointer input; no panel access."""
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-dir', type=Path, default=ROOT/'styles/glass-v2/.esphome/build/style-glass-v2')
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    spec = importlib.util.spec_from_file_location('native_detents_gen', ROOT/'styles/glass-v2/gen.py')
    gen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gen)
    libraries = list(args.build_dir.glob('.pioenvs/*/lib*/liblvgl.a'))
    headers = list(args.build_dir.glob('.piolibdeps/*/lvgl/lvgl.h'))
    if len(libraries) != 1 or len(headers) != 1:
        raise RuntimeError('Build the glass-v2 native simulator first, or supply --build-dir')
    pointer = ROOT/'components/panel_debug/panel_debug.cpp'
    implementation = pointer.read_text().split('void PanelDebug::pointer_(', 1)[1].split('\n}\n', 1)[0]
    template = (ROOT/'tests/native/slider_detents.cpp').read_text().replace(
        '// ACTUAL_PANEL_POINTER_IMPL', 'void Probe::pointer_(' + implementation + '\n}')
    cases = []
    for key, icon, name, value, minimum in [('bright', 'sun', 'Skärmljus', 100, 5),
                                          ('nightl', 'moon', 'Nattljus', 12, 1),
                                          ('vol', 'volume', 'Volym', 20, 0)]:
        row = gen.slider_row(key, icon, name, value, minimum)['obj']
        slider = next(w['slider'] for w in row['widgets'] if 'slider' in w)
        source = template
        for event, marker in [('on_value', '// ACTUAL_ON_VALUE'), ('on_release', '// ACTUAL_ON_RELEASE')]:
            code = slider[event][0]['lambda']
            for before, after in [(f'id(s_{key})', 'slider'), (f'id(s_{key}_val)', 'value_label'),
                                  (f'id({key}_pct)', 'shown')]:
                code = code.replace(before, after)
            source = source.replace(marker, code)
        src, binary = args.out/f'{key}.cpp', args.out/key
        src.write_text(source)
        command = [shutil.which('clang++') or shutil.which('g++') or 'c++', '-std=c++17',
                   '-DLV_CONF_INCLUDE_SIMPLE', f'-DSLIDER_LOW={minimum}', f'-DSLIDER_WIDTH={slider["width"]}',
                   '-I'+str(headers[0].parent), '-I'+str(args.build_dir/'src'),
                   '-I'+str(ROOT/'styles/glass-v2'), str(src), str(libraries[0]), '-o', str(binary)]
        result = subprocess.run(command, capture_output=True, text=True, timeout=60)
        (args.out/f'{key}-compile.log').write_text(result.stdout+result.stderr)
        result.check_returncode()
        result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
        (args.out/f'{key}-run.log').write_text(result.stdout+result.stderr)
        result.check_returncode()
        cases.append({'slider': key, **json.loads(result.stdout.splitlines()[-1])})
    report = {'ok': True, 'actual_native_lvgl': True, 'physical_hardware': False, 'cases': cases,
              'sources': {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest()
                          for path in (ROOT/'styles/glass-v2/gen.py', ROOT/'styles/glass-v2/slider_detents.h',
                                       ROOT/'tests/native/slider_detents.cpp', pointer, Path(__file__).resolve())}}
    (args.out/'report.json').write_text(json.dumps(report, indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
