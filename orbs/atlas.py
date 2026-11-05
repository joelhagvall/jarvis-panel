"""Pack the exact interpolated orb frames for native LVGL rendering.

HPA3 stores independent lossless LZ4 RGB565+A8 frames and retains only compressed bytes on
the panel. LVGL expands the current frame. Legacy HPA1/HPA2 delta/RLE packs remain published
for rollback; those versions expand the whole atlas once into PSRAM. Intermediate frames
are linear blends of existing artwork. Transparency lets every state follow the painting.
"""
from pathlib import Path
from array import array
import struct
import sys

SOURCE_COUNTS = {"listen": 24, "morph": 8, "speak": 24, "settle": 6}
COUNTS = {"listen": 64, "morph": 16, "speak": 48, "settle": 15}
# Later layouts per atlas version: (drawn frames' file prefix, how many) per state, then frames in the atlas.
# v4: the listening loop as the 96 drawn calm_* frames, no blends, played at 5.2 s.
# v5: v4 plus the speaking wave as the 72 drawn talk_* frames, no blends, played at 3.0 s.
# The panel's firmware names the version it reads; older ones stay published for older firmware.
LAYOUTS = {
    3: ({state: (state, n) for state, n in SOURCE_COUNTS.items()}, COUNTS),
    4: ({"listen": ("calm", 96), "morph": ("morph", 8), "speak": ("speak", 24), "settle": ("settle", 6)},
        {"listen": 96, "morph": 16, "speak": 48, "settle": 15}),
    5: ({"listen": ("calm", 96), "morph": ("morph", 8), "speak": ("talk", 72), "settle": ("settle", 6)},
        {"listen": 96, "morph": 16, "speak": 72, "settle": 15}),
    # v6: v5 with the drawn swing_* (24) and rest_* (18) transitions, which keep moving at the speed of the
    # state before them; the panel cross-fades into them from wherever that state's loop is.
    6: ({"listen": ("calm", 96), "morph": ("swing", 24), "speak": ("talk", 72), "settle": ("rest", 18)},
        {"listen": 96, "morph": 24, "speak": 72, "settle": 18}),
    # v7: v6 plus the empty glass (hollow_00) that the panel draws the voice-driven wave on.
    7: ({"listen": ("calm", 96), "morph": ("swing", 24), "speak": ("talk", 72), "settle": ("rest", 18),
         "hollow": ("hollow", 1)},
        {"listen": 96, "morph": 24, "speak": 72, "settle": 18, "hollow": 1}),
}
# v8: v7's layout drawn as the Jarvis line (orbs/line_orb.py, chosen 2026-10-28): no glass, a transparent hollow_00.
LAYOUTS[8] = LAYOUTS[7]
LINE_VERSIONS = {8}
PANEL_VERSION = 8   # the atlas styles/glass-v2/jarvis_orb.py builds the firmware for
SIZE = 220
ROOT = Path(__file__).resolve().parent.parent


def folder(version=3, alpha=True):
    """Where an atlas version's drawn frames are."""
    if version in LINE_VERSIONS:
        return ROOT / "orbs/line-jarvis"
    return ROOT / ("orbs/glass-jarvis" if alpha else "orbs/glass-jarvis-opaque")


def rgb565_precision(image):
    # The panel discards these low bits anyway. Removing them before PNG compression is lossless
    # at its RGB565 output and avoids sending/compressing invisible interpolation noise.
    return image.point([v & 0xF8 for v in range(256)] + [v & 0xFC for v in range(256)] +
                       [v & 0xF8 for v in range(256)])


def delta_encode(image):
    """XOR each RGB565 frame with its predecessor; exactly reversible at the panel's precision."""
    from PIL import Image
    rgb = image.tobytes()
    words = array("H", (((rgb[i] >> 3) << 11) | ((rgb[i + 1] >> 2) << 5) | (rgb[i + 2] >> 3)
                        for i in range(0, len(rgb), 3)))
    stride = SIZE * SIZE
    delta = (value ^ words[i - stride] if i >= stride else value for i, value in enumerate(words))
    encoded = bytearray(len(rgb))
    for i, value in enumerate(delta):
        encoded[i * 3] = (value >> 11) << 3
        encoded[i * 3 + 1] = ((value >> 5) & 63) << 2
        encoded[i * 3 + 2] = (value & 31) << 3
    return Image.frombytes("RGB", image.size, bytes(encoded))


