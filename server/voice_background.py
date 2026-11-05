"""Prepare the voice page's subtle frosted backdrop once per chosen picture."""
from pathlib import Path


def make(source, destination):
    from PIL import Image, ImageFilter
    with Image.open(source) as image:
        # A two-pixel frost keeps the picture recognisable. Retain the approved
        # dark tint for white text, with a very faint milky glass highlight.
        glass = image.convert('RGB').filter(ImageFilter.GaussianBlur(2))
    glass = glass.point([round(v * 0.44 + 3) for v in range(256)] * 3)
    # An intermediate: panel_image re-encodes it. optimize=True cost 2.5-3.3 s on Jarvis for the same pixels.
    glass.save(destination, format='PNG', compress_level=1)
    return Path(destination)


if __name__ == '__main__':
    import sys
    make(sys.argv[1], sys.argv[2])
