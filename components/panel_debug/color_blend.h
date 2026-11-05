#pragma once
#include <cstddef>
#include <cstdint>

namespace panel_debug_copy {
// LVGL 9.5's lv_color_16_16_mix (misc/lv_color.c), inlined, with the foreground spread once per call
// instead of once per pixel. Same integer steps in the same order, so every pixel is bit-identical;
// tests/test_color_blend.py compares it with a verbatim copy of LVGL's function.
inline uint32_t spread565(uint16_t c) { return (c | (uint32_t(c) << 16)) & 0x7E0F81FU; }
inline uint16_t mix565(uint32_t fg, uint16_t c1, uint16_t c2, uint8_t mix) {
  if (mix == 255) return c1;
  if (mix == 0) return c2;
  if (c1 == c2) return c1;
  const uint32_t level = (uint32_t(mix) + 4) >> 3;
  const uint32_t bg = spread565(c2);
  const uint32_t result = ((((fg - bg) * level) >> 5) + bg) & 0x7E0F81FU;
  return uint16_t((result >> 16) | result);
}

// LVGL's three colour-blend loops for an RGB565 destination (draw/sw/blend/lv_draw_sw_blend_to_rgb565.c):
// a colour at one opacity (translucent fills), through a mask at full opacity (text, rounded corners), and
// through a mask at an opacity (LV_OPA_MIX2). Strides are in bytes, as in lv_draw_sw_blend_fill_dsc_t.
inline void color_opa_rows(uint8_t *dest, size_t stride, size_t w, size_t h, uint16_t color, uint8_t opa) {
  const uint32_t fg = spread565(color);
  for (size_t y = 0; y < h; ++y) {
    auto *row = reinterpret_cast<uint16_t *>(dest + y * stride);
    uint16_t last_in = uint16_t(row[0] + 1), last_out = 0;   // one pixel's result, reused for equal neighbours
    for (size_t x = 0; x < w; ++x) {
      const uint16_t in = row[x];
      if (in != last_in) { last_in = in; last_out = mix565(fg, color, in, opa); }
      row[x] = last_out;
    }
  }
}

inline void color_mask_rows(uint8_t *dest, size_t stride, size_t w, size_t h, uint16_t color,
                            const uint8_t *mask, size_t mask_stride) {
  const uint32_t fg = spread565(color);
  for (size_t y = 0; y < h; ++y) {
    auto *row = reinterpret_cast<uint16_t *>(dest + y * stride);
    const uint8_t *m = mask + y * mask_stride;
    for (size_t x = 0; x < w; ++x) {
      const uint8_t a = m[x];
      if (a == 0) continue;
      row[x] = a == 255 ? color : mix565(fg, color, row[x], a);
    }
  }
}

inline void color_mask_opa_rows(uint8_t *dest, size_t stride, size_t w, size_t h, uint16_t color,
                                const uint8_t *mask, size_t mask_stride, uint8_t opa) {
  const uint32_t fg = spread565(color);
  for (size_t y = 0; y < h; ++y) {
    auto *row = reinterpret_cast<uint16_t *>(dest + y * stride);
    const uint8_t *m = mask + y * mask_stride;
    for (size_t x = 0; x < w; ++x) {
      const uint8_t a = uint8_t((int32_t(m[x]) * opa) >> 8);   // LV_OPA_MIX2
      if (a == 0) continue;
      row[x] = mix565(fg, color, row[x], a);
    }
  }
}
}  // namespace panel_debug_copy
