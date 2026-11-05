// Measures LVGL rendering and memory while the panel runs, and logs it every few seconds.
//
//   [bench] redraws/s 11.8 | render avg 21.4 ms, max 48.0 ms | PSRAM free 27512 kB (lowest 27200 kB) | internal free 312 kB
//
// "redraws/s" counts frames where LVGL actually drew something, so it shows the real
// animation rate (the orb is set to 12 frames/s, fades want more). "render" is the time
// from LVGL RENDER_START to RENDER_READY, including flush submission. It excludes
// layout/buffer synchronization and asynchronous scanout. responsiveness.h records
// the complete nonempty refresh cycle separately.
#pragma once
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "lvgl.h"
#ifdef USE_ESP32
#include "src/display/lv_display_private.h"   // inv_areas: what the slowest render redrew
#endif
#include <algorithm>
#include <array>
#include "responsiveness.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#endif

namespace bench {
inline uint32_t render_start = 0, frames = 0, window_start = 0;
inline uint64_t busy_us = 0;
inline uint64_t lifetime_frames = 0, lifetime_busy_us = 0;
inline uint32_t lifetime_max_us = 0, touch_last_us = 0;
inline uint32_t max_us = 0;
inline uint32_t touch_started = 0;
inline std::array<uint32_t, 64> touch_us{};
inline size_t touch_count = 0;

inline void on_event(lv_event_t *e) {
  const uint32_t now = esphome::micros();
  const lv_event_code_t code = lv_event_get_code(e);
  if (code == LV_EVENT_REFR_START) responsiveness::recorder.begin(now);
  if (code == LV_EVENT_RENDER_START) {
    responsiveness::recorder.render_begin(now);
    auto *display = lv_display_get_default();
    for (int i = 0; i < display->inv_p; ++i)
      if (!display->inv_area_joined[i]) {
        const auto &a = display->inv_areas[i];
        responsiveness::recorder.area(a.x1, a.y1, a.x2, a.y2);
      }
  }
  if (code == LV_EVENT_RENDER_READY) responsiveness::recorder.render_finish(now);
  if (code == LV_EVENT_FLUSH_WAIT_START) responsiveness::recorder.wait_begin(now);
  if (code == LV_EVENT_FLUSH_WAIT_FINISH) responsiveness::recorder.wait_finish(now);
  const bool drew = responsiveness::recorder.drew;
  if (code == LV_EVENT_REFR_READY) responsiveness::recorder.finish(now);
  if (code == LV_EVENT_REFR_READY && drew && touch_started != 0) {
    const uint32_t took = now - touch_started;
    touch_started = 0;
    {
      touch_us[touch_count++ % touch_us.size()] = took;
      touch_last_us = took;
      ESP_LOGI("latency", "Touch sample -> next refresh ready %.1f ms", took / 1000.0f);
    }
  }
  if (code == LV_EVENT_RENDER_START) {
    render_start = now;
  } else if (code == LV_EVENT_RENDER_READY && render_start != 0) {
    const uint32_t took = now - render_start;
    busy_us += took;
    lifetime_busy_us += took;
    lifetime_frames++;
    lifetime_max_us = std::max(lifetime_max_us, took);
    if (took > max_us) max_us = took;
    frames++;
    render_start = 0;
  }
}
}  // namespace bench

struct BenchSnapshot {
  float redraws_per_s{}, average_ms{}, maximum_ms{}, touch_last_ms{}, touch_p95_ms{};
};
// Read without resetting the existing Home Assistant/bench windows.
inline BenchSnapshot bench_snapshot() {
  BenchSnapshot result;
  const float seconds = (esphome::micros() - bench::window_start) / 1e6f;
  result.redraws_per_s = seconds > 0 ? bench::frames / seconds : 0;
  result.average_ms = bench::frames ? bench::busy_us / 1000.0f / bench::frames : 0;
  result.maximum_ms = bench::max_us / 1000.0f;
  result.touch_last_ms = bench::touch_last_us / 1000.0f;
  const size_t count = std::min(bench::touch_count, bench::touch_us.size());
  if (count) {
    auto values = bench::touch_us;
    std::sort(values.begin(), values.begin() + count);
    result.touch_p95_ms = values[(count * 95 + 99) / 100 - 1] / 1000.0f;
  }
  return result;
}

