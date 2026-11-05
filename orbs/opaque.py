#!/usr/bin/env python3
"""Bake the Jarvis orb's frames onto the voice page's background, so the panel draws them without alpha.

The orb's frames (orbs/glass-jarvis/<state>_NN.png, 220x220 with alpha) always sit on the same spot of the
same background (bg_orb.png, the blurred painting with its edge glow). Composited onto that background here,
each frame becomes an opaque RGB565 image: LVGL only copies it instead of blending every pixel, and it takes
2 bytes a pixel in PSRAM instead of 3 (about 6 MB for the 62 frames instead of 9). It looks the same, since
the pixels around it are the very same background.

  python3 orbs/opaque.py      writes orbs/glass-jarvis-opaque/<state>_NN.png (needs Pillow)

Run it again after orbs/glass_variants.py jarvis or a new bg_orb.png.
"""
import sys
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "styles" / "glass-v2"))
import gen  # noqa: E402  (where the orb sits)

SRC, OUT = ROOT / "orbs" / "glass-jarvis", ROOT / "orbs" / "glass-jarvis-opaque"
SETS = {"listen": 24, "morph": 8, "speak": 24, "settle": 6}   # as styles/glass-v2/jarvis_orb.py


def main():
    OUT.mkdir(exist_ok=True)
    x, y = gen.ORB_CENTER[0] - gen.ORB_SIZE // 2, gen.ORB_CENTER[1] - gen.ORB_SIZE // 2
    bg = Image.open(SRC / "bg_orb.png").convert("RGBA")
    patch = bg.crop((x, y, x + gen.ORB_SIZE, y + gen.ORB_SIZE))
    for key, n in SETS.items():
        for i in range(n):
            frame = Image.open(SRC / f"{key}_{i:02d}.png").convert("RGBA")
            Image.alpha_composite(patch, frame).convert("RGB").save(OUT / f"{key}_{i:02d}.png", optimize=True)
    size = sum(f.stat().st_size for f in OUT.glob("*.png"))
    print(f"{sum(SETS.values())} opaque frames at ({x}, {y}), {size / 1e6:.1f} MB as PNG -> {OUT}")


if __name__ == "__main__":
    main()
