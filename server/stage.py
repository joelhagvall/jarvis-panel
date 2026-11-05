#!/usr/bin/env python3
"""Copy the chosen design's images into server/www, named like the panel's image ids.

The panel downloads bg.png, art.png, voice_bg.png and transparent orb_atlas_v8.lz4 (the Jarvis line) through Home
Assistant's /local/hallpanel. Older atlases (v1-v7, the glass orb) and individual PNG frames remain for older firmware.

Usage: python3 server/stage.py [painting]   (default: starry; any bg_<painting>.png in glass-v2)
"""
import hashlib
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "orbs"))
import atlas
from voice_background import make as voice_background
from panel_image import convert as panel_image
WWW = ROOT / "server" / "www"
FRAMES = ROOT / "orbs" / "glass-jarvis"
OPAQUE = ROOT / "orbs" / "glass-jarvis-opaque"   # the frames baked onto bg_orb (orbs/opaque.py)
SETS = {"listen": 24, "morph": 8, "speak": 24, "settle": 6}   # matches styles/glass-v2/jarvis_orb.py
# Building the orb atlases took 11 of 14 s per deploy (2026-10-26) though their inputs rarely change. Each atlas
# is a pure function of atlas.py, the orb frames and the libraries, so it is kept per machine under that key.
# An empty HALLPANEL_ATLAS_CACHE turns the cache off.
ATLAS_CACHE = os.environ.get("HALLPANEL_ATLAS_CACHE", str(Path.home() / ".cache/hallpanel/orb-atlas"))


def atlas_key():
    import lz4
    import PIL
    digest = hashlib.sha256(b"orb-atlas-v1\0")
    sources = [Path(atlas.__file__)] + sorted((ROOT / "orbs").glob("glass-jarvis*/*.png")) + \
        sorted((ROOT / "orbs").glob("line-jarvis/*.png"))
    for path in sources:
        digest.update(str(path.relative_to(ROOT)).encode() + b"\0" + hashlib.sha256(path.read_bytes()).digest())
    for part in (lz4.version.version, PIL.__version__, sys.version, sys.byteorder):
        digest.update(part.encode() + b"\0")
    return digest.hexdigest()


def cached(destination, build, key):
    """build(destination) once per key; later calls copy the kept bytes."""
    kept = Path(ATLAS_CACHE) / key / destination.name if ATLAS_CACHE else None
    if kept is not None and kept.is_file():
        shutil.copyfile(kept, destination)
        return
    build(destination)
    if kept is not None:
        kept.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        partial = kept.with_name(f".{kept.name}.{os.getpid()}.tmp")
        shutil.copyfile(destination, partial)
        os.replace(partial, kept)


def main(painting="starry"):
    WWW.mkdir(parents=True, exist_ok=True)
    for old in WWW.glob("*.png"):
        old.unlink()
    files = {"bg.png": ROOT / "styles" / "glass-v2" / "assets" / f"bg_{painting}.png",
             "art.png": ROOT / "styles" / "glass-v2" / "assets" / f"art_{painting}.png",   # Tavla, no glass
             "bg_orb.png": FRAMES / "bg_orb.png"}
    for key, n in SETS.items():
        files.update({f"{key}_{i:02d}.png": OPAQUE / f"{key}_{i:02d}.png" for i in range(n)})
    for name, src in files.items():
        if name in ('bg.png', 'art.png', 'bg_orb.png'):
            panel_image(src, WWW / name)
        else:
            shutil.copyfile(src, WWW / name)
    voice_background(files['art.png'], WWW / 'voice_bg.png')
    panel_image(WWW / 'voice_bg.png', WWW / 'voice_bg.png')
    key = atlas_key()
    cached(WWW / "orb_atlas_v1.rle", atlas.build, key)
    cached(WWW / "orb_atlas_v2.rle", lambda path: atlas.build(path, alpha=True), key)
    packed = [f'orb_atlas_v{version}.lz4' for version in (3, *atlas.LAYOUTS)]
    for name, version in zip(packed, (3, *atlas.LAYOUTS)):
        cached(WWW / name, lambda path, version=version: atlas.build_packed(path, version=version), key)
    size = sum((WWW / name).stat().st_size for name in [*files, "voice_bg.png", "orb_atlas_v1.rle", "orb_atlas_v2.rle", *packed])
    print(f"{len(files) + 4} files, {size / 1e6:.1f} MB -> {WWW}")


if __name__ == "__main__":
    main(*sys.argv[1:2])
