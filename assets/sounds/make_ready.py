#!/usr/bin/env python3
"""Make ready.wav, the sound the panel plays once at boot when everything is up (board.py --voice): Wi-Fi,
Home Assistant and the painting. Never at night and never after an update. 16 kHz mono 16-bit, the panel's
format, peaking at -12 dBFS like timer.wav. Standard library only.

  python3 assets/sounds/make_ready.py            writes ready.wav (the chosen variant) next to this file
  python3 assets/sounds/make_ready.py --all DIR  writes every variant to DIR, to listen to them

Variants (2026-10-23: Joel found a, b and c too plain and asked for something more Jarvis, more futuristic):
  a  "Uppvaknande": a soft bell arpeggio up a major chord, C5 E5 G5 and C6 on top
  b  "Andetag": a fifth that swells in and out, like a breath
  c  "Signal": a quick glide up into two bright pings, a little sci-fi
  d  "Uppstart": a low hum that powers up through a filtered whoosh into a high shimmer
  e  "HUD": three quick digital blips climbing, then an airy chord that hangs and fades
  f  "Reaktor": a soft sub pulse, an FM shimmer rising like a reactor spinning up, a clean ping on top
  g  "Beräknar": ticks that speed up, as if thinking, resolving into one warm tone
  h  "Glöd": a soft electric piano (FM, like a Rhodes) rolling up a Dmaj9 chord
  i  "Soluppgång": a low warm pad (Fmaj7) swelling in, a soft round ping on top
  j  "Varm reaktor": f's spin-up lower and softer, landing on a warm low A major chord
  k  "Djupare": i lower and slower, an open Dbmaj9, a two-note ping (Joel liked i)
  l  "Gryning": i's pad starting dark and brightening like a sunrise, two pings falling
  m  "Mjukare": shorter and rounder, Ebmaj7, a soft electric piano note on top instead of the bell
"""
import array
import math
import sys
import wave
from pathlib import Path

RATE, PEAK_DB = 16000, -12.0
CHOSEN = "l"   # "Gryning", Joel's pick of 13 (2026-10-23)


def bell(t, freq, decay=0.28):
    if t < 0:
        return 0.0
    attack = min(t / 0.006, 1.0)
    return attack * math.exp(-t / decay) * (math.sin(2 * math.pi * freq * t)
                                            + 0.3 * math.sin(2 * math.pi * 2 * freq * t) * math.exp(-t / 0.07))


def variant_a(t):
    return sum(bell(t - start, f) for start, f in ((0.0, 523.25), (0.11, 659.25), (0.22, 783.99), (0.36, 1046.5)))


def variant_b(t):
    swell = math.sin(math.pi * min(t / 1.3, 1.0)) ** 2           # in and out over 1.3 s
    return swell * (math.sin(2 * math.pi * 392.0 * t) + 0.8 * math.sin(2 * math.pi * 588.0 * t)
                    + 0.25 * math.sin(2 * math.pi * 784.0 * t + 0.6 * math.sin(2 * math.pi * 3 * t)))


def variant_c(t):
    glide = 0.0
    if t < 0.28:                                                  # 330 -> 660 Hz, quiet, underneath
        f = 330 * 2 ** (t / 0.28)
        glide = 0.45 * math.sin(2 * math.pi * f * t) * math.sin(math.pi * t / 0.28)
    return glide + bell(t - 0.26, 1318.5, 0.18) + 0.8 * bell(t - 0.42, 1760.0, 0.24)


class Noise:
    """Deterministic white noise through a one-pole low-pass whose cutoff can move (a whoosh)."""
    def __init__(self, seed=7):
        self.x, self.y = seed, 0.0

    def __call__(self, cutoff):
        self.x = (1103515245 * self.x + 12345) % 2 ** 31
        white = self.x / 2 ** 30 - 1
        a = 1 - math.exp(-2 * math.pi * cutoff / RATE)
        self.y += a * (white - self.y)
        return self.y


def env(t, start, attack, length):
    """0 before start, up over attack, down to 0 at start + length (a smooth arch)."""
    u = t - start
    if u < 0 or u > length:
        return 0.0
    return min(u / attack, 1.0) * math.cos(0.5 * math.pi * u / length) ** 2


