#!/usr/bin/env python3
"""Jarvis orbs for the glass-v2 voice page, made to feel like Jarvis rather than Siri.

  plasma        a clear glass sphere with electric filaments running from a bright core
                to the glass, like a plasma ball
  voicewave     the plasma glass with glowing voice waves flowing through it instead of
                filaments, as if Jarvis is speaking
  jarvis        the orb as states, no status label needed: plasma while listening (played
                faster while thinking), filaments swinging into the voice wave, the wave while
                speaking, and the wave settling into a flat line at the end. calm_* and talk_* are
                the same plasma and wave loops drawn CALM and TALK times, so the panel can play them
                slower without blending far-apart frames into double filaments.

The glass is partly see-through: dark parts let the painting show, lit parts are opaque.
Each variant writes 24 looping RGBA frames (RGB565 + alpha on the board) and a matching
orb-page background, the same blurred painting as glass-v2 with a bloom and edge glow in
the orb's own colours. Every moving term turns a whole number of cycles per loop.

Usage (numpy + Pillow): python3 orbs/glass_variants.py <plasma|voicewave|jarvis> [frames] [size]
Writes orbs/glass-<variant>/orb_00.png … (jarvis: listen_, morph_, speak_, settle_) and bg_orb.png
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "styles" / "glass-v2"))
from bake import ORB_CENTER, H, W, gblur, load_painting, luminance, saturate  # noqa: E402

TAU = 2 * np.pi


def hexes(*codes):
    return np.array([[int(c[i:i + 2], 16) for i in (1, 3, 5)] for c in codes], np.float32) / 255


CYAN, ICE, WHITE, VIOLET = hexes("#38D9FF", "#BFF4FF", "#FFFFFF", "#8C5CFF")

VARIANTS = {
    "plasma": {"glass": hexes("#0C1230")[0], "rim": hexes("#6FA8FF")[0], "bloom": (0.42, 0.36, 0.95),
               "edge": hexes("#38D9FF", "#8C5CFF", "#2A8CFF", "#B56CFF", "#38D9FF"), "edge_strength": 1.0},
    "jarvis": {"glass": hexes("#0C1230")[0], "rim": hexes("#6FA8FF")[0], "bloom": (0.42, 0.36, 0.95),
               "edge": hexes("#38D9FF", "#8C5CFF", "#2A8CFF", "#B56CFF", "#38D9FF"), "edge_strength": 0.9},
    "voicewave": {"glass": hexes("#0B1430")[0], "rim": hexes("#62B8FF")[0], "bloom": (0.30, 0.45, 0.95),
                  "edge": hexes("#38D9FF", "#62B8FF", "#8C5CFF", "#62B8FF", "#38D9FF"), "edge_strength": 0.8},
}


def grid(size):
    c = size / 2
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    x, y = xx - c, yy - c
    return x, y, np.hypot(x, y), np.arctan2(y, x), c


def glow_line(x, y, px, py, core_sigma, halo_sigma):
    """Thin bright core and a soft halo around a polyline given as sample points."""
    d2 = (x[..., None] - px) ** 2 + (y[..., None] - py) ** 2
    return np.exp(-d2 / (2 * core_sigma ** 2)).max(axis=-1), np.exp(-d2 / (2 * halo_sigma ** 2)).max(axis=-1)


def glass_sphere(light, var, x, y, r, c, R, t, transparency=0.5):
    """Wrap an inner light (RGB, additive) in a glass sphere and return RGBA."""
    z = np.sqrt(np.clip(1 - (r / R) ** 2, 0, 1))
    cover = np.clip(R - r + 0.5, 0, 1)
    tint = var["glass"][None, None, :] * (0.45 + 0.55 * z[..., None])
    rim = var["rim"][None, None, :] * ((1 - z) ** 3)[..., None] * 0.95
    hx, hy = -0.38 * R, -0.44 * R
    spec = 0.55 * np.exp(-((x - hx) ** 2 / (2 * (0.20 * R) ** 2) + (y - hy) ** 2 / (2 * (0.10 * R) ** 2)))
    lit = 1 - np.exp(-(tint + rim + light) * 1.15)
    lit = lit + (1 - lit) * spec[..., None]
    glow_amount = light @ np.array([0.3, 0.5, 0.2], np.float32)
    inside_alpha = np.clip(transparency + (1 - transparency) * np.clip(glow_amount * 1.4 + (1 - z) ** 2 + spec, 0, 1), 0, 1)
    breath = 1 + 0.15 * np.sin(TAU * t)
    out_r = np.maximum(r - R, 0)
    glow = (0.75 * np.exp(-out_r / (11 * breath)) + 0.28 * np.exp(-out_r / (26 * breath))) * var.get("glow", 1.0)
    glow *= np.clip((c * 0.97 - r) / (c * 0.3), 0, 1) ** 2
    glow_col = 1 - (1 - var["rim"][None, None, :]) * 0.7
    a_in = cover * inside_alpha
    alpha = a_in + (1 - cover) * glow
    rgb = (lit * a_in[..., None] + glow_col * ((1 - cover) * glow)[..., None]) / np.maximum(alpha, 1e-4)[..., None]
    return np.dstack([np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)])


def plasma_light(t, x, y, r, R, pull=0.0):
    """Filaments from the core to the glass. pull 0..1 swings them flat towards the
    horizontal axis and calms them, so they can turn into the voice wave."""
    light = np.zeros(x.shape + (3,), np.float32)
    flick = 0.5 - 0.5 * np.cos(TAU * 3 * t)
    light += (WHITE * 0.7 + VIOLET * 0.3) * (np.exp(-(r / (R * 0.13)) ** 2) * (1.8 + 0.4 * flick) * (1 - 0.7 * pull))[..., None]
    light += VIOLET * (np.exp(-(r / (R * 0.35)) ** 2) * 0.35)[..., None]
    rng = np.random.default_rng(3)
    for k in range(7):
        a0 = rng.uniform(0, TAU)
        turns = (1, -1, 2, -1, 1, -2, 1)[k]
        freq, amp = rng.uniform(5, 9), rng.uniform(0.05, 0.10) * R
        rho = np.linspace(R * 0.12, R * 0.97, 70, dtype=np.float32)
        phase = TAU * (k % 3 + 1) * t + a0
        ang = a0 + TAU * turns * t
        if pull > 0:
            target = 0.0 if np.cos(ang) >= 0 else np.pi
            ang = ang + ((target - ang + np.pi) % TAU - np.pi) * pull
        bend = amp * np.sin(freq * rho / R + phase) * (rho / R) * (1 - pull)
        px = rho * np.cos(ang) - bend * np.sin(ang)
        py = rho * np.sin(ang) + bend * np.cos(ang)
        strength = 0.6 + 0.4 * np.sin(TAU * (k % 2 + 1) * t + k)
        core_line, halo = glow_line(x, y, px, py, 0.9, 3.2)
        tip = np.clip(r / R, 0, 1)
        col = CYAN * (1 - tip)[..., None] + VIOLET * tip[..., None]
        light += (WHITE * core_line[..., None] * 0.9 + col * halo[..., None] * 0.9) * strength
        light += ICE * (np.exp(-((x - px[-1]) ** 2 + (y - py[-1]) ** 2) / (2 * 3.0 ** 2)) * 1.2 * strength * (1 - pull))[..., None]
    return light


def plasma_frame(t, size, var):
    x, y, r, th, c = grid(size)
    R = size * 0.33
    return glass_sphere(plasma_light(t, x, y, r, R), var, x, y, r, c, R, t, transparency=0.47)


def wave_light(t, x, y, r, R, amp_scale=1.0):
    """Three glowing waves across the sphere, tapering at the glass, like a voice."""
    light = np.zeros(x.shape + (3,), np.float32)
    light += (WHITE * 0.6 + CYAN * 0.4) * (np.exp(-(r / (R * 0.45)) ** 2) * 0.18)[..., None]
    xs = np.linspace(-R * 0.94, R * 0.94, 420, dtype=np.float32)
    env = np.cos(np.clip(xs / R, -1, 1) * np.pi / 2) ** 1.6     # quiet at the glass, loud in the middle
    speak = 0.75 + 0.25 * np.sin(TAU * 2 * t)                    # amplitude breathes like speech
    waves = [  # colour, amplitude, (harmonic, cycles per loop, phase) ...
        (CYAN, 0.55, [(1.5, 1, 0.0), (3.0, 2, 1.3), (5.0, -2, 2.1)]),
        (VIOLET, 0.40, [(2.0, -1, 0.7), (3.5, 2, 2.5), (6.0, 3, 0.4)]),
        (ICE, 0.28, [(2.5, 2, 1.9), (4.5, -1, 0.2), (7.0, 3, 1.1)]),
    ]
    for col, amp, parts in waves:
        ys = sum(np.sin(h * np.pi * xs / R + TAU * n * t + p) / (1 + i * 0.7)
                 for i, (h, n, p) in enumerate(parts))
        ys = ys / 1.9 * amp * R * env * speak * amp_scale
        core_line, halo = glow_line(x, y, xs, ys, 1.0, 3.4)
        light += WHITE * (core_line * 0.8)[..., None] + col * (halo * 1.0)[..., None]
    return light


def voicewave_frame(t, size, var):
    x, y, r, th, c = grid(size)
    R = size * 0.33
    return glass_sphere(wave_light(t, x, y, r, R), var, x, y, r, c, R, t, transparency=0.47)


# Drawn frames of the listening loop on the panel (atlas v4): one loop takes 5.2 s there, so a
# filament moves at most 7.5 degrees between drawn frames instead of 30 with the 24 listen_* frames.
CALM = 96
# Drawn frames of the speaking wave on the panel (atlas v5): 3.0 s per loop instead of 2.0 s, and a
# wave term moves at most 15 degrees of phase between drawn frames instead of 45.
TALK = 72


# Drawn transitions for atlas v6. The panel enters them by cross-fading from wherever the loop it
# leaves is, so they no longer jump to a fixed phase; their own motion continues at the speed of the
# state before them. swing_*: 24 frames, the thinking filaments (1.8 s per loop) swinging into the wave
# in 0.65 s; rest_*: 18 frames, the speaking wave (3.0 s per loop) settling into the flat line in 0.6 s.
SWING, REST = 24, 18
SWING_TURN = 0.65 / 1.8     # of a plasma loop while swinging
REST_TURN = 0.6 / 3.0       # of a wave loop while settling


def swing_frame(j, size, var):
    x, y, r, th, c = grid(size)
    R = size * 0.33
    m = j / (SWING - 1)
    e = m * m * (3 - 2 * m)
    t = SWING_TURN * m
    light = plasma_light(t, x, y, r, R, pull=e) * (1 - e) + wave_light(0.0, x, y, r, R, amp_scale=e) * e
    return glass_sphere(light, var, x, y, r, c, R, 0.0, transparency=0.47)


def rest_frame(j, size, var):
    x, y, r, th, c = grid(size)
    R = size * 0.33
    m = j / (REST - 1)
    a = (1 - m) ** 2 * (1 + 2 * m)          # eased from the full wave to the flat line
    return glass_sphere(wave_light(REST_TURN * m, x, y, r, R, amp_scale=a), var, x, y, r, c, R, 0.0, transparency=0.47)


def hollow_frame(size, var):
    """The glass with the wave's soft centre light and no lines: the panel draws the voice wave on it
    (styles/glass-v2/voice_wave.h), so the wave can follow Jarvis's voice instead of a drawn loop."""
    x, y, r, th, c = grid(size)
    R = size * 0.33
    light = np.zeros(x.shape + (3,), np.float32)
    light += (WHITE * 0.6 + CYAN * 0.4) * (np.exp(-(r / (R * 0.45)) ** 2) * 0.18)[..., None]
    return glass_sphere(light, var, x, y, r, c, R, 0.0, transparency=0.47)


