#!/usr/bin/env python3
"""The still layer over Spelas nu's spinning record (vinyl.h): grooves, the sheen, the rim and the spindle hole,
and the sheet's own colour outside the circle, so the turning square cover needs no clip mask on the panel.

Joel chose a picture disc on 2026-10-28: the Spotify cover is the whole record. Only the cover turns; the grooves
are round and the light stays still, as on a real record. Run this after changing the look, then commit the PNG:

    .venv/bin/python styles/glass-v2/vinyl_overlay.py
"""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

SIZES = (144,)                     # the sheet's record; the page's layer is turntable.py's, with the plinth
SHEET_BG = (0x14, 0x18, 0x26)      # the sheet's and the Musik card's bg_color
SS = 4                             # supersampling for smooth circles
ASSETS = Path(__file__).resolve().parent / "assets"


def out(size):
    return ASSETS / f"vinyl_overlay_{size}.png"


def render(size, around=None):
    """The still layer for a record of `size` px. Outside the circle it is the card's colour, or `around` (an RGBA
    image of the same size: what lies around the record, the turntable's platter and wood, turntable.py)."""
    S = size * SS
    c = (S - 1) / 2      # the pixel grid's centre, as the disc's (0, 0, S - 1, S - 1): symmetric half a turn on

    def ring(draw, r, **kw):
        draw.ellipse((c - r, c - r, c + r, c + r), **kw)

    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    grooves = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(grooves)
    n = max(6, size // 5)
    for i in range(n):
        ring(d, c * (0.25 + 0.72 * i / n), outline=(0, 0, 0, 38), width=max(1, SS // 2))
    layer = Image.alpha_composite(layer, grooves)
    sheen = Image.new("L", (S, S), 0)
    sd = ImageDraw.Draw(sheen)
    # Two equal wedges half a turn apart: vinyl.h shows the other half turn as the records upside down.
    sd.pieslice((0, 0, S - 1, S - 1), -60, -30, fill=70)
    sd.pieslice((0, 0, S - 1, S - 1), 120, 150, fill=70)
    sheen = sheen.filter(ImageFilter.GaussianBlur(S / 14))
    white = Image.new("RGBA", (S, S), (255, 255, 255, 0))
    white.putalpha(sheen)
    layer = Image.alpha_composite(layer, white)
    d = ImageDraw.Draw(layer)
    ring(d, c - SS, outline=(12, 12, 14, 255), width=SS * 2)                      # the rim
    h = S // 16
    ring(d, h * 1.8, outline=(12, 12, 14, 160), width=SS)                        # the label's edge
    ring(d, h * 0.45, fill=(18, 18, 20, 255))                                     # the spindle hole
    disc = Image.new("L", (S, S), 0)
    ImageDraw.Draw(disc).ellipse((0, 0, S - 1, S - 1), fill=255)
    inside = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    inside.paste(layer, (0, 0), disc)
    outside = Image.new("RGBA", (S, S), (*SHEET_BG, 255))
    outside.putalpha(disc.point(lambda v: 255 - v))
    out = Image.alpha_composite(inside, outside).resize((size, size), Image.LANCZOS)
    # Exactly the same half a turn on (PIL's wedges and outlines are a pixel off): the mean of the layer and itself
    # upside down, in premultiplied colour so clear pixels add nothing.
    rgba = np.asarray(out).astype(np.float64)
    pre = np.concatenate([rgba[..., :3] * rgba[..., 3:] / 255, rgba[..., 3:]], axis=2)
    pre = (pre + pre[::-1, ::-1]) / 2
    alpha = pre[..., 3:]
    rgb = np.where(alpha > 0, pre[..., :3] * 255 / np.maximum(alpha, 1e-9), 0)
    layer = Image.fromarray(np.clip(np.concatenate([rgb, alpha], axis=2) + 0.5, 0, 255).astype(np.uint8), "RGBA")
    if around is None:
        return layer
    # Around the record: the given picture, as it is (it need not be symmetric: vinyl_motion.h's flip_record keeps
    # it the right way up); inside: the symmetric layer.
    return Image.composite(layer, around.convert("RGBA"), disc.resize((size, size), Image.LANCZOS))


if __name__ == "__main__":
    for size in SIZES:
        render(size).save(out(size), optimize=True)
        print(out(size))
