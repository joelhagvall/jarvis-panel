#!/usr/bin/env python3
"""The Jarvis line: the voice page's orb as a free glowing line on the painting, with no glass sphere.

Joel chose it on 2026-10-28 over the plasma glass, which felt heavy. The same states and frame sets as
glass_variants.py's jarvis orb, so the panel's director (styles/glass-v2/orb_director.h) runs it unchanged:

  calm    a short line with a slight ripple and a soft light gliding along it and back once per loop
          (5.2 s per loop while listening, quicker with your voice, eased to 1.8 s while thinking)
  swing   the line growing into the voice wave (calm_00 = swing_00, swing_23 = talk_00)
  talk    the three-line voice wave, full width; the panel draws it live on hollow (voice_wave.h)
  rest    the wave settling into a flat line (rest_17 = settle_05 = the drawn wave at height 0)
  hollow  nothing: fully transparent, the panel draws the wave on it
  listen, morph, speak, settle: the older 24/8/24/6 sets, compiled into the simulator without --remote

Light is additive on black and tone-mapped as 1 - exp(-1.15 light); the alpha is the brightest channel, so
the painting shows everywhere the line is dark. voice_wave.h uses the same formula.

  python3 orbs/line_orb.py      writes orbs/line-jarvis/<state>_NN.png (needs numpy + Pillow)
"""
from pathlib import Path

import numpy as np
from PIL import Image

import glass_variants as gv

HERE = Path(__file__).resolve().parent
OUT = HERE / "line-jarvis"
SIZE = 220
TAU = 2 * np.pi
WIDTH = SIZE * 0.42     # the wave's half-width scale (voice_wave.h RADIUS)
HEIGHT = SIZE * 0.33    # the wave's height scale, as the glass wave's (voice_wave.h HEIGHT)
END = WIDTH * 0.94      # the last sample on each side (voice_wave.h END)
CALM_LENGTH = 0.71      # the listening line's length, of the wave's
CALM_BRIGHT = 0.6       # its brightness, of one wave line's
FADE = 0.35             # each end fades out over this part of the line's half (voice_wave.h FADE)
WAVES = [  # colour, amplitude, (harmonic, cycles per loop, phase) ... as voice_wave.h LINES
    (gv.CYAN, 0.55, [(1.5, 1, 0.0), (3.0, 2, 1.3), (5.0, -2, 2.1)]),
    (gv.VIOLET, 0.40, [(2.0, -1, 0.7), (3.5, 2, 2.5), (6.0, 3, 0.4)]),
    (gv.ICE, 0.28, [(2.5, 2, 1.9), (4.5, -1, 0.2), (7.0, 3, 1.1)]),
]

x, y, r, _, c = gv.grid(SIZE)


def rgba(light):
    lit = 1 - np.exp(-1.15 * light)
    a = np.clip(lit.max(axis=-1), 0, 1)
    rgb = lit / np.maximum(a, 1e-6)[..., None]
    return np.dstack([np.clip(rgb, 0, 1), a])


def curve(parts, t, xs, length=1.0):
    """One line's height along xs (before its amplitude), quiet at its ends."""
    env = np.maximum(np.cos(np.clip(xs / (WIDTH * length), -1, 1) * np.pi / 2), 0) ** 1.6
    ys = sum(np.sin(h * np.pi * xs / WIDTH + TAU * n * t + p) / (1 + i * 0.7) for i, (h, n, p) in enumerate(parts))
    return ys / 1.9 * env


def fade(px, half):
    """1 along the middle, easing to 0 at the line's ends, by the pixel's column."""
    m = np.clip((half - np.abs(px)) / (FADE * half), 0, 1)
    return m * m * (3 - 2 * m)


def add_line(light, xs, ys, col, strength, glint=None):
    core, halo = gv.glow_line(x, y, xs, ys, 1.0, 3.4)
    g = fade(x[0], float(xs[-1]))[None, :]       # brightness by the pixel's column
    if glint is not None:
        g = g * np.interp(x[0], xs, glint)[None, :]
    core, halo = core * g, halo * g
    light += (gv.WHITE * (core * 0.8)[..., None] + col * halo[..., None]) * strength