def jarvis_sets(n, size, var):
    """Frame sets for the stateful orb. speak_00 equals the last morph frame, so the
    hand-over from morph to speaking is seamless; settle ends on a flat glowing line."""
    x, y, r, th, c = grid(size)
    R = size * 0.33
    sets = {"listen": [plasma_frame(i / n, size, var) for i in range(n)],
            "calm": [plasma_frame(i / CALM, size, var) for i in range(CALM)],
            "talk": [voicewave_frame(i / TALK, size, var) for i in range(TALK)],
            "speak": [voicewave_frame(i / n, size, var) for i in range(n)], "morph": [], "settle": []}
    for j in range(8):
        m = (j + 1) / 8
        e = m * m * (3 - 2 * m)
        light = plasma_light(j / n, x, y, r, R, pull=e) * (1 - e) + wave_light(0.0, x, y, r, R, amp_scale=e) * e
        sets["morph"].append(glass_sphere(light, var, x, y, r, c, R, 0.0, transparency=0.47))
    for j in range(6):
        a = 1 - (j + 1) / 6
        sets["settle"].append(glass_sphere(wave_light(0.0, x, y, r, R, amp_scale=a), var, x, y, r, c, R, 0.0, transparency=0.47))
    sets["swing"] = [swing_frame(j, size, var) for j in range(SWING)]
    sets["rest"] = [rest_frame(j, size, var) for j in range(REST)]
    sets["hollow"] = [hollow_frame(size, var)]
    return sets


