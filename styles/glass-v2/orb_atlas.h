#pragma once
#include <array>
#include "esphome/components/image/image.h"
#include "esphome/core/log.h"
#include "lvgl.h"

namespace orb_atlas_ns {
inline uint16_t *delta_pixels = nullptr;
inline size_t delta_next = 1, delta_count = 0;
inline size_t frame_bytes = 220 * 220 * 2;

inline void delta_begin(esphome::image::Image *image) {
  delta_pixels = nullptr;
  delta_next = 1;
  delta_count = 0;
  if (image->get_width() != 220 || image->get_height() % 220 != 0 ||
      image->get_type() != esphome::image::IMAGE_TYPE_RGB565 || image->get_data_start() == nullptr) return;
  delta_pixels = reinterpret_cast<uint16_t *>(const_cast<uint8_t *>(image->get_data_start()));
  delta_count = image->get_height() / 220;
  frame_bytes = 220 * 220 * (image->has_transparency() ? 3 : 2);
}

// One frame per scheduler turn: reconstruct in place, yielding between frames so touch/audio
// remain serviced during startup. No second atlas allocation is needed.
inline void delta_step() {
  if (delta_pixels == nullptr || delta_next >= delta_count) return;
  const size_t pixels = frame_bytes / 2;
  uint16_t *dst = delta_pixels + delta_next * pixels;
  const uint16_t *previous = dst - pixels;
  for (size_t i = 0; i < pixels; i++) dst[i] ^= previous[i];
  delta_next++;
}
// The decoded atlas stays owned by Loader for the device's lifetime. Never release or
// refresh it while these descriptors are in use. Only the painting is refreshed each morning.
template<size_t Listen, size_t Morph, size_t Speak, size_t Settle>
bool install(esphome::image::Image *image, lv_obj_t *listen, lv_obj_t *think,
             lv_obj_t *morph, lv_obj_t *speak, lv_obj_t *settle) {
  constexpr size_t count = Listen + Morph + Speak + Settle;
  constexpr size_t side = 220;
  const size_t bytes = frame_bytes;
  static std::array<lv_image_dsc_t, count> frames{};
  static std::array<const void *, count> sources{};
  static std::array<const void *, (Listen + 2) / 3> thinking{};
  if (delta_next != count || image->get_data_start() == nullptr || image->get_width() != side ||
      image->get_height() != side * count || image->get_type() != esphome::image::IMAGE_TYPE_RGB565) {
    ESP_LOGE("orb_atlas", "Invalid orb atlas; keeping the static orb");
    return false;
  }
  for (size_t i = 0; i < count; i++) {
    auto &frame = frames[i];
    frame.header.magic = LV_IMAGE_HEADER_MAGIC;
    frame.header.cf = image->has_transparency() ? LV_COLOR_FORMAT_RGB565A8 : LV_COLOR_FORMAT_RGB565;
    frame.header.w = side;
    frame.header.h = side;
    frame.header.stride = side * 2;
    frame.data_size = bytes;
    frame.data = image->get_data_start() + i * bytes;
    sources[i] = &frame;
  }
  for (size_t i = 0; i < thinking.size(); i++) thinking[i] = sources[i * 3];
  lv_animimg_set_src(listen, sources.data(), Listen);
  lv_animimg_set_src(think, thinking.data(), thinking.size());
  lv_animimg_set_src(morph, sources.data() + Listen, Morph);
  lv_animimg_set_src(speak, sources.data() + Listen + Morph, Speak);
  lv_animimg_set_src(settle, sources.data() + Listen + Morph + Speak, Settle);
  // One-shot morph/settle retain their repeat_count=1; never replace them with an infinite loop.
  lv_animimg_start(listen);
  lv_animimg_start(think);
  lv_animimg_start(speak);
  ESP_LOGI("orb_atlas", "Installed %u frames without copying pixels", unsigned(count));
  return true;
}
}  // namespace orb_atlas_ns
