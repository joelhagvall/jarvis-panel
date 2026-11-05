#!/usr/bin/env python3
"""Run the orb director (styles/glass-v2/orb_director.h) offline on a real reply's loudness.

Free and local: it compiles the director, feeds it listen -> think -> swing -> speak (the WAV's
loudness, as the panel's microphone would hear it next to the speaker) -> settle, and prints how often
the state changes, how many frames cross-fade (double images) and the largest per-frame changes of
speed and line height. Use it before flashing a change to the orb's motion.

  tools/orb_director_sim.py REPLY.wav            a 16 kHz mono WAV, e.g. HA's cached TTS file
  tools/orb_director_sim.py REPLY.wav --json     the same numbers as JSON

A cached reply: ~/docker/homeassistant/config/tts/*.wav on Jarvis (tools/jarvis_ssh.sh, cat).
"""
import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import wave

ROOT = Path(__file__).resolve().parents[1]
FPS = 60.385
MIC_SCALE = 0.025 / 0.2   # the microphone heard ~0.025 RMS when the reply was ~0.2 RMS (2026-10-27)
QUIET = 0.0006            # the microphone's level in a quiet hallway

PROGRAM = r'''
#include <cstdio>
#include "orb_director.h"
using packed_orb::DirectorCore; using M = DirectorCore;
int main() {
  DirectorCore d; d.setup(96, 24, 72, 18, 210);
  auto step = [&](double level) {
    d.voice(level); auto r = d.advance();
    std::printf("%d %d %.5f %.4f %.4f %.4f %u\n", int(d.cur.mode), int(d.fading), d.cur.speed,
                d.wave.amp[0], d.wave.amp[1], d.wave.amp[2], unsigned(r.first));
  };
  int phase; double level;
  while (std::scanf("%d %lf", &phase, &level) == 2) {
    if (phase >= 0) d.change(M::Mode(phase));
    step(level);
  }
}
'''


def loudness(path):
    import numpy as np
    with wave.open(str(path)) as w:
        rate = w.getframerate()
        x = np.frombuffer(w.readframes(w.getnframes()), '<i2').astype(float) / 32768
    step, level, out = rate / FPS, 0.0, []
    for k in range(int(len(x) / step)):
        seg = x[int(k * step):int((k + 1) * step)]
        rms = float(np.sqrt(np.mean(seg ** 2))) * MIC_SCALE + QUIET
        a = 1 - np.exp(-(1 / FPS) / (0.020 if rms > level else 0.120))   # the microphone's envelope
        level += (rms - level) * a
        out.append(level)
    return out


def run(wav):
    compiler = shutil.which('clang++') or shutil.which('g++')
    if not compiler:
        raise SystemExit('C++ compiler unavailable')
    script = [(-1, QUIET)] * 90 + [(0, 0.005)] * 150 + [(1, QUIET)] + [(-1, QUIET)] * 100 + [(2, QUIET)] + \
             [(-1, QUIET)] * 39 + [(3, QUIET)] + [(-1, QUIET)] * 25 + [(-1, v) for v in loudness(wav)] + \
             [(4, QUIET)] + [(-1, QUIET)] * 120
    with tempfile.TemporaryDirectory(prefix='hallpanel-orbsim-') as tmp:
        tmp = Path(tmp)
        (tmp / 'sim.cpp').write_text(PROGRAM)
        subprocess.run([compiler, '-std=c++17', '-O1', '-I', str(ROOT / 'styles/glass-v2'), str(tmp / 'sim.cpp'),
                        '-o', str(tmp / 'sim')], check=True, capture_output=True)
        out = subprocess.run([str(tmp / 'sim')], input='\n'.join(f'{p} {v}' for p, v in script),
                             capture_output=True, text=True, check=True).stdout
    rows = [line.split() for line in out.splitlines()]
    names = 'listen think swing speak settle hush'.split()
    modes = [int(r[0]) for r in rows]
    heights = [[float(r[3]), float(r[4]), float(r[5])] for r in rows]
    speeds = [float(r[2]) for r in rows]
    same = [i for i in range(1, len(rows)) if modes[i] == modes[i - 1]]
    speaking = [i for i in same if modes[i] == 3] or [1]
    return {
        'frames': len(rows),
        'states': [(i, names[modes[i]]) for i in range(1, len(rows)) if modes[i] != modes[i - 1]],
        'cross_fading_frames': sum(int(r[1]) for r in rows),
        'largest_speed_step_within_a_state': max(abs(speeds[i] - speeds[i - 1]) for i in same),
        # Within speaking only: entering it sets talk_00's height, the same picture as the swing's end.
        'largest_height_rise': max(max(heights[i][k] - heights[i - 1][k] for k in range(3)) for i in speaking),
        'largest_height_fall': max(max(heights[i - 1][k] - heights[i][k] for k in range(3)) for i in speaking),
    }


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('wav', type=Path)
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args()
    result = run(args.wav)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        for key, value in result.items():
            print(f'{key}: {value}')
    sys.exit(0)
