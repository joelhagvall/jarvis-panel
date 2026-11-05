#!/usr/bin/env python3
"""The Spelas nu page's turntable (Joel's choice P1, 2026-10-28): a walnut plinth with a silver platter (strobe dots
on its edge, a dark mat), a speed knob (33/45) and a START button with its lamp. The record (vinyl.h), the tonearm
and its rest (now_playing.py's tonearm()) are drawn over it on the panel.

Equal gaps above, below and left of the platter; the right side is wider for the arm and its rest. It also writes
the page record's still layer with this plinth around the circle (vinyl_overlay.py), so the record's square image
is opaque and LVGL never blends the plinth under it per frame. Run this after changing the look, then commit both:

    .venv/bin/python styles/glass-v2/turntable.py
"""
import math
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import vinyl_overlay  # noqa: E402

CX, CY = 216, 254                  # the record's centre on the page (now_playing.py: DISC_XY + 144)
RECORD, PLATTER, MAT = 144, 156, 147
GAP = 31
PLINTH = (CX - PLATTER - GAP, CY - PLATTER - GAP, 436, CY + PLATTER + GAP)
MARGIN = 0                         # no shadow: its soft edge showed as a darker film around (Joel, 2026-10-28)
ORIGIN = (PLINTH[0] - MARGIN, PLINTH[1] - MARGIN)
SIZE = (PLINTH[2] - PLINTH[0] + 2 * MARGIN, PLINTH[3] - PLINTH[1] + 2 * MARGIN)
OUT = HERE / "assets" / "turntable_p1.png"           # the whole plinth, for the tests and for looking at
# The plinth around the record's square, in four pieces (page coordinates, x0 y0 x1 y1): nothing of it lies under
# the record, so the record's 60 frames a second never blend the plinth again. One picture under the record cost
# 14.4 ms a frame against 6.5 (2026-10-28).
SQUARE = (CX - RECORD, CY - RECORD, CX + RECORD, CY + RECORD)
PIECES = {"top": (PLINTH[0], PLINTH[1], PLINTH[2], SQUARE[1]),
          "bottom": (PLINTH[0], SQUARE[3], PLINTH[2], PLINTH[3]),
          "left": (PLINTH[0], SQUARE[1], SQUARE[0], SQUARE[3]),
          "right": (SQUARE[2], SQUARE[1], PLINTH[2], SQUARE[3])}


def piece_path(name):
    return HERE / "assets" / f"turntable_p1_{name}.png"


def pieces(plinth):
    return {name: plinth.crop((x0 - ORIGIN[0], y0 - ORIGIN[1], x1 - ORIGIN[0], y1 - ORIGIN[1]))
            for name, (x0, y0, x1, y1) in PIECES.items()}
LAYER = HERE / "assets" / "vinyl_overlay_288_p1.png"
SS = 4
GRAIN = (46, 60, 74)               # the grain lines' alpha, three shades
FONT = HERE / ".esphome/font/Inter@700@False@v1.ttf"


