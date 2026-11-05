# Frozen pre-experiment renderer: 88d145f94268ff94b71cbbeecb929de51504baff.
#!/usr/bin/env python3
"""Bake iOS 26 style liquid glass into the panel background.

LVGL cannot blur, refract or light anything behind a widget, so every glass shape
in gen.RECTS is drawn into the background here:

  * continuous corners: a superellipse (n = 5) for widgets, true capsules for buttons
  * refraction: near the rim the glass samples the painting from further out along
    the edge normal, so the background bends into the edge like a thick lens
  * light blur (sigma 3) and a little extra saturation, so the painting stays readable
  * a dark tint chosen per shape from the painting's brightness, so white text reads
    on both dark and light paintings; a shape tinted "blue" gets the iOS blue instead
  * a specular rim, brightest top-left and faint bottom-right, an inner shade at the
    bottom and a soft drop shadow

It also draws the white icons (mic, rain) as PNGs with alpha.

Usage: bake.py <painting.jpg> [out.png]   (default out: assets/bg_<painting>.png)
Needs numpy and Pillow.
"""
import math
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from gen import CLOCK_AREA, RECTS  # noqa: E402

W, H = 1024, 600
CORNER = 30          # visual corner radius of the widgets
SQUIRCLE_N = 5.0     # superellipse exponent for continuous corners
LIGHT = np.array([-0.55, -1.0]) / np.hypot(0.55, 1.0)   # light from the top, a little left
TINT_DARK = np.array([0.03, 0.05, 0.10])
# How dark the glass gets: its mean brightness is pulled towards GLASS_TARGET with a tint of at least GLASS_MIN
# and at most GLASS_MAX, and spots brighter than GLASS_SPOT are softened. Darker since 2026-10-22 (target 0.24,
# 20-60 %, spots over 0.42 before), so small text reads on bright pictures such as Mandelblom or a galaxy.
GLASS_TARGET, GLASS_MIN, GLASS_MAX, GLASS_SPOT = 0.17, 0.32, 0.70, 0.34
TINT_BLUE = np.array([0x0A, 0x84, 0xFF]) / 255.0


def load_painting(path):
    """Cover-crop to 1024x600 with 4 % extra zoom, so scan edges fall outside the screen."""
    im = Image.open(path).convert("RGB")
    scale = max(W / im.width, H / im.height) * 1.04
    im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
    left, top = (im.width - W) // 2, (im.height - H) // 2
    return np.asarray(im.crop((left, top, left + W, top + H)), dtype=np.float32) / 255.0


def luminance(a):
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def gblur(a, sigma):
    """Separable Gaussian blur on a 2D or 3D float array (edge padding)."""
    r = max(1, int(3 * sigma + 0.5))
    k = np.exp(-np.arange(-r, r + 1) ** 2 / (2 * sigma * sigma))
    k /= k.sum()
    out = a
    for axis in (0, 1):
        pad = [(0, 0)] * a.ndim
        pad[axis] = (r, r)
        p = np.pad(out, pad, mode="edge")
        n = out.shape[axis]
        acc = np.zeros_like(out)
        for i, w in enumerate(k):
            sl = [slice(None)] * a.ndim
            sl[axis] = slice(i, i + n)
            acc += w * p[tuple(sl)]
        out = acc
    return out


def saturate(a, amount):
    lum = luminance(a)[..., None]
    return np.clip(lum + (a - lum) * amount, 0, 1)


def sample(img, sx, sy):
    """Bilinear sample img at float coordinates (clamped to the frame)."""
    sx = np.clip(sx, 0, W - 1.001)
    sy = np.clip(sy, 0, H - 1.001)
    x0, y0 = np.floor(sx).astype(int), np.floor(sy).astype(int)
    fx, fy = (sx - x0)[..., None], (sy - y0)[..., None]
    a = img[y0, x0] * (1 - fx) + img[y0, x0 + 1] * fx
    b = img[y0 + 1, x0] * (1 - fx) + img[y0 + 1, x0 + 1] * fx
    return a * (1 - fy) + b * fy


def shape_field(x, y, w, h, kind):
    """Signed distance (px, positive inside) and outward unit normal for the whole frame."""
    px, py = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    cx, cy, hw, hh = x + w / 2, y + h / 2, w / 2, h / 2
    if kind == "capsule":
        radius, n = min(hw, hh), 2.0
    else:
        radius, n = min(CORNER * 1.6, hw, hh), SQUIRCLE_N
    dx, dy = px - cx, py - cy
    ex, ey = np.abs(dx) - (hw - radius), np.abs(dy) - (hh - radius)
    exp_, eyp = np.maximum(ex, 0), np.maximum(ey, 0)
    s = (exp_ ** n + eyp ** n) ** (1 / n)
    s_safe = np.maximum(s, 1e-6)
    gx, gy = (exp_ / s_safe) ** (n - 1), (eyp / s_safe) ** (n - 1)
    g = np.maximum(np.hypot(gx, gy), 1e-6)
    near = (ex > 0) | (ey > 0)
    d = np.where(near, (radius - s) / g, np.minimum(hw - np.abs(dx), hh - np.abs(dy)))
    nx = np.where(near, np.sign(dx) * gx / g, np.where(hw - np.abs(dx) < hh - np.abs(dy), np.sign(dx), 0))
    ny = np.where(near, np.sign(dy) * gy / g, np.where(hw - np.abs(dx) < hh - np.abs(dy), 0, np.sign(dy)))
    return d, nx, ny, px, py


