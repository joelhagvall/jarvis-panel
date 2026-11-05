# Local runtime_image

Copied from ESPHome 2026.9.1's `components/runtime_image` (licence text in `LICENSE`). The only change is a
direct RGB565 path in the QOI decoder:

- `RuntimeImage::rgb565_pixels()` returns the buffer as 16-bit words when `draw_pixel()` would only store
  `color_to_565(color)` there: RGB565, opaque, little endian, aligned. Otherwise it returns nullptr.
- `QoiDecoder` uses that pointer at 1:1 scale and writes each pixel (and each run, clipped to the image)
  directly. Every other case keeps upstream's `ImageDecoder::draw()` per pixel.

Upstream's path cost two virtual calls, a float scale and two `ceil` per pixel: the 1024x600 background
(770 kB QOI) took 2.3 s to decode while the orb atlas downloaded at 1.7 MB/s beside it (2026-10-30,
docs/baseline/wifi-boot-2026-10-26.md). `tests/test_qoi_rgb565.py` compiles the real decoder on the host with
ASan/UBSan and checks the direct path against the draw path pixel for pixel.
Recheck the change when updating ESPHome; do not patch the installed Python package.
