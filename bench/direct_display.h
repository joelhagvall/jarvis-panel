#pragma once
#ifdef USE_ESP32_VARIANT_ESP32P4
#define HALLPANEL_DIRECT_DISPLAY 1
#include "esphome/components/mipi_dsi/mipi_dsi.h"
#include "esphome/core/application.h"
#include "esp_lcd_mipi_dsi.h"
#include "esp_timer.h"
#include "lvgl.h"
#include <atomic>
#include <functional>
#include "responsiveness.h"
#include "vsync_clock.h"
#include "esphome/components/panel_debug/pixel_copy.h"

namespace direct_display {
inline esphome::mipi_dsi::MipiDsi *panel{};
// Every frame boundary and every presentation, written by the frame interrupt.
inline vsync::Clock clock;
inline vsync::SourceClock vinyl_clock;
inline std::atomic<uint32_t> rendering_vinyl{0},submitted_vinyl{0};
inline SemaphoreHandle_t frame_sem{};
inline lv_display_t *active_display{};
inline std::atomic<bool> waiting{false};
inline bool double_buffered{false};
// Only the ESPHome main task writes these. Screenshot copies run on that same task.
inline const uint8_t *presented_pixels{};
inline uint32_t frame_sequence{};
// When the frame interrupt last started scanning a newly submitted frame (us). For /clip timestamps.
inline std::atomic<uint32_t> presented_at_us{0};
inline uint64_t flush_us{};
inline uint32_t flush_max_us{}, flush_count{};
inline int dirty_low{600}, dirty_high{-1}, previous_low{600}, previous_high{-1};

inline bool frame_done(esp_lcd_panel_handle_t, esp_lcd_dpi_panel_event_data_t *, void *) {
  // IDF calls this at every frame boundary: its DMA has finished one whole frame and
  // restarts with the buffer selected by the last submission.
  BaseType_t yield = pdFALSE;
  const bool presented = waiting.exchange(false, std::memory_order_acq_rel);
  const auto now=uint32_t(esp_timer_get_time());
  clock.frame(now, presented);
  vinyl_clock.frame(now,presented,submitted_vinyl.load(std::memory_order_acquire));
  if (presented) {
    presented_at_us.store(now, std::memory_order_release);
    // IDF has started scanning the submitted buffer; LVGL may reuse the old one now.
    // This is one of the LVGL functions explicitly safe to call from an ISR.
    lv_display_flush_ready(active_display);
    xSemaphoreGiveFromISR(frame_sem, &yield);
  }
  // Run the main loop now, so a frame paced by this boundary starts rendering at
  // once instead of at the next 1 ms poll.
  esphome::Application::wake_loop_isrsafe(&yield);
  return yield == pdTRUE;
}
// Snapshot on the UI task; format on the HTTP task.
inline std::function<std::string()> deferred_presentation() {
  const auto snapshot = clock.snapshot();
  const auto vinyl_snapshot = vinyl_clock.snapshot();
  const uint32_t now = esphome::micros();
  return [snapshot, vinyl_snapshot,now]() { return snapshot.json(now)+vinyl_snapshot.json(now,"vinyl_presentation"); };
}
// Time LVGL's refreshes spent waiting for the previous frame to be presented (main task only; page_draw.h).
inline uint64_t wait_us_total{};
inline void wait_for_frame(lv_display_t *) {
  // Normally the flip finishes between 25 fps redraws. If a redraw arrives sooner,
  // sleep rather than spinning on LVGL's flushing flag. Never reuse a scanned buffer.
  const int64_t start = esp_timer_get_time();
  xSemaphoreTake(frame_sem, portMAX_DELAY);
  wait_us_total += esp_timer_get_time() - start;
}
inline bool color_done(esp_lcd_panel_handle_t, esp_lcd_dpi_panel_event_data_t *, void *) {
  if (!xPortInIsrContext()) {
    xSemaphoreGive(panel->color_semaphore());
    return false;
  }
  BaseType_t yield = pdFALSE;
  xSemaphoreGiveFromISR(panel->color_semaphore(), &yield);
  return yield == pdTRUE;
}
inline void flush(lv_display_t *display, const lv_area_t *area, uint8_t *pixels) {
  dirty_low = std::min(dirty_low, int(area->y1));
  dirty_high = std::max(dirty_high, int(area->y2));
  if (lv_display_flush_is_last(display)) {
    const uint32_t start = esphome::micros();
    xSemaphoreTake(frame_sem, 0);
    // LVGL also synchronizes the preceding frame's dirty regions into the other buffer.
    const auto err = esp_lcd_panel_draw_bitmap(panel->panel_handle(), 0, std::min(dirty_low, previous_low),
                                              1024, std::max(dirty_high, previous_high) + 1, pixels);
    previous_low = dirty_low;
    previous_high = dirty_high;
    dirty_low = 600;
    dirty_high = -1;
    // Arm after submission. A completion before submission cannot release the old frame buffer.
    if (err == ESP_OK && double_buffered) {
      submitted_vinyl.store(rendering_vinyl.load(std::memory_order_acquire),std::memory_order_release);
      waiting.store(true, std::memory_order_release);
      clock.submitted();
    }
    if (err == ESP_OK) {
      presented_pixels = pixels;
      ++frame_sequence;
    }
    if (err != ESP_OK) {
      ESP_LOGE("direct_display", "Frame presentation failed (%s)", esp_err_to_name(err));
    }
    const uint32_t took = esphome::micros() - start;
    responsiveness::recorder.submitted(took);
    flush_us += took;
    flush_max_us = std::max(flush_max_us, took);
    flush_count++;
    // Keep LVGL's buffer busy until the display interrupt acknowledges the flip.
    // Returning here lets touch, audio and network processing continue meanwhile.
    if (err == ESP_OK && double_buffered) return;
  }
  lv_display_flush_ready(display);
}
inline bool copy_presented(uint8_t *destination, size_t size, uint32_t &sequence) {
  // Wait for the submitted flip, then copy while LVGL cannot draw concurrently.
  // The LCD and its ISR only read pixels. Includes screen, top layer and system layer.
  if (!double_buffered || !presented_pixels || waiting.load(std::memory_order_acquire) ||
      size != 1024 * 600 * 2) return false;
  if (!panel_pixel_copy_bytes(destination, presented_pixels, size)) std::memcpy(destination, presented_pixels, size);
  sequence = frame_sequence;
  return true;
}
// The frame on screen, without copying it (panel_debug's clips copy only a region). Main task only;
// false while a submitted frame still waits for the panel to start scanning it.
inline bool presented(const uint8_t *&pixels, uint32_t &sequence, uint32_t &at_us) {
  if (!double_buffered || !presented_pixels || waiting.load(std::memory_order_acquire)) return false;
  pixels = presented_pixels;
  sequence = frame_sequence;
  at_us = presented_at_us.load(std::memory_order_acquire);
  return true;
}
inline void install(esphome::mipi_dsi::MipiDsi *hardware, bool use_double = false) {
  auto *display = lv_display_get_default();
  void *first{}, *second{};
  if (!display || esp_lcd_dpi_panel_get_frame_buffer(hardware->panel_handle(), use_double ? 2 : 1, &first, &second) != ESP_OK ||
      !first || (use_double && !second)) {
    ESP_LOGE("direct_display", "Direct buffers unavailable; keeping ESPHome rendering");
    return;
  }
  frame_sem = xSemaphoreCreateBinary();
  if (!frame_sem) return;
  panel = hardware;
  active_display = display;
  double_buffered = use_double;
  esp_lcd_dpi_panel_event_callbacks_t callbacks{};
  callbacks.on_color_trans_done = color_done;
  callbacks.on_frame_buf_complete = frame_done;
  if (esp_lcd_dpi_panel_register_event_callbacks(panel->panel_handle(), &callbacks, nullptr) != ESP_OK) return;
  // Never clear the buffers here. Before this runs (priority -95, ~2.6 s into boot) ESPHome has already copied
  // the boot splash into the scanned-out buffer (`first`, the DPI driver's current one). Zeroing it showed a black
  // screen until LVGL's next full frame was presented, ~62 ms: the "black blink" during the logo (2026-10-26).
  // The full invalidation below makes LVGL draw every pixel into the hidden buffer before it is shown.
  lv_display_set_buffers(display, use_double ? second : first, use_double ? first : nullptr, 1024 * 600 * 2, LV_DISPLAY_RENDER_MODE_DIRECT);
  lv_display_set_flush_cb(display, flush);
  if (use_double) lv_display_set_flush_wait_cb(display, wait_for_frame);
  lv_obj_invalidate(lv_screen_active());
  ESP_LOGI("direct_display", "%s-buffer DIRECT rendering installed (1024x600 RGB565)", use_double ? "Double" : "Single");
}
inline void report() {
  ESP_LOGI("direct_display", "submit count %u avg %.2f ms max %.2f ms", flush_count,
           flush_count ? flush_us / (1000.0 * flush_count) : 0, flush_max_us / 1000.0);
  flush_us = flush_max_us = flush_count = 0;
}
}  // namespace direct_display
#endif
