#!/usr/bin/env python3
"""Pre-render the Jarvis orb as looping RGBA frames for an LVGL `animimg`.

LVGL plays the frames as images, so everything here (glow, rings, dots) is drawn once,
offline. Every style loops seamlessly: a ring of n identical strokes turns by exactly
one stroke spacing per loop, a sphere of n meridians by one meridian, breathing by one period.

Usage (needs numpy and Pillow):
  python3 orbs/render.py <style> [--frames 24] [--size 240]
Styles: lowkey, hud, geist, starry. Writes orbs/<style>/frame_00.png …
"""
import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image

TAU = 2 * math.pi


def rgb(h):
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)], np.float32)


class Canvas:
    """Premultiplied RGBA accumulator on a [-1, 1] square."""

    def __init__(self, s):
        self.s = s
        self.px = 2 / s
        y, x = np.mgrid[0:s, 0:s].astype(np.float32)
        self.x = (x + 0.5) / s * 2 - 1
        self.y = (y + 0.5) / s * 2 - 1
        self.r = np.hypot(self.x, self.y)
        self.th = np.arctan2(self.y, self.x)
        self.c = np.zeros((s, s, 3), np.float32)
        self.a = np.zeros((s, s), np.float32)

    def over(self, color, alpha):
        alpha = np.clip(alpha, 0, 1)
        col = np.broadcast_to(color, self.c.shape) if np.ndim(color) == 1 else color
        self.c = col * alpha[..., None] + self.c * (1 - alpha[..., None])
        self.a = alpha + self.a * (1 - alpha)

    def add(self, color, alpha):
        """Additive light: brightens what is there, and shows on its own as a glow."""
        alpha = np.clip(alpha, 0, 1)
        col = np.broadcast_to(color, self.c.shape) if np.ndim(color) == 1 else color
        self.c = self.c + col * alpha[..., None]
        self.a = 1 - (1 - self.a) * (1 - alpha)

    def image(self):
        a = np.clip(self.a, 0, 1)
        c = np.clip(self.c / np.maximum(a[..., None], 1e-6), 0, 1)
        out = np.dstack([c, a])
        return Image.fromarray((out * 255 + 0.5).astype(np.uint8), "RGBA")


def smooth(e0, e1, v):
    t = np.clip((v - e0) / (e1 - e0), 0, 1)
    return t * t * (3 - 2 * t)


def band(cv, radius, width):
    """Anti-aliased ring of the given width (in [-1, 1] units)."""
    return np.clip(1 - (np.abs(cv.r - radius) - width / 2) / cv.px, 0, 1)


def dashes(cv, n, duty, rot, soft=0.03):
    """1 inside each of n dashes around the circle, rotated by rot radians."""
    u = (((cv.th - rot) / TAU) * n) % 1.0
    return smooth(0, soft, u) * smooth(duty, duty - soft, u)


# ---------------------------------------------------------------- styles

def lowkey(t, s):
    """Quiet: a soft pearl that breathes, with one slow colour drifting inside."""
    cv = Canvas(s)
    b = 0.5 - 0.5 * math.cos(TAU * t)                     # 0 → 1 → 0
    R = 0.50 + 0.03 * b
    R = 0.42 + 0.025 * b
    cv.add(rgb("#8EA2D8"), smooth(0.9, R * 0.95, cv.r) * (0.06 + 0.05 * b))
    disk = smooth(R, R - 0.06, cv.r)
    shade = np.clip(cv.r / R, 0, 1)[..., None]
    base = rgb("#C9D2E3") * (1 - shade) + rgb("#7D8AA6") * shade
    cv.over(base, disk * 0.62)
    dx, dy = 0.13 * math.cos(TAU * t), 0.13 * math.sin(TAU * t)
    blob = np.exp(-(((cv.x - dx) ** 2 + (cv.y - dy) ** 2) / 0.07)) * disk
    cv.over(rgb("#A99FD8"), blob * 0.35)
    hi = np.exp(-(((cv.x + 0.14) ** 2 + (cv.y + 0.16) ** 2) / 0.02)) * disk
    cv.add(rgb("#FFFFFF"), hi * 0.18)
    return cv.image()


def hud(t, s):
    """Futuristic: rotating segmented rings, a radar sweep and a pulsing core."""
    cv = Canvas(s)
    cyan, amber, white = rgb("#5EE6FF"), rgb("#FFB020"), rgb("#EAF6FF")
    # radar sweep: one turn per loop, fading tail
    lag = ((TAU * t - cv.th) % TAU)
    cv.add(cyan, np.exp(-lag * 1.3) * smooth(0.64, 0.6, cv.r) * smooth(0.12, 0.2, cv.r) * 0.42)
    # tick ring
    ticks = dashes(cv, 72, 0.18, TAU / 72 * t, 0.05)
    cv.over(white, band(cv, 0.80, 0.045) * ticks * 0.45)
    # outer arcs, counter-rotating
    cv.over(cyan, band(cv, 0.66, 0.018) * dashes(cv, 6, 0.72, -TAU / 6 * t) * 0.9)
    # middle dashed ring
    cv.over(cyan, band(cv, 0.50, 0.03) * dashes(cv, 24, 0.55, TAU / 24 * t) * 0.75)
    # inner thin ring
    cv.over(white, band(cv, 0.34, 0.008) * 0.5)
    # amber marker orbiting on the outer arcs
    mx, my = 0.66 * math.cos(TAU * t), 0.66 * math.sin(TAU * t)
    dot = np.exp(-(((cv.x - mx) ** 2 + (cv.y - my) ** 2) / 0.0012))
    cv.add(amber, dot)
    # core
    p = 0.5 - 0.5 * math.cos(TAU * 2 * t)
    rc = 0.13 + 0.015 * p
    cv.add(cyan, np.exp(-(cv.r / (rc * 2.2)) ** 2) * (0.35 + 0.2 * p))
    cv.over(white, smooth(rc, rc - 0.02, cv.r))
    return cv.image()


