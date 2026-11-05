#!/usr/bin/env python3
"""Compare variants of the drawn voice wave (styles/glass-v2/voice_wave.h) on the Mac, before flashing.

  .venv/bin/python tools/wave_bench.py [-DNAME=VALUE ...] [--include DIR]

Compiles tests/test_voice_wave.py's program against voice_wave.h (or the copy in --include DIR, to try an
edited version beside the real one), draws talk_00, 09 ... 63 on the line's hollow_00 and prints the worst
mean error against the drawn frames (colour times alpha, where the line is lit, and alpha), as the test
measures it, and the slowest render time on this Mac. Free and offline.

Mac times only rank variants roughly: on 2026-10-28 one change was 28 % faster on both, another 19 % on the
Mac and 22 % on the panel. Decide on the panel with tools/panel wave --measure (docs/responsiveness.md).
"""
import argparse
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'orbs'))
sys.path.insert(0, str(ROOT / 'tests'))


def bench(include, flags):
    import numpy as np
    from PIL import Image
    import atlas
    import test_voice_wave as reference
    compiler = shutil.which('clang++') or shutil.which('g++')
    if not compiler:
        raise RuntimeError('C++ compiler unavailable')
    frames = atlas.folder(atlas.PANEL_VERSION)
    with tempfile.TemporaryDirectory(prefix='hallpanel-wave-bench-') as tmp:
        tmp = Path(tmp)
        (tmp / 'wave.cpp').write_text(reference.PROGRAM)
        subprocess.run([compiler, '-std=c++17', '-O2', *flags, '-I', str(include), str(tmp / 'wave.cpp'),
                        '-o', str(tmp / 'wave')], check=True)
        with Image.open(frames / 'hollow_00.png') as hollow:
            (tmp / 'hollow.raw').write_bytes(atlas.rgb565a8(hollow.convert('RGBA')))
        slowest = subprocess.run([str(tmp / 'wave'), str(tmp / 'hollow.raw'), str(tmp / 'out.raw')],
                                 check=True, capture_output=True, text=True).stdout.strip()
        drawn = (tmp / 'out.raw').read_bytes()
    size, colour, alpha = 220 * 220 * 3, [], []
    for i, k in enumerate(range(0, 72, 9)):
        with Image.open(frames / f'talk_{k:02d}.png') as talk:
            ref_rgb, ref_a = reference.planes(atlas.rgb565a8(talk.convert('RGBA')))
        rgb, a = reference.planes(drawn[i * size:(i + 1) * size])
        lit = ref_a >= 24
        colour.append((np.abs(rgb * a[:, None] - ref_rgb * ref_a[:, None]).max(1) / 255)[lit].mean())
        alpha.append(np.abs(a - ref_a)[lit].mean())
    return {'error': round(float(max(colour)), 2), 'alpha_error': round(float(max(alpha)), 2),
            'slowest_ms_mac': float(slowest)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--include', type=Path, default=ROOT / 'styles/glass-v2')
    args, flags = parser.parse_known_args(argv)
    result = bench(args.include, flags)
    print(f"{' '.join(flags) or 'voice_wave.h'}: worst mean error {result['error']}, alpha {result['alpha_error']}, "
          f"slowest {result['slowest_ms_mac']} ms on this Mac")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
