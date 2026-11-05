#pragma once
#ifdef USE_ESP32_VARIANT_ESP32P4
#include "esp_heap_caps.h"
#include "esphome/core/log.h"
#include "lvgl.h"
namespace fast_display {
inline void install() {
  auto *display = lv_display_get_default();
  if (!display) return;
  constexpr size_t bytes = 1024 * 32 * 2;
  // Keep the native ESPHome flush callback/DMA completion semantics. Only the rendering memory changes.
  void *buffer = heap_caps_aligned_alloc(64, bytes, MALLOC_CAP_INTERNAL | MALLOC_CAP_DMA | MALLOC_CAP_8BIT);
  if (!buffer) {
    ESP_LOGW("fast_display", "Internal draw buffer unavailable; keeping ESPHome's buffer");
    return;
  }
  lv_display_set_buffers(display, buffer, nullptr, bytes, LV_DISPLAY_RENDER_MODE_PARTIAL);
  lv_obj_invalidate(lv_screen_active());
  ESP_LOGI("fast_display", "Internal 64 KiB draw buffer installed (32 rows, native DMA flush)");
}
}  // namespace fast_display
#endif