_noise_d = Noise(11)


def variant_d(t):
    rise = min(t / 0.7, 1.0)
    f = 55 * 2 ** (2.5 * rise)                                       # 55 Hz up to about 310 Hz
    hum = env(t, 0, 0.05, 0.95) * (math.sin(2 * math.pi * f * t) + 0.4 * math.sin(4 * math.pi * f * t))
    whoosh = env(t, 0.1, 0.45, 0.8) * 2.2 * _noise_d(300 + 5000 * rise)
    shimmer = sum(env(t, 0.62, 0.02, 0.85) * 0.35 * math.sin(2 * math.pi * fr * t + 1.5 * math.sin(2 * math.pi * 6 * t))
                  for fr in (1567.98, 2093.0, 2637.0))
    return 0.6 * hum + whoosh + shimmer


def blip(t, start, freq):
    u = t - start
    if u < 0 or u > 0.07:
        return 0.0
    return math.exp(-u / 0.025) * math.tanh(3 * math.sin(2 * math.pi * freq * u))   # a soft square


def variant_e(t):
    blips = sum(blip(t, st, f) for st, f in ((0.0, 1318.5), (0.09, 1760.0), (0.18, 2349.3)))
    pad = env(t, 0.26, 0.18, 1.25) * sum(math.sin(2 * math.pi * f * t + 0.3 * math.sin(2 * math.pi * 0.8 * t)) * a
                                         for f, a in ((440.0, 0.5), (659.25, 0.4), (987.77, 0.3), (1318.5, 0.15)))
    return 0.55 * blips + pad


def variant_f(t):
    sub = env(t, 0, 0.01, 0.45) * math.sin(2 * math.pi * 48 * t) * 1.4
    spin = min(t / 0.8, 1.0)
    carrier = 220 * 2 ** (2 * spin)                                  # 220 Hz up to 880 Hz
    fm = env(t, 0.05, 0.4, 0.95) * 0.6 * math.sin(2 * math.pi * carrier * t + (2.5 - 2 * spin) * math.sin(2 * math.pi * 3 * carrier * t))
    return sub + fm + 0.9 * bell(t - 0.8, 1760.0, 0.32)


def variant_g(t):
    ticks, start, gap = 0.0, 0.0, 0.11
    while gap > 0.02:                                               # each tick a little sooner than the last (to ~0.5 s)
        ticks += blip(t, start, 2600.0) * 0.5
        start, gap = start + gap, gap * 0.8
    tone = env(t, 0.78, 0.04, 0.9) * (math.sin(2 * math.pi * 587.33 * t) + 0.5 * math.sin(2 * math.pi * 880.0 * t)
                                      + 0.2 * math.sin(2 * math.pi * 1174.66 * t))
    return ticks + tone


def epiano(t, freq, decay=0.9):
    """An FM electric piano note: a sine carrier, a modulator at 1x that fades fast (the tine), soft attack."""
    if t < 0:
        return 0.0
    index = 1.2 * math.exp(-t / 0.12)
    tone = math.sin(2 * math.pi * freq * t + index * math.sin(2 * math.pi * freq * t))
    tine = 0.15 * math.sin(2 * math.pi * 7 * freq * t) * math.exp(-t / 0.03)
    return min(t / 0.012, 1.0) * math.exp(-t / decay) * (tone + tine)


def warm(t, freq, harmonics=5):
    """A warm sustained tone: harmonics falling off fast (no bright top), a slow vibrato."""
    v = 1 + 0.002 * math.sin(2 * math.pi * 4.5 * t)
    return sum(math.sin(2 * math.pi * freq * k * v * t) / k ** 1.8 for k in range(1, harmonics + 1))


def variant_h(t):
    chord = ((0.0, 146.83), (0.07, 220.0), (0.14, 369.99), (0.21, 554.37), (0.28, 659.25))   # D3 A3 F#4 C#5 E5
    return sum(epiano(t - st, f) * (1.0 if f < 400 else 0.7) for st, f in chord)


