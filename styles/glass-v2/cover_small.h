#pragma once
// The cover in two sizes from one download (2026-10-28): Home Assistant prepares 288 px for the Musik page's
// record, and this makes the 144 px copy the Spelas nu row and sheet show, averaging each 2 x 2 block once per
// cover instead of LVGL scaling it down on every frame. The copy lives in PSRAM.
#include <cstdint>
#include "lvgl.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#endif

namespace panel_cover {
constexpr int SMALL = 144;

struct Small {
  lv_image_dsc_t dsc{};
  uint16_t *pixels{nullptr};

  // The async artwork worker prepares this copy before publishing. Neither thumbnail is written while shown.
  void attach(uint16_t *ready) {
    pixels=ready;dsc.header.magic=LV_IMAGE_HEADER_MAGIC;dsc.header.cf=LV_COLOR_FORMAT_RGB565;
    dsc.header.w=SMALL;dsc.header.h=SMALL;dsc.header.stride=SMALL*2;
    dsc.data=reinterpret_cast<const uint8_t *>(pixels);dsc.data_size=SMALL*SMALL*2;
  }

  // Averages the RGB565 source down to 144 x 144 (any size: each output pixel is the mean of its source box).
  // False, and the copy unchanged, when the source is not RGB565 or no memory is left.
  bool make(const lv_image_dsc_t *src) {
    if (!src || !src->data || src->header.cf != LV_COLOR_FORMAT_RGB565 || src->header.w < SMALL ||
        src->header.h < SMALL)
      return false;
    if (!pixels) {
#ifdef USE_ESP32
      pixels = static_cast<uint16_t *>(heap_caps_malloc(SMALL * SMALL * 2, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
#else
      pixels = new uint16_t[SMALL * SMALL];
#endif
      if (!pixels) return false;
    }
    const int w = src->header.w, h = src->header.h;
    const uint32_t stride = src->header.stride ? src->header.stride : uint32_t(w) * 2;
    for (int y = 0; y < SMALL; ++y) {
      const int y0 = y * h / SMALL, y1 = (y + 1) * h / SMALL;
      for (int x = 0; x < SMALL; ++x) {
        const int x0 = x * w / SMALL, x1 = (x + 1) * w / SMALL;
        uint32_t r = 0, g = 0, b = 0, n = 0;
        for (int sy = y0; sy < y1; ++sy) {
          const uint16_t *row = reinterpret_cast<const uint16_t *>(src->data + sy * stride);
          for (int sx = x0; sx < x1; ++sx) {
            const uint16_t p = row[sx];
            r += p >> 11; g += (p >> 5) & 0x3F; b += p & 0x1F; ++n;
          }
        }
        pixels[y * SMALL + x] = uint16_t(((r / n) << 11) | ((g / n) << 5) | (b / n));
      }
    }
    dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
    dsc.header.cf = LV_COLOR_FORMAT_RGB565;
    dsc.header.w = SMALL;
    dsc.header.h = SMALL;
    dsc.header.stride = SMALL * 2;
    dsc.data = reinterpret_cast<const uint8_t *>(pixels);
    dsc.data_size = SMALL * SMALL * 2;
    return true;
  }
};

inline Small small;

// Points an image at the copy again: same pointer, new pixels. The firmware has no image cache
// (packed_orb.h), so LVGL reads the pixels anew; invalidating is enough.
inline void show(lv_obj_t *image) {
  lv_image_set_src(image, &small.dsc);
  lv_obj_invalidate(image);
}
}  // namespace panel_cover