def wave(t, amps, length=1.0, strengths=(1, 1, 1), calm=None):
    """The voice wave at phase t with each line's height (talk_k: t = k/72, height 0.75 + 0.25 sin(4 pi t)).
    calm: (phase, weight) of the listening line mixed into the first line, for the swing."""
    light = np.zeros(x.shape + (3,), np.float32)
    xs = np.linspace(-END * length, END * length, 420, dtype=np.float32)
    for k, (col, amp, parts) in enumerate(WAVES):
        if strengths[k] <= 0:
            continue
        ys = curve(parts, t, xs, length) * amp * HEIGHT * amps[k]
        glint = None
        if k == 0 and calm is not None:
            ct, w = calm
            ys = ys + calm_y(ct, xs) * w
            glint = 1 + (calm_glint(ct, xs, length) - 1) * w
        add_line(light, xs, ys, col, strengths[k], glint)
    return light


def calm_y(t, xs):
    """The listening line's slight ripple: whole cycles per loop, so the loop closes."""
    env = np.maximum(np.cos(np.clip(xs / (WIDTH * CALM_LENGTH), -1, 1) * np.pi / 2), 0) ** 1.6
    ripple = 0.6 * np.sin(2.2 * np.pi * xs / WIDTH + TAU * t) + 0.4 * np.sin(3.6 * np.pi * xs / WIDTH - TAU * 2 * t + 1.1)
    return ripple * env * HEIGHT * 0.045


def calm_glint(t, xs, length):
    """A soft light gliding along the line and back once per loop, over a dimmer line."""
    pos = np.sin(TAU * t) * END * CALM_LENGTH * 0.7
    return CALM_BRIGHT * (1 + 1.6 * np.exp(-((xs - pos) / 16) ** 2))


def calm_frame(t):
    light = np.zeros(x.shape + (3,), np.float32)
    xs = np.linspace(-END * CALM_LENGTH, END * CALM_LENGTH, 420, dtype=np.float32)
    add_line(light, xs, calm_y(t, xs), gv.CYAN, 1.0, calm_glint(t, xs, CALM_LENGTH))
    return rgba(light)


def swing_mix(t_calm, e):
    """Between the listening line (e 0) and talk_00 (e 1)."""
    length = CALM_LENGTH + (1 - CALM_LENGTH) * e
    h = 0.75 * e
    return rgba(wave(0.0, (h, h, h), length, (1, e, e), calm=(t_calm, 1 - e)))


def talk_frame(t):
    h = 0.75 + 0.25 * np.sin(2 * TAU * t)
    return rgba(wave(t, (h, h, h)))


def rest_frame(t, a):
    h = 0.75 * a
    return rgba(wave(t, (h, h, h)))


def sets():
    swing = []
    for j in range(gv.SWING):
        m = j / (gv.SWING - 1)
        e = m * m * (3 - 2 * m)
        swing.append(calm_frame(0.0) if j == 0 else talk_frame(0.0) if j == gv.SWING - 1
                     else swing_mix(gv.SWING_TURN * m, e))
    rest = []
    for j in range(gv.REST):
        m = j / (gv.REST - 1)
        rest.append(rest_frame(gv.REST_TURN * m, (1 - m) ** 2 * (1 + 2 * m)))
    morph = []
    for j in range(8):
        m = (j + 1) / 8
        e = m * m * (3 - 2 * m)
        morph.append(talk_frame(0.0) if j == 7 else swing_mix(j / 24, e))
    return {
        "calm": [calm_frame(i / gv.CALM) for i in range(gv.CALM)],
        "talk": [talk_frame(i / gv.TALK) for i in range(gv.TALK)],
        "swing": swing,
        "rest": rest,
        "hollow": [np.zeros((SIZE, SIZE, 4), np.float32)],
        "listen": [calm_frame(i / 24) for i in range(24)],
        "morph": morph,
        "speak": [talk_frame(i / 24) for i in range(24)],
        "settle": [rest_frame(0.0, 1 - (j + 1) / 6) for j in range(6)],
    }


def main():
    OUT.mkdir(exist_ok=True)
    total = 0
    for key, frames in sets().items():
        for i, frame in enumerate(frames):
            Image.fromarray((frame * 255 + 0.5).astype(np.uint8), "RGBA").save(OUT / f"{key}_{i:02d}.png", optimize=True)
        total += len(frames)
    print(f"line: {total} frames -> {OUT}")


if __name__ == "__main__":
    main()
