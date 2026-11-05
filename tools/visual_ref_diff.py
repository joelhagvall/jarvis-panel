#!/usr/bin/env python3
"""Where two visual references differ, scene by scene: what to look at before a new reference becomes the default.

    tools/panel visual-diff OLD_REFERENCE NEW_REFERENCE [--band Y0 Y1]

`visual compare` needs the same policy, and a reference is re-recorded exactly when the policy changed. This reads
only the two folders' screen.png files and prints, per scene, how many pixels changed (more than 8 per channel) in
the given band (default the tab bar, y 505-590) and elsewhere, with the bounding box of the rest, so changes can be
told apart from live data (clock, weather, SL, calendar) before AGENTS.md's "inspect and explain" (2026-10-28).
"""
import argparse
import json
from pathlib import Path

import numpy as np
from PIL import Image


def diff(old, new, band=(505, 590), tolerance=8):
    a = np.asarray(Image.open(old).convert("RGB")).astype(int)
    b = np.asarray(Image.open(new).convert("RGB")).astype(int)
    changed = np.max(np.abs(a - b), axis=2) > tolerance
    in_band = changed[band[0]:band[1], :]
    rest = changed.copy()
    rest[band[0]:band[1], :] = False
    ys, xs = np.where(rest)
    box = [int(xs.min()), int(ys.min()), int(xs.max()) + 1, int(ys.max()) + 1] if len(xs) else None
    return {"band": int(in_band.sum()), "elsewhere": int(rest.sum()), "elsewhere_box": box}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--band", type=int, nargs=2, default=(505, 590))
    args = parser.parse_args(argv)
    out = {}
    for scene in sorted(p.parent.name for p in args.new.glob("*/screen.png")):
        old = args.old / scene / "screen.png"
        out[scene] = diff(old, args.new / scene / "screen.png", tuple(args.band)) if old.exists() else "new scene"
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
