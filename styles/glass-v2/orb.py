#!/usr/bin/env python3
"""Render the Jarvis orb as a seamless loop of RGBA frames for an LVGL animimg.

A glowing liquid sphere: Siri-like colour lights (iOS blue, purple, pink, cyan)
swirl inside it, the rim glows with the colour behind it, a soft specular
highlight sits top-left, and an outer glow breathes with the loop. Every moving
term uses whole cycles per loop, so the last frame flows into the first.

Usage: orb.py [frames] [size]     (default 24 frames of 220x220 px)
Writes assets/orb/orb_00.png ... and prints the flash cost as RGB565 + alpha.
Needs numpy and Pillow.
"""
import os
import sys

import numpy as np
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
COLORS = np.array([
    [0x0A, 0x84, 0xFF],   # iOS blue
    [0xAF, 0x52, 0xDE],   # iOS purple
    [0xFF, 0x37, 0x5F],   # iOS pink
    [0x64, 0xD2, 0xFF],   # iOS cyan
    [0x5E, 0x5C, 0xE6],   # iOS indigo
], dtype=np.float32) / 255.0
BASE = np.array([0.03, 0.04, 0.12], dtype=np.float32)
# (start angle, orbit radius, orbit wobble, speed in turns per loop, size, strength) per light
LIGHTS = [
    (0.0, 0.42, 0.10, 1, 0.48, 1.10),
    (1.6, 0.48, 0.12, -1, 0.42, 1.00),
    (3.1, 0.40, 0.08, 2, 0.40, 0.95),
    (4.5, 0.50, 0.10, -2, 0.38, 0.85),
    (5.6, 0.30, 0.06, 1, 0.55, 0.60),
]


def interior(u, v, t, radius):
    """Colour of the swirling liquid at sphere coordinates (u, v), time t in [0, 1)."""
    r = np.hypot(u, v) / radius
    # Twist: inner layers turn further than the rim, and the twist itself sways.
    twist = 2 * np.pi * t + 1.1 * (1 - np.clip(r, 0, 1)) * np.sin(2 * np.pi * t)
    cu, cv = np.cos(twist), np.sin(twist)
    ur, vr = u * cu - v * cv, u * cv + v * cu
    col = np.zeros(u.shape + (3,), np.float32) + BASE
    for k, (a0, orbit, wob, speed, size, strength) in enumerate(LIGHTS):
        ang = a0 + 2 * np.pi * speed * t
        rad = radius * (orbit + wob * np.sin(2 * np.pi * (t * (k % 2 + 1)) + a0))
        bx, by = rad * np.cos(ang), rad * np.sin(ang)
        s = radius * size
        w = strength * np.exp(-((ur - bx) ** 2 + (vr - by) ** 2) / (2 * s * s))
        col += COLORS[k][None, None, :] * w[..., None]
    col = 1 - np.exp(-col * 1.2)   # soft tone map keeps overlaps from clipping
    lum = (col @ np.array([0.2126, 0.7152, 0.0722], np.float32))[..., None]
    return np.clip(lum + (col - lum) * 1.35, 0, 1)   # keep the colours vivid, not milky


def frame(i, n, size):
    t = i / n
    c = size / 2
    yy, xx = np.mgrid[0:size, 0:size].astype(np.float32) + 0.5
    x, y = xx - c, yy - c
    r = np.hypot(x, y)
    radius = size * 0.33 * (1 + 0.025 * np.sin(2 * np.pi * 2 * t))   # gentle breathing

    # Sphere body: liquid colour, depth shading and a bright fresnel rim.
    z = np.sqrt(np.clip(1 - (r / radius) ** 2, 0, 1))
    body = interior(x, y, t, radius)
    rim_col = interior(x / np.maximum(r, 1e-3) * radius * 0.85, y / np.maximum(r, 1e-3) * radius * 0.85, t, radius)
    body = body * (0.55 + 0.45 * z[..., None]) + rim_col * ((1 - z) ** 3)[..., None] * 0.9
    # Specular highlight top-left and a faint bounce light bottom-right.
    hx, hy = -0.36 * radius, -0.42 * radius
    spec = 0.45 * np.exp(-((x - hx) ** 2 / (2 * (0.22 * radius) ** 2) + (y - hy) ** 2 / (2 * (0.13 * radius) ** 2)))
    bounce = 0.12 * np.exp(-((x - 0.3 * radius) ** 2 + (y - 0.45 * radius) ** 2) / (2 * (0.25 * radius) ** 2))
    body = body + (1 - body) * (spec + bounce)[..., None]
    cover = np.clip(radius - r + 0.5, 0, 1)

    # Outer glow in the colour of the rim, breathing with the loop, fading to zero before the frame edge.
    breath = 1 + 0.18 * np.sin(2 * np.pi * t)
    out_r = np.maximum(r - radius, 0)
    glow = 0.90 * np.exp(-out_r / (14 * breath)) + 0.34 * np.exp(-out_r / (30 * breath))
    glow *= np.clip((c * 0.97 - r) / (c * 0.3), 0, 1) ** 2
    glow_col = 1 - (1 - rim_col) * 0.88   # a touch whiter than the rim

    alpha = cover + (1 - cover) * glow
    rgb = (body * cover[..., None] + glow_col * ((1 - cover) * glow)[..., None]) / np.maximum(alpha, 1e-4)[..., None]
    rgba = np.dstack([np.clip(rgb, 0, 1), np.clip(alpha, 0, 1)])
    return Image.fromarray((rgba * 255 + 0.5).astype(np.uint8), "RGBA")


def main():
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 24
    size = int(sys.argv[2]) if len(sys.argv) > 2 else 220
    out_dir = os.path.join(HERE, "assets", "orb")
    os.makedirs(out_dir, exist_ok=True)
    for old in os.listdir(out_dir):
        if old.startswith("orb_") and old.endswith(".png"):
            os.remove(os.path.join(out_dir, old))
    for i in range(n):
        frame(i, n, size).save(os.path.join(out_dir, f"orb_{i:02d}.png"))
    per = size * size * 3   # RGB565 (2 bytes) + 8-bit alpha (1 byte) per pixel
    print(f"Wrote {n} frames of {size}x{size} px to {out_dir}")
    print(f"Flash as RGB565 + alpha: {per:,} B per frame, {per * n:,} B total ({per * n / 1048576:.2f} MiB)")


if __name__ == "__main__":
    main()