def frame(painting_path, out_path):
    """The painting alone for the Tavla page: the same 1024x600 crop as the background, no glass, no dimming."""
    art = load_painting(painting_path)
    Image.fromarray((np.clip(art, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out_path, optimize=True)


def bake(painting_path, out_path):
    art = load_painting(painting_path)
    out = art.copy()

    # Calm the painting a little: brighter paintings get dimmed more.
    dim = float(np.clip(1 - 0.42 / max(luminance(art).mean(), 1e-3), 0.06, 0.18))
    out *= 1 - dim

    # Soft shade behind the lock-screen clock so white digits read on any painting.
    cx0, cy0, cw, ch = CLOCK_AREA
    px, py = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)
    ell = np.exp(-(((px - (cx0 + cw * 0.36)) / (cw * 0.62)) ** 2 + ((py - (cy0 + ch * 0.42)) / (ch * 0.62)) ** 2))
    clock_lum = luminance(out[cy0:cy0 + ch, cx0:cx0 + cw]).mean()
    out *= 1 - ell[..., None] * float(np.clip(1 - 0.24 / max(clock_lum, 1e-3), 0.18, 0.6))

    fields = {k: shape_field(*r[:4], r[4]) for k, r in RECTS.items()}

    # Drop shadows first, so no shadow lands on a neighbour's glass.
    shadow = np.zeros((H, W), np.float32)
    for key, (x, y, w, h, kind, tint) in RECTS.items():
        cov = np.clip(fields[key][0] + 0.5, 0, 1)
        soft = gblur(np.roll(cov, 8, axis=0), 12)
        shadow = 1 - (1 - shadow) * (1 - 0.34 * soft * (1 - cov))
    out *= (1 - shadow)[..., None]

    # Lightly blurred, slightly more vivid copy of the painting for the glass to show.
    glass_src = saturate(gblur(art, 3.0), 1.3)

    for key, (x, y, w, h, kind, tint) in RECTS.items():
        d, nx, ny, gpx, gpy = fields[key]
        cov = np.clip(d + 0.5, 0, 1)
        band = min(20.0, h * 0.40)
        pull = min(28.0, h * 0.40)
        t = np.clip(1 - d / band, 0, 1) * (d > -1)
        m = pull * t ** 1.6
        # Slight magnification inside, plus the rim pulling in what lies beyond the edge.
        cx, cy = x + w / 2, y + h / 2
        sx = cx + (gpx - cx) * 0.97 + nx * m
        sy = cy + (gpy - cy) * 0.97 + ny * m
        glass = sample(glass_src, sx, sy)

        inside = cov > 0.5
        if tint == "blue":
            glass = glass * 0.38 + TINT_BLUE * 0.62
        else:
            # Soften bright spots (stars, blossoms) so white text never sits on near-white.
            lum_px = luminance(glass)[..., None]
            glass = glass / (1 + 1.3 * np.maximum(lum_px - GLASS_SPOT, 0))
            lum = float(luminance(glass[inside]).mean())
            a = float(np.clip(1 - GLASS_TARGET / max(lum, 1e-3), GLASS_MIN, GLASS_MAX))
            glass = glass * (1 - a) + TINT_DARK * a

        # Top sheen and bottom inner shade give the glass some thickness.
        rel = np.clip((gpy - y) / h, 0, 1)
        # The lens rim also catches a little light.
        sheen = (0.10 * np.exp(-np.maximum(d, 0) / 14) * np.clip(-ny, 0, 1) + 0.05 * np.clip(1 - rel / 0.35, 0, 1)
                 + 0.07 * t ** 2)
        shade = 0.24 * np.exp(-np.maximum(d, 0) / 10) * np.clip(ny, 0, 1) + 0.10 * np.clip((rel - 0.55) / 0.45, 0, 1)
        glass = glass + (1 - glass) * sheen[..., None]
        glass = glass * (1 - shade[..., None])

        out = out * (1 - cov[..., None]) + glass * cov[..., None]

        # Specular rim: a thin bright edge, strongest where the edge faces the light.
        facing = nx * LIGHT[0] + ny * LIGHT[1]
        weight = 0.28 + 0.72 * np.clip(facing, 0, 1) ** 1.3 + 0.45 * np.clip(-facing, 0, 1) ** 2.5
        ring = np.exp(-(((d - 0.9) / 0.85) ** 2)) * (d > -0.6)
        inner = 0.10 * np.exp(-(((d - 4.0) / 1.8) ** 2)) * np.clip(facing, 0, 1)
        alpha = np.clip((ring * 0.92 + inner) * weight, 0, 1)[..., None]
        out = out * (1 - alpha) + alpha

    Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out_path)
    print(f"Wrote {out_path} (dim {dim:.2f})")