def geist(t, s):
    """Vercel-like: a monochrome dot-matrix globe turning one meridian per loop."""
    cv = Canvas(s)
    R, n_lon, n_lat = 0.66, 24, 11
    tilt = math.radians(18)
    rot = TAU / n_lon * t
    color = rgb("#EDEDED")
    cv.over(color, band(cv, R + 0.12, cv.px * 0.9) * 0.22)
    points = []
    for i in range(n_lat):
        lat = (i + 1) / (n_lat + 1) * math.pi - math.pi / 2
        for j in range(n_lon):
            lon = TAU * j / n_lon + rot
            x, y, z = math.cos(lat) * math.sin(lon), math.sin(lat), math.cos(lat) * math.cos(lon)
            y, z = y * math.cos(tilt) - z * math.sin(tilt), y * math.sin(tilt) + z * math.cos(tilt)
            points.append((z, x, y))
    for z, x, y in sorted(points):                                # back to front
        depth = (z + 1) / 2                                       # 0 back, 1 front
        size = 0.010 + 0.016 * depth
        alpha = 0.16 + 0.84 * depth ** 1.6
        d2 = (cv.x - x * R) ** 2 + (cv.y + y * R) ** 2
        cv.over(color, np.clip(1 - (np.sqrt(d2) - size) / cv.px, 0, 1) * alpha)
    return cv.image()


def starry(t, s):
    """Van Gogh star: a glowing core with concentric brushstroke rings, each turning by
    one stroke per loop in alternating directions, yellow inside fading to night blue."""
    cv = Canvas(s)
    rng = np.random.default_rng(7)
    grain = rng.random((s, s)).astype(np.float32)
    cv.add(rgb("#3B6FD8"), np.exp(-((cv.r - 0.62) / 0.22) ** 2) * 0.22)
    rings = [  # radius, strokes, duty, width, colour, direction
        (0.70, 11, 0.62, 0.050, "#1E3E8C", -1),
        (0.62, 13, 0.70, 0.052, "#2F63B8", 1),
        (0.54, 9, 0.66, 0.050, "#5C8FD6", -1),
        (0.46, 11, 0.72, 0.048, "#9CC3E6", 1),
        (0.385, 7, 0.68, 0.046, "#D9E6C8", -1),
        (0.315, 9, 0.74, 0.044, "#F2DB7A", 1),
        (0.25, 7, 0.70, 0.040, "#F5C842", -1),
    ]
    for radius, n, duty, width, col, direction in rings:
        rot = direction * TAU / n * t
        u = (((cv.th - rot) / TAU) * n) % 1.0
        along = np.clip(u / duty, 0, 1)
        taper = np.clip(np.sin(np.pi * along), 0, 1) ** 0.6 * (u < duty)        # thick middle, thin ends
        wobble = 0.012 * np.sin(np.pi * along * 2)               # same for every stroke
        dist = np.abs(cv.r - (radius + wobble))
        stroke = np.clip(1 - (dist - width * 0.5 * taper) / cv.px, 0, 1) * (taper > 0.02)
        stroke *= 0.78 + 0.22 * grain                             # paint texture
        cv.over(rgb(col), stroke)
    # star core with halo
    p = 0.5 - 0.5 * math.cos(TAU * t)
    cv.add(rgb("#F5D042"), np.exp(-(cv.r / (0.26 + 0.02 * p)) ** 2) * 0.55)
    cv.over(rgb("#FFF6C8") * 0.6 + rgb("#F5D042") * 0.4, smooth(0.17, 0.14, cv.r))
    cv.add(rgb("#FFFFFF"), np.exp(-(cv.r / 0.08) ** 2) * 0.6)
    return cv.image()


STYLES = {"lowkey": lowkey, "hud": hud, "geist": geist, "starry": starry}

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("style", choices=STYLES)
    ap.add_argument("--frames", type=int, default=24)
    ap.add_argument("--size", type=int, default=240)
    args = ap.parse_args()
    out = Path(__file__).resolve().parent / args.style
    out.mkdir(exist_ok=True)
    for i in range(args.frames):
        STYLES[args.style](i / args.frames, args.size).save(out / f"frame_{i:02d}.png")
    print(f"{args.frames} frames → {out}")