def bake_bg(var, out_path, painting=ROOT / "art" / "starry.jpg"):
    """glass-v2's orb background with this orb's bloom and edge colours."""
    art = load_painting(str(painting))
    out = saturate(gblur(art, 28.0), 1.1)
    out *= 0.12 / max(float(luminance(out).mean()), 0.12)
    px, py = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    ox, oy = ORB_CENTER
    bloom = 0.22 * np.exp(-((px - ox) ** 2 + (py - oy) ** 2) / (2 * 150.0 ** 2))
    out = out * (1 - bloom[..., None]) + np.array(var["bloom"]) * bloom[..., None]
    stops = var["edge"]
    u = (np.arctan2(py - H / 2, px - W / 2) / TAU) % 1.0
    pos = u * (len(stops) - 1)
    i0 = np.floor(pos).astype(int)
    fr = (pos - i0)[..., None]
    col = stops[i0] * (1 - fr) + stops[np.minimum(i0 + 1, len(stops) - 1)] * fr
    e = np.minimum.reduce([px, W - px, py, H - py])
    a = np.clip((0.70 * np.exp(-e / 9) + 0.22 * np.exp(-e / 50)) * var["edge_strength"], 0, 1)[..., None]
    out = out * (1 - a) + col * a
    Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out_path)


DRAW = {"plasma": plasma_frame, "voicewave": voicewave_frame}

if __name__ == "__main__":
    name = sys.argv[1]
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 24
    size = int(sys.argv[3]) if len(sys.argv) > 3 else 220
    var = VARIANTS[name]
    out = HERE / f"glass-{name}"
    out.mkdir(exist_ok=True)
    if name == "jarvis":
        total = 0
        for key, frames in jarvis_sets(n, size, var).items():
            for i, rgba in enumerate(frames):
                Image.fromarray((rgba * 255 + 0.5).astype(np.uint8), "RGBA").save(out / f"{key}_{i:02d}.png")
            total += len(frames)
        bake_bg(var, out / "bg_orb.png")
        per = size * size * 3
        print(f"jarvis: {total} frames → {out} ({per * total / 1048576:.2f} MiB as RGB565 + alpha), bg_orb.png")
        sys.exit()
    for i in range(n):
        rgba = DRAW[name](i / n, size, var)
        Image.fromarray((rgba * 255 + 0.5).astype(np.uint8), "RGBA").save(out / f"orb_{i:02d}.png")
    bake_bg(var, out / "bg_orb.png")
    per = size * size * 3
    print(f"{name}: {n} frames → {out} ({per * n / 1048576:.2f} MiB as RGB565 + alpha), bg_orb.png")
