#!/usr/bin/env python3
"""The Spelas nu page's sleeve (Joel, 2026-10-29): a tap on the record shows the cover straight and sharp as a
sleeve, with a black record half out of it on the right; another tap brings the turntable back. The record shows
the edge of its cream paper label beside the sleeve, as a real LP pulled out of its sleeve does (Joel, 2026-10-29). The sleeve is the
downloaded cover itself (np_art, an LVGL image); this draws only the record's visible strip right of the sleeve,
opaque on the page's colour with the sleeve's soft shadow on it. Nothing here moves, so the page draws it once.
Run this after changing the look, then commit the PNG:

    .venv/bin/python styles/glass-v2/sleeve.py
"""
import sys
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import turntable       # noqa: E402  (dither_rgb565)
import vinyl_overlay   # noqa: E402

SIZE = 288                        # the cover and the record, as on the turntable
SLEEVE_XY = (29, 254 - 144)       # now_playing.py: the sleeve at the plinth's left edge, centred on PAGE_MID
RECORD_X = 148                    # the record's left edge: 119 px of it show, up to the turntable's right edge 436
STRIP_W = RECORD_X + SIZE - (SLEEVE_XY[0] + SIZE)
OUT = HERE / "assets" / "sleeve_record.png"
VINYL = (24, 24, 28)              # the record's black, light enough that the grooves and rim show on the dark page
SHADOW = 16                       # px of the sleeve's shadow on the record
LABEL_R = SIZE // 6               # a 12" LP's 4" label: a third of the record across; its outer 23 px show
LABEL = (226, 218, 200)           # cream paper


# The page's colour as the panel shows it: LVGL drops the low bits of SHEET_BG (0x141826 shows as 16, 24, 32).
# Dithered, the corners round to a mix of 16 and 24 and showed as a lighter box round the record (2026-10-29).
PAGE = tuple(c & m for c, m in zip(vinyl_overlay.SHEET_BG, (0xF8, 0xFC, 0xF8)))


def label():
    """The paper label in the record's middle: cream, a printed ring near its edge and a fainter one inside."""
    S, c = SIZE * 4, SIZE * 2
    layer = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)

    def ring(r, **kw):
        d.ellipse((c - r, c - r, c + r, c + r), **kw)
    ring(LABEL_R * 4, fill=(*LABEL, 255))
    ring((LABEL_R - 4) * 4, outline=(*LABEL, 255), width=1)
    ring((LABEL_R - 4) * 4, outline=(120, 104, 84, 150), width=4)
    ring((LABEL_R - 15) * 4, outline=(120, 104, 84, 70), width=4)
    return layer.resize((SIZE, SIZE), Image.LANCZOS)


def render():
    disc = Image.new("RGBA", (SIZE, SIZE), (*VINYL, 255))
    record = Image.alpha_composite(disc, vinyl_overlay.render(SIZE))   # grooves, sheen, rim, page colour outside
    record = Image.alpha_composite(record, label())
    box = (SIZE - STRIP_W, 0, SIZE, SIZE)
    strip = record.crop(box)
    inside = Image.new("L", (SIZE * 4, SIZE * 4), 0)
    ImageDraw.Draw(inside).ellipse((0, 0, SIZE * 4 - 1, SIZE * 4 - 1), fill=255)
    # BOX gives each pixel its covered share: LANCZOS rang a little outside the circle, which kept a dithered pixel
    # at the strip's corner once the record moved out to it (2026-10-29).
    inside = inside.resize((SIZE, SIZE), Image.BOX).crop(box)
    shade = Image.new("L", strip.size, 0)
    d = ImageDraw.Draw(shade)
    for x in range(SHADOW):
        d.line((x, 0, x, SIZE), fill=int(120 * (1 - x / SHADOW) ** 2))
    black = Image.new("RGBA", strip.size, (0, 0, 0, 255))
    black.putalpha(ImageChops.multiply(shade, inside))                  # the shadow falls on the record only
    out = turntable.dither_rgb565(Image.alpha_composite(strip, black))
    out.paste(Image.new("RGBA", strip.size, (*PAGE, 255)), (0, 0), inside.point(lambda v: 255 if v == 0 else 0))
    return out


if __name__ == "__main__":
    render().save(OUT, optimize=True)
    print(OUT)
