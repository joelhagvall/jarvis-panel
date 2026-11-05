#!/usr/bin/env python3
"""Make timer.wav, the chime the panel plays when one of Home Assistant's timers is done (board.py --voice).

Two soft bell notes going up a fourth (A5, then D6), each a sine with a quiet octave and a fast attack that
dies away over about half a second: 1.1 s in all, peaking at -12 dBFS so a minute of it every 4 s stays
gentle. 16 kHz mono 16-bit, the panel's format. Standard library only.

  python3 assets/sounds/make_timer.py      writes assets/sounds/timer.wav next to this file
"""
import array
import math
import wave
from pathlib import Path

RATE, LENGTH, PEAK_DB = 16000, 1.1, -12.0
NOTES = [(0.0, 880.0), (0.32, 1174.66)]    # (start in seconds, frequency in Hz)


def note(t, freq):
    if t < 0:
        return 0.0
    attack = min(t / 0.008, 1.0)
    decay = math.exp(-t / 0.22)
    tone = math.sin(2 * math.pi * freq * t) + 0.25 * math.sin(2 * math.pi * 2 * freq * t) * math.exp(-t / 0.08)
    return attack * decay * tone


samples = [sum(note(i / RATE - start, freq) for start, freq in NOTES) for i in range(int(RATE * LENGTH))]
fade = int(RATE * 0.05)                     # no click at the end
for i in range(fade):
    samples[-1 - i] *= i / fade
scale = 32767 * 10 ** (PEAK_DB / 20) / max(abs(s) for s in samples)
out = Path(__file__).resolve().parent / "timer.wav"
with wave.open(str(out), "wb") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(RATE)
    w.writeframes(array.array("h", (round(s * scale) for s in samples)).tobytes())
print(f"wrote {out}")