def render():
    W, H = SIZE
    img = Image.new("RGBA", (W * SS, H * SS), (0, 0, 0, 0))
    x0, y0 = MARGIN * SS, MARGIN * SS
    x1, y1 = (W - MARGIN) * SS, (H - MARGIN) * SS
    wood = Image.new("RGBA", img.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(wood)
    top, bottom = (122, 74, 42), (86, 50, 28)
    for i in range(y0, y1):
        k = (i - y0) / (y1 - y0)
        d.line((x0, i, x1, i), fill=tuple(int(a + (b - a) * k) for a, b in zip(top, bottom)) + (255,))
    grain = Image.new("RGBA", img.size, (0, 0, 0, 0))            # the grain, blended in: drawn straight onto the
    g = ImageDraw.Draw(grain)                                    # wood its lines replaced pixels and left holes
    # Strong enough for the panel's 16-bit colour: at alpha 16-28 and half a pixel wide the grain spanned red 114-120,
    # two RGB565 steps, and did not show (Joel, 2026-10-28). The plinth is also dithered (now_playing.py).
    for i in range(x0, x1, 7):
        g.line((i, y0, i + 40 + (i % 23), y1), fill=(0, 0, 0, GRAIN[i % 3]), width=SS)
    wood = Image.alpha_composite(wood, grain)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((x0, y0, x1 - 1, y1 - 1), 22 * SS, fill=255)
    img.paste(wood, (0, 0), mask)
    lines = Image.new("RGBA", img.size, (0, 0, 0, 0))            # translucent strokes, blended in as the grain is
    ImageDraw.Draw(lines).rounded_rectangle((x0 + 2 * SS, y0 + 2 * SS, x1 - 2 * SS, y1 - 2 * SS), 22 * SS,
                                            outline=(255, 230, 200, 50), width=2 * SS)
    img = Image.alpha_composite(img, lines)
    d = ImageDraw.Draw(img)
    cx, cy = (CX - ORIGIN[0]) * SS, (CY - ORIGIN[1]) * SS      # the platter, its strobe dots and the mat
    r = PLATTER * SS
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(196, 200, 208, 255))
    rim = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(rim).ellipse((cx - r, cy - r, cx + r, cy + r), outline=(255, 255, 255, 90), width=2 * SS)
    img = Image.alpha_composite(img, rim)
    d = ImageDraw.Draw(img)
    for k in range(72):
        a = math.radians(k * 5)
        px, py = cx + (PLATTER - 5) * SS * math.cos(a), cy + (PLATTER - 5) * SS * math.sin(a)
        d.ellipse((px - 1.5 * SS, py - 1.5 * SS, px + 1.5 * SS, py + 1.5 * SS), fill=(70, 74, 82, 255))
    r = MAT * SS
    d.ellipse((cx - r, cy - r, cx + r, cy + r), fill=(30, 30, 34, 255))
    font = ImageFont.truetype(str(FONT), 11 * SS) if FONT.exists() else None
    face, ring, label = (200, 204, 212, 255), (80, 84, 94, 255), (230, 214, 196, 255)
    kx, ky = x0 + 30 * SS, y1 - 30 * SS                         # the speed knob, 30 px in like START
    d.ellipse((kx - 16 * SS, ky - 16 * SS, kx + 16 * SS, ky + 16 * SS), fill=face, outline=ring, width=2 * SS)
    # Its pointer and the 33 / 45 beside it are widgets (now_playing.py): the knob works (Joel, 2026-10-28).
    bx, by = x1 - 76 * SS, y1 - 30 * SS                         # START, its lamp 30 px from the edge
    d.rounded_rectangle((bx - 26 * SS, by - 13 * SS, bx + 26 * SS, by + 13 * SS), 8 * SS, fill=face, outline=ring,
                        width=2 * SS)
    if font:
        d.text((bx, by), "START", font=ImageFont.truetype(str(FONT), 10 * SS), fill=ring, anchor="mm")
    d.ellipse((bx + 32 * SS, by - 4 * SS, bx + 40 * SS, by + 4 * SS), fill=(255, 80, 60, 255))
    return dither_rgb565(img.resize(SIZE, Image.LANCZOS))


def dither_rgb565(img):
    """Floyd-Steinberg onto the panel's 5/6/5-bit levels, so the wood's fine shades show as a fine pattern instead
    of rounding away; ESPHome's RGB565 encoder then keeps every pixel as it is (it dithers only 1-bit images)."""
    import numpy as np
    a = np.asarray(img.convert("RGBA")).astype(np.float64)
    rgb, alpha = a[..., :3].copy(), a[..., 3:]
    steps = np.array([8.0, 4.0, 8.0])
    h, w, _ = rgb.shape
    for y in range(h):
        row, below = rgb[y], rgb[y + 1] if y + 1 < h else None
        for x in range(w):
            old = row[x].copy()
            new = np.clip(np.floor(old / steps + 0.5) * steps, 0, 255 - (255 % 8))
            row[x] = new
            err = old - new
            if x + 1 < w:
                row[x + 1] += err * 7 / 16
            if below is not None:
                if x > 0:
                    below[x - 1] += err * 3 / 16
                below[x] += err * 5 / 16
                if x + 1 < w:
                    below[x + 1] += err / 16
    return Image.fromarray(np.concatenate([np.clip(rgb, 0, 255), alpha], axis=2).astype(np.uint8), "RGBA")


def record_layer(plinth):
    """The page record's still layer with the plinth around the circle."""
    left, top = CX - RECORD - ORIGIN[0], CY - RECORD - ORIGIN[1]
    around = plinth.crop((left, top, left + 2 * RECORD, top + 2 * RECORD))
    return vinyl_overlay.render(2 * RECORD, around=around)


if __name__ == "__main__":
    plinth = render()
    plinth.save(OUT, optimize=True)
    for name, image in pieces(plinth).items():
        image.save(piece_path(name), optimize=True)
    record_layer(plinth).save(LAYER, optimize=True)
    print(OUT, LAYER)
