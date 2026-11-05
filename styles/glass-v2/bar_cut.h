// The shared tab bar's glass, cut out of the background picture on the panel (gen.py's shared_bar).
//
// bg has the tab bar and the mic button baked in (bake.py). The top layer shows this cut-out over the pages, so
// when a page slides the bar stands still. Inside the two capsules the cut is opaque; outside it fades over
// FEATHER px, so the baked shadow blends into the picture sliding under it. Called after every new bg.
#pragma once
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include "lvgl.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#endif

namespace bar_cut_ns {
constexpr int X0 = 222, Y0 = 500, W = 580, H = 100;          // gen.py's BAR_CUT
constexpr float FEATHER = 22.0f;
// gen.py's RECTS: the tab bar and the mic button, both capsules
constexpr float SHAPES[2][4] = {{246, 516, 460, 60}, {718, 516, 60, 60}};
inline uint8_t *buf = nullptr;
inline lv_image_dsc_t dsc{};

inline float capsule_distance(float px, float py, const float *r) {
  const float rad = r[3] / 2, cy = r[1] + rad;
  const float cx = std::clamp(px, r[0] + rad, r[0] + r[2] - rad);
  return std::hypot(px - cx, py - cy) - rad;
}
}  // namespace bar_cut_ns

inline void bar_cut(const lv_image_dsc_t *bg, lv_obj_t *img) {
  using namespace bar_cut_ns;
  if (bg == nullptr || bg->data == nullptr || bg->header.w < X0 + W || bg->header.h < Y0 + H ||
      bg->header.cf != LV_COLOR_FORMAT_RGB565)
    return;  // not downloaded yet; the next bg calls this again
  if (buf == nullptr) {
#ifdef USE_ESP32
    buf = static_cast<uint8_t *>(heap_caps_malloc(W * H * 3, MALLOC_CAP_SPIRAM));
#else
    buf = static_cast<uint8_t *>(malloc(W * H * 3));
#endif
    if (buf == nullptr)
      return;
  }
  const uint32_t stride = bg->header.stride ? bg->header.stride : bg->header.w * 2;
  uint8_t *alpha = buf + W * H * 2;  // RGB565A8: the colours, then one alpha byte per pixel
  for (int y = 0; y < H; y++) {
    const uint8_t *row = bg->data + (Y0 + y) * stride + X0 * 2;
    std::copy(row, row + W * 2, buf + y * W * 2);
    for (int x = 0; x < W; x++) {
      const float px = X0 + x + 0.5f, py = Y0 + y + 0.5f;
      const float d = std::min(capsule_distance(px, py, SHAPES[0]), capsule_distance(px, py, SHAPES[1]));
      const float t = d <= 0 ? 1.0f : d >= FEATHER ? 0.0f : 1.0f - d / FEATHER;
      alpha[y * W + x] = static_cast<uint8_t>(255.0f * t * t);
    }
  }
  dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
  dsc.header.cf = LV_COLOR_FORMAT_RGB565A8;
  dsc.header.w = W;
  dsc.header.h = H;
  dsc.header.stride = W * 2;
  dsc.data_size = W * H * 3;
  dsc.data = buf;
  lv_image_set_src(img, static_cast<const void *>(nullptr));  // the same dsc again would not be read again (ESPHome has LVGL's cache off)
  lv_image_set_src(img, &dsc);
  lv_obj_remove_flag(img, LV_OBJ_FLAG_HIDDEN);
  lv_obj_invalidate(img);
}
