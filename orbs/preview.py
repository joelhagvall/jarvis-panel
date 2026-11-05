#!/usr/bin/env python3
"""Show an orb style on the real orb screen, as an animated WebP.

Takes the LVGL render of the glass-v2 orb page, covers the old orb with the baked
background it sits on, plays the new frames on top and rounds every frame to RGB565
like the panel does.

Usage (needs Pillow and numpy):
  python3 orbs/preview.py <frames-dir> <frame-ms> <out.webp> [screen.png] [bg.png]

screen.png is an LVGL render of the orb page and bg.png the background it was drawn on
(default: the glass-v2 orb page over Starry Night).
"""
import sys
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
SCREEN = ROOT / "docs/readme/listening.jpg"
BG = ROOT / "styles/glass-v2/assets/bg_orb_starry.png"
CENTER, SIZE = (512, 205), 220            # matches styles/glass-v2/gen.py


def rgb565(img):
    a = np.asarray(img.convert("RGB")).astype(np.uint16)
    a[..., 0] = (a[..., 0] >> 3) << 3
    a[..., 1] = (a[..., 1] >> 2) << 2
    a[..., 2] = (a[..., 2] >> 3) << 3
    return Image.fromarray(a.astype(np.uint8), "RGB")


def main(frames_dir, frame_ms, out, screen_path=SCREEN, bg_path=BG):
    screen = Image.open(screen_path).convert("RGBA")
    box = (CENTER[0] - SIZE // 2, CENTER[1] - SIZE // 2, CENTER[0] + SIZE // 2, CENTER[1] + SIZE // 2)
    screen.paste(Image.open(bg_path).convert("RGBA").crop(box), box[:2])
    frames = []
    for f in sorted(Path(frames_dir).glob("*_[0-9][0-9].png")):
        frame = screen.copy()
        frame.alpha_composite(Image.open(f).convert("RGBA"), box[:2])
        frames.append(rgb565(frame))
    frames[0].save(out, save_all=True, append_images=frames[1:], duration=int(frame_ms),
                   loop=0, quality=82, method=6)
    print(f"{len(frames)} frames → {out} ({Path(out).stat().st_size / 1e6:.2f} MB)")


if __name__ == "__main__":
    main(*sys.argv[1:6])
