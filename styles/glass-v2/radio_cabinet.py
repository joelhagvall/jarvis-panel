#!/usr/bin/env python3
"""The Spelas nu page's radio (Joel's choice B1, 2026-10-28): a walnut cabinet with a speaker grille, a warm dial
window with the FM scale and two knobs. Only what never changes is drawn here; the station's logo, the frequency
and the red needle are LVGL widgets over the dial (now_playing.py, radio_dial.h). Run this after changing the look,
then commit the PNG:

    .venv/bin/python styles/glass-v2/radio_cabinet.py
"""
from pathlib import Path

from PIL import Image, ImageDraw

W, H = 360, 250
SS = 4
DIAL = (186, 30, 338, 140)        # the dial window inside the cabinet (radio_dial.h uses the same numbers)
OUT = Path(__file__).resolve().parent / "assets" / "radio_b1.png"
INK = (70, 40, 20)


def render():
    S = SS
    img = Image.new("RGBA", (W * S, H * S), (0, 0, 0, 0))
    wood = Image.new("RGBA", img.size)
    d = ImageDraw.Draw(wood)
    for i in range(H * S):                                   # walnut, darker towards the bottom
        k = i / (H * S)
        d.line((0, i, W * S, i), fill=(int(92 - 40 * k), int(56 - 26 * k), int(34 - 16 * k), 255))
    grain = Image.new("RGBA", img.size, (0, 0, 0, 0))        # the grain, blended in: drawn straight onto the
    g = ImageDraw.Draw(grain)                                # wood its lines replaced pixels and left holes
    for i in range(0, W * S, 9 * S // 3):
        g.line((i, 0, i + 30 * S // 3, H * S), fill=(0, 0, 0, 52), width=S)   # strong enough for RGB565
    wood = Image.alpha_composite(wood, grain)
    mask = Image.new("L", img.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, W * S - 1, H * S - 1), 26 * S, fill=255)
    img.paste(wood, (0, 0), mask)
    edge = Image.new("RGBA", img.size, (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle((4 * S, 4 * S, (W - 4) * S, (H - 4) * S), 24 * S,
                                           outline=(255, 220, 170, 40), width=2 * S)
    img = Image.alpha_composite(img, edge)
    d = ImageDraw.Draw(img)
    gx0, gy0, gx1, gy1 = 22, 22, 168, H - 22                 # the grille
    d.rounded_rectangle((gx0 * S, gy0 * S, gx1 * S, gy1 * S), 16 * S, fill=(38, 26, 20, 255))
    for yy in range(gy0 + 10, gy1 - 6, 9):
        for xx in range(gx0 + 10 + ((yy // 9) % 2) * 4, gx1 - 6, 9):
            d.ellipse(((xx - 2) * S, (yy - 2) * S, (xx + 2) * S, (yy + 2) * S), fill=(14, 10, 8, 255))
    dx0, dy0, dx1, dy1 = DIAL                                # the dial window and its scale
    d.rounded_rectangle((dx0 * S, dy0 * S, dx1 * S, dy1 * S), 12 * S, fill=(255, 196, 110, 255))
    from PIL import ImageFont
    font = ImageFont.truetype(str(Path(__file__).resolve().parent / ".esphome/font/Inter@600@False@v1.ttf"), 11 * S) \
        if (Path(__file__).resolve().parent / ".esphome/font/Inter@600@False@v1.ttf").exists() else None
    for mhz in range(88, 109, 2):
        xx = dx0 + 12 + (dx1 - dx0 - 24) * (mhz - 88) / 20
        d.line((xx * S, (dy0 + 54) * S, xx * S, (dy0 + 66 + (6 if mhz % 4 == 0 else 0)) * S), fill=INK, width=S)
        if mhz % 4 == 0 and font:
            d.text((xx * S, (dy0 + 84) * S), str(mhz), font=font, fill=INK, anchor="mm")
    for kx in (222, W - 58):                                 # two knobs
        d.ellipse(((kx - 26) * S, 168 * S, (kx + 26) * S, 220 * S), fill=(214, 196, 168, 255),
                  outline=(120, 90, 60, 255), width=2 * S)
        d.line((kx * S, 194 * S, (kx - 9) * S, (194 - 15) * S), fill=(90, 60, 40, 255), width=3 * S)
    # Dithered onto the panel's 5/6/5 levels like the turntable (turntable.dither_rgb565): at alpha 18 the grain
    # was 7 shades in the picture and 3 on the panel (2026-10-28).
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from turntable import dither_rgb565
    return dither_rgb565(img.resize((W, H), Image.LANCZOS))


if __name__ == "__main__":
    render().save(OUT, optimize=True)
    print(OUT)
