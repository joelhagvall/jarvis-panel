#!/usr/bin/env python3
"""Exercise the dashboard chat with real native LVGL; no device or model calls."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    args.out.mkdir(parents=True, exist_ok=False, mode=0o700)
    build = ROOT/'styles/glass-v2/.esphome/build/style-glass-v2'
    library = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
    headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
    if len(library) != 1 or len(headers) != 1:
        raise RuntimeError('Build the native glass-v2 simulator first')
    pointer = (ROOT/'components/panel_debug/panel_debug.cpp').read_text().split('void PanelDebug::pointer_(',1)[1].split('\n}\n',1)[0]
    source = args.out/'chat-scroll.cpp'
    source.write_text((ROOT/'tests/native/chat_scroll.cpp').read_text().replace('// ACTUAL_PANEL_POINTER_IMPL', 'void Probe::pointer_('+pointer+'\n}'))
    binary = args.out/'chat-scroll'
    result = subprocess.run([shutil.which('clang++') or 'c++', '-std=c++17', '-DLV_CONF_INCLUDE_SIMPLE',
        '-I'+str(headers[0].parent), '-I'+str(build/'src'), '-I'+str(ROOT/'styles/glass-v2'),
        str(source), str(library[0]), '-o', str(binary)], capture_output=True, text=True, timeout=60)
    (args.out/'compile.log').write_text(result.stdout+result.stderr)
    result.check_returncode()
    result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=15)
    (args.out/'native.log').write_text(result.stdout+result.stderr)
    result.check_returncode()
    report = json.loads(result.stdout.splitlines()[-1])
    (args.out/'report.json').write_text(json.dumps(report,indent=2))
    print(json.dumps(report))


if __name__ == '__main__':
    main()