def rle_encode(words):
    """16-bit PackBits: high bit repeats one word; otherwise the token prefixes literal words."""
    result = bytearray()
    i, length = 0, len(words)
    while i < length:
        end = i + 1
        while end < min(length, i + 32767) and words[end] == words[i]:
            end += 1
        if end - i >= 3:
            result.extend(struct.pack("<HH", 0x8000 | (end - i), words[i]))
            i = end
        else:
            start = i
            i = end
            while i < min(length, start + 32767):
                if i + 2 < length and words[i] == words[i + 1] == words[i + 2]:
                    break
                i += 1
            literals = words[start:i]
            if sys.byteorder != "little":
                literals.byteswap()
            result.extend(struct.pack("<H", i - start))
            result.extend(literals.tobytes())
    return bytes(result)


def layout(version=3):
    """(drawn sources, atlas counts) for an atlas version; v3 reads COUNTS when called, for tests."""
    if version == 3:
        return {state: (state, n) for state, n in SOURCE_COUNTS.items()}, COUNTS
    return LAYOUTS[version]


def frames(alpha=False, version=3):
    """The exact interpolated animation, shared by the raw and compressed containers."""
    from PIL import Image  # generators only read COUNTS and must work with plain Python
    sources, counts = layout(version)
    for state, count in counts.items():
        name, drawn = sources[state]
        source = [Image.open(folder(version, alpha) / f"{name}_{i:02d}.png").convert("RGBA" if alpha else "RGB")
                  for i in range(drawn)]
        if any(image.size != (SIZE, SIZE) for image in source):
            raise ValueError("Orb frames must be 220x220")
        loop = state in ("listen", "speak")
        for i in range(count):
            position = i * len(source) / count if loop else i * (len(source) - 1) / max(1, count - 1)
            index = int(position)
            following = (index + 1) % len(source) if loop else min(index + 1, len(source) - 1)
            frame = Image.blend(source[index], source[following], position - index)
            yield frame


def rgb565a8(frame):
    """LVGL's RGB565 colour plane followed by its lossless 8-bit alpha plane."""
    rgb = frame.convert('RGB').tobytes()
    colors = array('H', (((rgb[i] >> 3) << 11) | ((rgb[i + 1] >> 2) << 5) | (rgb[i + 2] >> 3)
                         for i in range(0, len(rgb), 3)))
    if sys.byteorder != 'little':
        colors.byteswap()
    return colors.tobytes() + frame.getchannel('A').tobytes()


def build_packed(destination, version=3):
    """Independent LVGL LZ4 frames; the panel never expands the entire atlas."""
    import lz4.block
    count = sum(layout(version)[1].values())
    offset = 12 + 4 * (count + 1)
    offsets, packets = [], []
    for frame in frames(alpha=True, version=version):
        raw = rgb565a8(frame)
        compressed = lz4.block.compress(raw, mode='high_compression', compression=12, store_size=False)
        packet = struct.pack('<III', 2, len(compressed), len(raw)) + compressed
        packet += b'\0' * (-len(packet) % 4)
        offsets.append(offset)
        packets.append(packet)
        offset += len(packet)
    offsets.append(offset)
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(struct.pack('<4sHHI', b'HPA3', SIZE, count, offset) +
                            struct.pack(f'<{len(offsets)}I', *offsets) + b''.join(packets))
    return destination


def build(destination, delta=True, alpha=False):
    from PIL import Image
    atlas = Image.new('RGBA' if alpha else 'RGB', (SIZE, SIZE * sum(COUNTS.values())))
    for row, frame in enumerate(frames(alpha=alpha)):
        atlas.paste(frame, (0, row * SIZE))
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.suffix == ".rle":
        if alpha:
            # LVGL RGB565A8: each frame has an RGB565 plane followed by an A8 plane.
            # The image remains independent of the chosen painting, including its soft glow.
            packed = bytearray()
            for row in range(sum(COUNTS.values())):
                frame = atlas.crop((0, row * SIZE, SIZE, (row + 1) * SIZE))
                packed.extend(rgb565a8(frame))
            words = array("H")
            words.frombytes(packed)
            if sys.byteorder != "little":
                words.byteswap()
        else:
            rgb = atlas.tobytes()
            words = array("H", (((rgb[i] >> 3) << 11) | ((rgb[i + 1] >> 2) << 5) | (rgb[i + 2] >> 3)
                                for i in range(0, len(rgb), 3)))
        stride = SIZE * SIZE * (3 if alpha else 2) // 2
        differences = array("H", (v ^ words[i - stride] if i >= stride else v for i, v in enumerate(words)))
        destination.write_bytes(struct.pack("<4sHHI", b"HPA2" if alpha else b"HPA1", SIZE, sum(COUNTS.values()), len(words) * 2) +
                                rle_encode(differences))
    else:
        (atlas if alpha else delta_encode(atlas) if delta else rgb565_precision(atlas)).save(destination, optimize=True)
    return destination


if __name__ == "__main__":
    path = build(ROOT / "server/www/orb_atlas_v1.rle")
    print(f"{sum(COUNTS.values())} frames, {path.stat().st_size:,} bytes -> {path}")