def variant_i(t):
    pad = env(t, 0.0, 0.45, 1.75) * sum(warm(t, f) * a for f, a in ((87.31, 0.9), (174.61, 0.7), (220.0, 0.5),
                                                                       (261.63, 0.45), (329.63, 0.35)))
    ping = 0.35 * bell(t - 0.55, 698.46, 0.5)                     # F5, soft
    return pad + ping


def variant_j(t):
    sub = env(t, 0, 0.02, 0.5) * math.sin(2 * math.pi * 55 * t)
    spin = min(t / 0.7, 1.0)
    carrier = 110 * 2 ** (2 * spin)                                # 110 Hz up to 440 Hz
    fm = env(t, 0.03, 0.35, 0.8) * 0.45 * math.sin(2 * math.pi * carrier * t + (1.2 - spin) * math.sin(2 * math.pi * 2 * carrier * t))
    land = env(t, 0.62, 0.08, 1.0) * sum(warm(t, f, 4) * a for f, a in ((110.0, 0.8), (164.81, 0.6), (277.18, 0.45)))
    return sub + fm + land + 0.25 * bell(t - 0.66, 440.0, 0.45)


def warm_lit(t, freq, light):
    """warm() whose top opens up as light goes from 0 (dark, almost a sine) to 1 (like warm())."""
    roll = 3.2 - 1.4 * light
    return sum(math.sin(2 * math.pi * freq * k * t) / k ** roll for k in range(1, 7))


def variant_k(t):
    pad = env(t, 0.0, 0.6, 2.0) * sum(warm(t, f) * a for f, a in ((69.30, 0.9), (138.59, 0.7), (207.65, 0.5),
                                                                     (261.63, 0.4), (311.13, 0.35), (349.23, 0.25)))
    return pad + 0.3 * bell(t - 0.65, 554.37, 0.5) + 0.25 * bell(t - 0.85, 830.61, 0.55)


def variant_l(t):
    light = min(t / 1.2, 1.0)
    pad = env(t, 0.0, 0.5, 1.9) * sum(warm_lit(t, f, light) * a for f, a in ((98.0, 0.9), (196.0, 0.7), (246.94, 0.5),
                                                                               (293.66, 0.45), (369.99, 0.3)))
    return pad + 0.3 * bell(t - 0.7, 987.77, 0.45) + 0.28 * bell(t - 0.88, 739.99, 0.55)


def variant_m(t):
    pad = env(t, 0.0, 0.3, 1.4) * sum(warm(t, f, 4) * a for f, a in ((77.78, 0.9), (155.56, 0.7), (196.0, 0.5),
                                                                        (233.08, 0.45), (293.66, 0.35)))
    return pad + 0.55 * epiano(t - 0.42, 622.25, 0.6)


VARIANTS = {"a": (variant_a, 1.3), "b": (variant_b, 1.4), "c": (variant_c, 1.1), "d": (variant_d, 1.6),
            "e": (variant_e, 1.6), "f": (variant_f, 1.5), "g": (variant_g, 1.7),
            "h": (variant_h, 1.8), "i": (variant_i, 1.8), "j": (variant_j, 1.7),
            "k": (variant_k, 2.1), "l": (variant_l, 2.0), "m": (variant_m, 1.5)}


def write(path, key):
    fn, length = VARIANTS[key]
    samples = [fn(i / RATE) for i in range(int(RATE * length))]
    fade = int(RATE * 0.06)                     # no click at the end
    for i in range(fade):
        samples[-1 - i] *= i / fade
    scale = 32767 * 10 ** (PEAK_DB / 20) / max(abs(s) for s in samples)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(array.array("h", (round(s * scale) for s in samples)).tobytes())
    print(f"wrote {path}")


if __name__ == "__main__":
    if "--all" in sys.argv:
        out = Path(sys.argv[sys.argv.index("--all") + 1])
        out.mkdir(parents=True, exist_ok=True)
        for key in VARIANTS:
            write(out / f"ready_{key}.wav", key)
    else:
        write(Path(__file__).resolve().parent / "ready.wav", CHOSEN)
