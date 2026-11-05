#pragma once
#include "esphome/core/application.h"
#include "lvgl.h"

// Keep scheduler quantization small enough to investigate the 16.67 ms frame budget.
// Physical input has its own 5 ms LVGL read period, independently of GT911 polling.
// This changes scheduling only: no pixel/asset, display clock or buffer changes.
inline void frame_pacing_install(uint32_t period_ms = 1) {
  esphome::App.set_loop_interval(period_ms);
  if (auto *display = lv_display_get_default())
    lv_timer_set_period(lv_display_get_refr_timer(display), period_ms);
  if (auto *animation = lv_anim_get_timer()) lv_timer_set_period(animation, period_ms);
  for (auto *input = lv_indev_get_next(nullptr); input; input = lv_indev_get_next(input))
    if (auto *timer = lv_indev_get_read_timer(input)) lv_timer_set_period(timer, 5);
}
