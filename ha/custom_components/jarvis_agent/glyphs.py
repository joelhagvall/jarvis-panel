"""The characters the panel's Spelas nu fonts have, and text folded to them, so the panel never shows a box.

Equal to styles/glass-v2/gen.py PLAQUE_GLYPHS (the fonts in MUSIC_FONTS); tests/test_plaque_glyphs.py keeps them
equal. No Home Assistant imports: the free tests load this file on its own.
"""
import unicodedata

PANEL_GLYPHS = frozenset([chr(c) for c in range(0x20, 0x7F)] + list("åäöÅÄÖ°µ·–³…→éèüÜæøÆØ’‘”“—€")
                         + [chr(c) for c in range(0xA1, 0x180) if c not in (0xAD, 0x149)])


def fold(text):
    """A letter the fonts lack loses its accent (ő -> o); anything else becomes "?"."""
    out = []
    for ch in text:
        if ch in PANEL_GLYPHS:
            out.append(ch)
            continue
        base = "".join(c for c in unicodedata.normalize("NFKD", ch) if not unicodedata.combining(c))
        out.append(base if base and all(c in PANEL_GLYPHS for c in base) else "?")
    return "".join(out)
