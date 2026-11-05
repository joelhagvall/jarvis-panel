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
from image_sampling import sample, gaussian_blur  # noqa: E402

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
    return gaussian_blur(a, k)


def saturate(a, amount):
    lum = luminance(a)[..., None]
    return np.clip(lum + (a - lum) * amount, 0, 1)


def shape_field(x, y, w, h, kind, bounds=None):
    """Signed distance (px, positive inside) and outward unit normal for the whole frame."""
    left, top, right, bottom = bounds or (0, 0, W, H)
    px, py = np.meshgrid(np.arange(left, right) + 0.5, np.arange(top, bottom) + 0.5)
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
    # Fast DEFLATE: panel_image re-encodes this for the device; same pixels, ~0.3 s less on Jarvis.
    Image.fromarray((np.clip(art, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out_path, compress_level=1)


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

    # Keep the full Gaussian rim tail: at 64 px outside the shape its
    # contribution has underflowed to zero in float64. Smaller bounds clipped
    # faint highlights and changed a few source pixels before RGB565 conversion.
    fields = {}
    for key, (x, y, w, h, kind, tint) in RECTS.items():
        bounds = max(0, x - 64), max(0, y - 64), min(W, x + w + 64), min(H, y + h + 64)
        left, top, right, bottom = bounds
        region = np.s_[top:bottom, left:right]
        fields[key] = region, shape_field(x, y, w, h, kind, bounds)

    # Drop shadows first, so no shadow lands on a neighbour's glass.
    shadow = np.zeros((H, W), np.float32)
    for key, (x, y, w, h, kind, tint) in RECTS.items():
        region, field = fields[key]
        # Keep the full-frame blur and roll: their edge padding and ordering are
        # part of the approved shadow pixels.
        cov = np.zeros((H, W), dtype=np.float64)
        cov[region] = np.clip(field[0] + 0.5, 0, 1)
        soft = gblur(np.roll(cov, 8, axis=0), 12)
        shadow = 1 - (1 - shadow) * (1 - 0.34 * soft * (1 - cov))
    out *= (1 - shadow)[..., None]

    # Lightly blurred, slightly more vivid copy of the painting for the glass to show.
    glass_src = saturate(gblur(art, 3.0), 1.3)

    # The first glass blend promotes the original full frame to float64.
    # Promote explicitly before updating slices, so assignment cannot round it.
    out = out.astype(np.float64)
    for key, (x, y, w, h, kind, tint) in RECTS.items():
        region, (d, nx, ny, gpx, gpy) = fields[key]
        tile = out[region]
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

        tile = tile * (1 - cov[..., None]) + glass * cov[..., None]

        # Specular rim: a thin bright edge, strongest where the edge faces the light.
        facing = nx * LIGHT[0] + ny * LIGHT[1]
        weight = 0.28 + 0.72 * np.clip(facing, 0, 1) ** 1.3 + 0.45 * np.clip(-facing, 0, 1) ** 2.5
        ring = np.exp(-(((d - 0.9) / 0.85) ** 2)) * (d > -0.6)
        inner = 0.10 * np.exp(-(((d - 4.0) / 1.8) ** 2)) * np.clip(facing, 0, 1)
        alpha = np.clip((ring * 0.92 + inner) * weight, 0, 1)[..., None]
        out[region] = tile * (1 - alpha) + alpha

    Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out_path, compress_level=1)
    print(f"Wrote {out_path} (dim {dim:.2f})")


ORB_CENTER = (512, 205)   # matches the animimg position in gen.py
EDGE_STOPS = np.array([[0x0A, 0x84, 0xFF], [0xAF, 0x52, 0xDE], [0xFF, 0x37, 0x5F],
                       [0x64, 0xD2, 0xFF], [0x0A, 0x84, 0xFF]], dtype=np.float32) / 255.0


def bake_orb_bg(painting_path, out_path):
    """Background for the voice page: the painting heavily blurred and darkened, a soft
    bloom behind the orb, and a Siri-style colour glow along the screen edges."""
    art = load_painting(painting_path)
    out = saturate(gblur(art, 28.0), 1.25)
    out *= 0.13 / max(float(luminance(out).mean()), 0.13)   # dark enough for white text
    px, py = np.meshgrid(np.arange(W) + 0.5, np.arange(H) + 0.5)

    # Soft bloom behind the orb.
    ox, oy = ORB_CENTER
    bloom = 0.22 * np.exp(-((px - ox) ** 2 + (py - oy) ** 2) / (2 * 150.0 ** 2))
    out = out * (1 - bloom[..., None]) + np.array([0.25, 0.30, 0.85]) * bloom[..., None]

    # Edge glow: the colour cycles round the frame, bright at the edge and fading inwards.
    u = (np.arctan2(py - H / 2, px - W / 2) / (2 * np.pi)) % 1.0
    pos = u * (len(EDGE_STOPS) - 1)
    i0 = np.floor(pos).astype(int)
    fr = (pos - i0)[..., None]
    col = EDGE_STOPS[i0] * (1 - fr) + EDGE_STOPS[np.minimum(i0 + 1, len(EDGE_STOPS) - 1)] * fr
    e = np.minimum.reduce([px, W - px, py, H - py])
    a = np.clip(0.85 * np.exp(-e / 10) + 0.30 * np.exp(-e / 55), 0, 1)[..., None]
    out = out * (1 - a) + col * a

    Image.fromarray((np.clip(out, 0, 1) * 255 + 0.5).astype(np.uint8)).save(out_path)
    print(f"Wrote {out_path}")


def draw_icons():
    """White icons with alpha, drawn at 4x and scaled down for smooth edges."""
    def mic(size, path):
        s = size * 4
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        dr = ImageDraw.Draw(im)
        lw = max(4, s // 12)
        dr.rounded_rectangle((s * 0.36, s * 0.08, s * 0.64, s * 0.58), radius=s * 0.14, fill="white")
        dr.arc((s * 0.22, s * 0.26, s * 0.78, s * 0.74), 20, 160, fill="white", width=lw)
        dr.line((s * 0.5, s * 0.74, s * 0.5, s * 0.9), fill="white", width=lw)
        dr.line((s * 0.36, s * 0.9, s * 0.64, s * 0.9), fill="white", width=lw)
        im.resize((size, size), Image.LANCZOS).save(path)

    mic(26, os.path.join(HERE, "assets", "mic.png"))
    mic(18, os.path.join(HERE, "assets", "mic_small.png"))

    s = 36 * 4
    im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    dr = ImageDraw.Draw(im)
    dr.ellipse((s * 0.10, s * 0.30, s * 0.42, s * 0.62), fill="white")
    dr.ellipse((s * 0.28, s * 0.14, s * 0.70, s * 0.56), fill="white")
    dr.ellipse((s * 0.56, s * 0.30, s * 0.90, s * 0.62), fill="white")
    dr.rounded_rectangle((s * 0.10, s * 0.42, s * 0.90, s * 0.62), radius=s * 0.1, fill="white")
    blue = (0x64, 0xD2, 0xFF, 255)
    for x0 in (0.30, 0.50, 0.70):
        dr.line((s * x0, s * 0.72, s * (x0 - 0.06), s * 0.90), fill=blue, width=s // 14)
    im.resize((36, 36), Image.LANCZOS).save(os.path.join(HERE, "assets", "rain.png"))
    draw_more_icons()


def draw_more_icons():
    """The rest of the icons, in the same flat white style: the weather card's sun, moon, partly cloudy, cloud
    and snow (36 px), the Hemmet cards' thermometer, shield, bell and washing machine (18 px), and Hemmet's
    status, a green check or an orange "!" in a circle (44 px). Drawn at 4x and scaled down."""
    white, yellow, ice = (255, 255, 255, 255), (0xFF, 0xD6, 0x0A, 255), (0x64, 0xD2, 0xFF, 255)

    def canvas(size):
        s = size * 4
        im = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        return im, ImageDraw.Draw(im), s

    def save(im, size, name):
        im.resize((size, size), Image.LANCZOS).save(os.path.join(HERE, "assets", f"{name}.png"))

    def cloud(dr, s, dx=0.0, dy=0.0, k=1.0, fill=white):
        def box(x0, y0, x1, y1):
            return (s * (dx + x0 * k), s * (dy + y0 * k), s * (dx + x1 * k), s * (dy + y1 * k))
        dr.ellipse(box(0.10, 0.40, 0.42, 0.72), fill=fill)
        dr.ellipse(box(0.28, 0.24, 0.70, 0.66), fill=fill)
        dr.ellipse(box(0.56, 0.40, 0.90, 0.72), fill=fill)
        dr.rounded_rectangle(box(0.10, 0.52, 0.90, 0.72), radius=s * 0.1 * k, fill=fill)

    def sun(dr, s, cx, cy, r, colour):
        dr.ellipse((s * (cx - r), s * (cy - r), s * (cx + r), s * (cy + r)), fill=colour)
        for i in range(8):
            a = i * math.pi / 4
            dr.line((s * (cx + math.cos(a) * r * 1.45), s * (cy + math.sin(a) * r * 1.45),
                     s * (cx + math.cos(a) * r * 1.95), s * (cy + math.sin(a) * r * 1.95)), fill=colour, width=s // 18)

    im, dr, s = canvas(36)
    sun(dr, s, 0.5, 0.5, 0.2, yellow)
    save(im, 36, "sun")

    im, dr, s = canvas(36)
    dr.ellipse((s * 0.16, s * 0.14, s * 0.84, s * 0.82), fill=white)
    dr.ellipse((s * 0.38, s * 0.02, s * 1.02, s * 0.66), fill=(0, 0, 0, 0))
    save(im, 36, "moon")

    im, dr, s = canvas(36)
    sun(dr, s, 0.36, 0.34, 0.15, yellow)
    cloud(dr, s, dx=0.08, dy=0.10, k=0.92)
    save(im, 36, "partly")

    im, dr, s = canvas(36)
    cloud(dr, s, dy=-0.04)
    save(im, 36, "cloud")

    im, dr, s = canvas(36)
    cloud(dr, s, dy=-0.12)
    for x0, y0 in ((0.30, 0.78), (0.50, 0.86), (0.70, 0.78)):
        dr.ellipse((s * (x0 - 0.05), s * (y0 - 0.05), s * (x0 + 0.05), s * (y0 + 0.05)), fill=ice)
    save(im, 36, "snow")

    lw = 18 * 4 // 9
    im, dr, s = canvas(18)                        # thermometer
    dr.rounded_rectangle((s * 0.38, s * 0.06, s * 0.62, s * 0.66), radius=s * 0.12, outline=white, width=lw)
    dr.ellipse((s * 0.26, s * 0.56, s * 0.74, s * 0.96), fill=white)
    save(im, 18, "i_thermo")

    im, dr, s = canvas(18)                        # shield
    dr.polygon([(s * 0.5, s * 0.04), (s * 0.88, s * 0.18), (s * 0.84, s * 0.56), (s * 0.5, s * 0.96),
                (s * 0.16, s * 0.56), (s * 0.12, s * 0.18)], fill=white)
    save(im, 18, "i_shield")

    im, dr, s = canvas(18)                        # bell
    dr.pieslice((s * 0.18, s * 0.10, s * 0.82, s * 0.78), 180, 360, fill=white)
    dr.rectangle((s * 0.18, s * 0.44, s * 0.82, s * 0.70), fill=white)
    dr.rounded_rectangle((s * 0.08, s * 0.66, s * 0.92, s * 0.78), radius=s * 0.05, fill=white)
    dr.ellipse((s * 0.40, s * 0.80, s * 0.60, s * 0.98), fill=white)
    save(im, 18, "i_bell")

    im, dr, s = canvas(18)                        # washing machine
    dr.rounded_rectangle((s * 0.10, s * 0.04, s * 0.90, s * 0.96), radius=s * 0.14, outline=white, width=lw)
    dr.ellipse((s * 0.28, s * 0.36, s * 0.72, s * 0.80), outline=white, width=lw)
    for x0 in (0.26, 0.42):
        dr.ellipse((s * x0, s * 0.14, s * (x0 + 0.1), s * 0.24), fill=white)
    save(im, 18, "i_washer")

    # The mic with a slash, for the mic buttons while the microphone is off (26 and 18 px, like mic.png).
    for size, name in ((26, "mic_off"), (18, "mic_off_small")):
        im, dr, s = canvas(size)
        lw = max(4, s // 12)
        dr.rounded_rectangle((s * 0.36, s * 0.08, s * 0.64, s * 0.58), radius=s * 0.14, fill=white)
        dr.arc((s * 0.22, s * 0.26, s * 0.78, s * 0.74), 20, 160, fill=white, width=lw)
        dr.line((s * 0.5, s * 0.74, s * 0.5, s * 0.9), fill=white, width=lw)
        dr.line((s * 0.36, s * 0.9, s * 0.64, s * 0.9), fill=white, width=lw)
        dr.line((s * 0.14, s * 0.08, s * 0.86, s * 0.92), fill=(0, 0, 0, 0), width=lw * 3)   # a gap around the slash
        dr.line((s * 0.14, s * 0.08, s * 0.86, s * 0.92), fill=white, width=lw)
        save(im, size, name)

    for name, colour in (("s_ok", (0x30, 0xD1, 0x58, 255)), ("s_warn", (0xFF, 0x9F, 0x0A, 255))):
        im, dr, s = canvas(44)
        dr.ellipse((s * 0.02, s * 0.02, s * 0.98, s * 0.98), fill=colour)
        if name == "s_ok":
            dr.line([(s * 0.28, s * 0.52), (s * 0.44, s * 0.68), (s * 0.73, s * 0.36)], fill=white, width=s // 11,
                    joint="curve")
        else:
            dr.rounded_rectangle((s * 0.45, s * 0.22, s * 0.55, s * 0.60), radius=s * 0.05, fill=white)
            dr.ellipse((s * 0.44, s * 0.66, s * 0.56, s * 0.78), fill=white)
        save(im, 44, name)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = sys.argv[1]
    stem = os.path.splitext(os.path.basename(src))[0]
    dst = sys.argv[2] if len(sys.argv) > 2 else os.path.join(HERE, "assets", f"bg_{stem}.png")
    draw_icons()
    bake(src, dst)
    bake_orb_bg(src, os.path.join(os.path.dirname(dst), f"bg_orb_{stem}.png"))
    frame(src, os.path.join(os.path.dirname(dst), f"art_{stem}.png"))