inline void bench_install() {
  lv_display_t *display = lv_display_get_default();
  if (display == nullptr) {
    ESP_LOGW("bench", "No LVGL display yet");
    return;
  }
  lv_display_add_event_cb(display, bench::on_event, LV_EVENT_REFR_READY, nullptr);
  lv_display_add_event_cb(display, bench::on_event, LV_EVENT_REFR_START, nullptr);
  lv_display_add_event_cb(display, bench::on_event, LV_EVENT_RENDER_START, nullptr);
  lv_display_add_event_cb(display, bench::on_event, LV_EVENT_RENDER_READY, nullptr);
  lv_display_add_event_cb(display, bench::on_event, LV_EVENT_FLUSH_WAIT_START, nullptr);
  lv_display_add_event_cb(display, bench::on_event, LV_EVENT_FLUSH_WAIT_FINISH, nullptr);
  bench::window_start = esphome::micros();
  ESP_LOGI("bench", "Measuring LVGL rendering");
}

inline void bench_report(const char *phase) {
  const uint32_t now = esphome::micros();
  const float secs = (now - bench::window_start) / 1e6f;
  const float avg_ms = bench::frames ? bench::busy_us / 1000.0f / bench::frames : 0.0f;
#ifdef USE_ESP32
  ESP_LOGI("bench", "%s | redraws/s %.1f | render avg %.1f ms, max %.1f ms | PSRAM free %u kB (lowest %u kB) | internal free %u kB",
           phase, bench::frames / secs, avg_ms, bench::max_us / 1000.0f,
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_SPIRAM) / 1024),
           (unsigned) (heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM) / 1024),
           (unsigned) (heap_caps_get_free_size(MALLOC_CAP_INTERNAL) / 1024));
#else
  ESP_LOGI("bench", "%s | redraws/s %.1f | render avg %.1f ms, max %.1f ms", phase, bench::frames / secs, avg_ms,
           bench::max_us / 1000.0f);
#endif
  bench::frames = 0;
  bench::busy_us = 0;
  bench::max_us = 0;
  bench::window_start = now;
}

// The same numbers for Home Assistant (board.py, every minute): read and start a new window, without logging.
inline void bench_take(float &redraws_per_s, float &avg_ms, float &max_ms) {
  const uint32_t now = esphome::micros();
  const float secs = (now - bench::window_start) / 1e6f;
  redraws_per_s = secs > 0 ? bench::frames / secs : 0.0f;
  avg_ms = bench::frames ? bench::busy_us / 1000.0f / bench::frames : 0.0f;
  max_ms = bench::max_us / 1000.0f;
  bench::frames = 0;
  bench::busy_us = 0;
  bench::max_us = 0;
  bench::window_start = now;
}

// True after a software restart (an OTA update, a restart from Home Assistant), not after power on or a crash:
// the boot's ready sound stays quiet then (board.py).
#ifdef USE_ESP32
#include "esp_system.h"
inline bool boot_was_software() { return esp_reset_reason() == ESP_RST_SW; }
#else
inline bool boot_was_software() { return false; }
#endif

// This starts after the touch controller has sampled input; it is not finger-to-photon timing.
inline void bench_touch_mark(bool remote = false) {
  bench::touch_started = esphome::micros();
  responsiveness::recorder.touch(bench::touch_started, remote, false);
}
inline void bench_touch_release(bool remote = false) {
  responsiveness::recorder.touch(esphome::micros(), remote, true);
}
inline void bench_touch_report() {
  const size_t count = std::min(bench::touch_count, bench::touch_us.size());
  if (!count) return;
  auto values = bench::touch_us;
  std::sort(values.begin(), values.begin() + count);
  const size_t p95 = (count * 95 + 99) / 100 - 1;
  ESP_LOGI("latency", "Touch sample -> next refresh n=%u p50=%.1f ms p95=%.1f ms", (unsigned) count,
           values[(count - 1) / 2] / 1000.0f, values[p95] / 1000.0f);
  bench::touch_count = 0;
}
