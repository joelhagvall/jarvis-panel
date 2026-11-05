"""color_blend.h's inlined colour blends are bit-identical to LVGL 9.5's own loops, padding untouched."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

CODE = r'''
#include "color_blend.h"
#include <cassert>
#include <cstdint>
#include <random>
#include <vector>
// Verbatim from LVGL 9.5 misc/lv_color.c.
uint16_t lv_color_16_16_mix(uint16_t c1, uint16_t c2, uint8_t mix)
{
    if(mix == 255) return c1;
    if(mix == 0) return c2;
    if(c1 == c2) return c1;

    uint16_t ret;

    /* Source: https://stackoverflow.com/a/50012418/1999969*/
    mix = (uint32_t)((uint32_t)mix + 4) >> 3;

    /*0x7E0F81F = 0b00000111111000001111100000011111*/
    uint32_t bg = (uint32_t)(c2 | ((uint32_t)c2 << 16)) & 0x7E0F81F;
    uint32_t fg = (uint32_t)(c1 | ((uint32_t)c1 << 16)) & 0x7E0F81F;
    uint32_t result = ((((fg - bg) * mix) >> 5) + bg) & 0x7E0F81F;
    ret = (uint16_t)(result >> 16) | result;

    return ret;
}
#define LV_OPA_MIX2(a1, a2) ((uint8_t)(((int32_t)(a1) * (a2)) >> 8))
// LVGL's per-pixel meaning of its three loops (draw/sw/blend/lv_draw_sw_blend_to_rgb565.c).
void lvgl_opa(uint16_t *d, int stride_px, int w, int h, uint16_t c, uint8_t opa) {
  for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) d[y * stride_px + x] = lv_color_16_16_mix(c, d[y * stride_px + x], opa);
}
void lvgl_mask(uint16_t *d, int stride_px, int w, int h, uint16_t c, const uint8_t *m, int ms) {
  for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) d[y * stride_px + x] = lv_color_16_16_mix(c, d[y * stride_px + x], m[y * ms + x]);
}
void lvgl_mask_opa(uint16_t *d, int stride_px, int w, int h, uint16_t c, const uint8_t *m, int ms, uint8_t opa) {
  for (int y = 0; y < h; y++) for (int x = 0; x < w; x++)
    d[y * stride_px + x] = lv_color_16_16_mix(c, d[y * stride_px + x], LV_OPA_MIX2(m[y * ms + x], opa));
}
int main() {
  std::mt19937 rng(7);
  // The mix itself: every alpha for many colour pairs, equal colours and the extremes included.
  for (int i = 0; i < 200000; i++) {
    const uint16_t a = uint16_t(rng()), b = uint16_t(i % 7 == 0 ? a : rng());
    const uint8_t mix = uint8_t(rng());
    assert(panel_debug_copy::mix565(panel_debug_copy::spread565(a), a, b, mix) == lv_color_16_16_mix(a, b, mix));
  }
  for (uint16_t a : {uint16_t(0), uint16_t(0xFFFF), uint16_t(0xF800), uint16_t(0x07E0), uint16_t(0x001F)})
    for (int b = 0; b < 65536; b += 7)
      for (int mix = 0; mix < 256; mix++)
        assert(panel_debug_copy::mix565(panel_debug_copy::spread565(a), a, uint16_t(b), uint8_t(mix)) ==
               lv_color_16_16_mix(a, uint16_t(b), uint8_t(mix)));
  // The loops: odd sizes and strides, runs of equal pixels, guard pixels around the area.
  for (int round = 0; round < 300; round++) {
    const int w = 1 + rng() % 40, h = 1 + rng() % 9, stride = w + rng() % 5, ms = w + rng() % 4, off = rng() % 3;
    std::vector<uint16_t> start(off + stride * h + 4), ours, theirs;
    for (auto &p : start) p = (rng() % 3) ? uint16_t(rng()) : uint16_t(0x4208);
    std::vector<uint8_t> mask(ms * h);
    for (auto &v : mask) { const int r = rng() % 4; v = r == 0 ? 0 : r == 1 ? 255 : uint8_t(rng()); }
    const uint16_t color = uint16_t(rng());
    const uint8_t opa = uint8_t(rng());
    ours = theirs = start;
    panel_debug_copy::color_opa_rows(reinterpret_cast<uint8_t *>(ours.data() + off), stride * 2, w, h, color, opa);
    lvgl_opa(theirs.data() + off, stride, w, h, color, opa);
    assert(ours == theirs);
    ours = theirs = start;
    panel_debug_copy::color_mask_rows(reinterpret_cast<uint8_t *>(ours.data() + off), stride * 2, w, h, color, mask.data(), ms);
    lvgl_mask(theirs.data() + off, stride, w, h, color, mask.data(), ms);
    assert(ours == theirs);
    ours = theirs = start;
    panel_debug_copy::color_mask_opa_rows(reinterpret_cast<uint8_t *>(ours.data() + off), stride * 2, w, h, color,
                                          mask.data(), ms, opa);
    lvgl_mask_opa(theirs.data() + off, stride, w, h, color, mask.data(), ms, opa);
    assert(ours == theirs);
  }
}
'''


class ColorBlendTest(unittest.TestCase):
    def test_bit_identical_to_lvgl(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'test.cpp').write_text(CODE)
            binary = folder / 'test'
            subprocess.run(['c++', '-std=c++17', '-O1', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I' + str(ROOT / 'components/panel_debug'), str(folder / 'test.cpp'), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=60)


if __name__ == '__main__':
    unittest.main()
