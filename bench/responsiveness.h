#pragma once
// Cold, zero-initialised state goes to PSRAM's .bss (CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY): internal RAM
// was down to ~100 kB free on 2026-11-03 (docs/memory.md). Empty on host builds.
#if __has_include("esp_attr.h")
#include "esp_attr.h"
#endif
#ifndef EXT_RAM_BSS_ATTR
#define EXT_RAM_BSS_ATTR
#endif
// Main-task-only, allocation-free recording. JSON allocation occurs only on /status.
// Completion means LVGL refresh work including submission, not LCD scanout/photons.
#include <array>
#include <cstdint>
#include <string>
#include <functional>
#include <memory>
#include "vsync_clock.h"

namespace responsiveness {
struct Series {
  std::array<uint32_t, 128> us{};
  uint64_t total{};
  void add(uint32_t value) { us[total++ % us.size()] = value; }
  std::string json() const {
    const auto count = total < us.size() ? total : us.size();
    std::string out = "{\"total\":" + std::to_string(total) + ",\"us\":[";
    for (uint64_t i = total - count; i < total; ++i) {
      if (i != total - count) out += ',';
      out += std::to_string(us[i % us.size()]);
    }
    return out + "]}";
  }
};
struct Pending {
  uint32_t at{};
  bool active{};
  void mark(uint32_t now) { at = now; active = true; }
  void finish(uint32_t now, Series &series) {
    if (active) { series.add(now - at); active = false; }
  }
};
// The slowest render of the last two seconds and what it redrew (2026-10-31: a song change on Spelas nu repeated a
// frame; render_work showed 18-22 ms but not which areas). Areas come from LVGL's invalidated list at RENDER_START.
struct Slowest {
  static constexpr int RECTS = 8;
  static constexpr uint32_t WINDOW_US = 2000000;
  uint32_t render_us{}, at{}, pixels{}, areas{};
  std::array<std::array<int16_t, 4>, RECTS> rects{};
  std::string json() const {
    std::string out = "{\"render_us\":" + std::to_string(render_us) + ",\"at_us\":" + std::to_string(at) +
                      ",\"areas\":" + std::to_string(areas) + ",\"pixels\":" + std::to_string(pixels) + ",\"rects\":[";
    for (uint32_t i = 0; i < areas && i < RECTS; ++i) {
      if (i) out += ',';
      const auto &r = rects[i];
      out += "[" + std::to_string(r[0]) + "," + std::to_string(r[1]) + "," + std::to_string(r[2]) + "," + std::to_string(r[3]) + "]";
    }
    return out + "]}";
  }
};
struct Recorder {
  Series refresh_work, refresh_interval, remote_press, remote_release, physical_press, physical_release;
  Series layout_sync, render_work, flush_wait, flush_submit;
  // Frame boundary -> REFR_START, when a live display clock is attached.
  Series vsync_latency;
  // REFR_START -> REFR_READY of cycles that drew nothing. Double-buffered direct mode still
  // copies the previous frame's areas into the other buffer there (LVGL refr_sync_areas):
  // after a full-screen page change that is a whole-screen copy outside every other series.
  Series empty_refresh_work;
  Slowest slowest, current;
  const vsync::Clock *clock{};
  Pending remote_down, remote_up, physical_down, physical_up;
  uint32_t start{}, previous{};
  bool drew{}, have_previous{};
  uint32_t render_at{}, render_us{}, layout_us{}, wait_at{}, wait_us{}, submit_us{}, latency_us{};
  bool waiting{}, latency_valid{};
  void begin(uint32_t now) {
    start = now; drew = false; render_us = layout_us = wait_us = submit_us = 0; current.areas = current.pixels = 0;
    latency_valid = clock && clock->live(now);
    if (latency_valid) latency_us = now - clock->last_us.load(std::memory_order_acquire);
  }
  void render_begin(uint32_t now) { render_at = now; layout_us = now - start; }
  void render_finish(uint32_t now) { render_us += now - render_at; rendered(); }
  void wait_begin(uint32_t now) { wait_at = now; waiting = true; }
  void wait_finish(uint32_t now) { if (waiting) wait_us += now - wait_at; waiting = false; }
  void submitted(uint32_t elapsed) { submit_us += elapsed; }
  void rendered() { drew = true; }
  // RENDER_START: one area LVGL is about to redraw (joined areas are skipped by the caller).
  void area(int x1, int y1, int x2, int y2) {
    if (current.areas < Slowest::RECTS) current.rects[current.areas] = {int16_t(x1), int16_t(y1), int16_t(x2), int16_t(y2)};
    ++current.areas; current.pixels += uint32_t(x2 - x1 + 1) * uint32_t(y2 - y1 + 1);
  }
  void finish(uint32_t now) {
    if (!drew) {  // LVGL also emits REFR_READY for empty refresh cycles.
      empty_refresh_work.add(now - start);
      return;
    }
    drew = false;
    refresh_work.add(now - start);
    layout_sync.add(layout_us); render_work.add(render_us);
    if (render_us >= slowest.render_us || now - slowest.at > Slowest::WINDOW_US) {
      current.render_us = render_us; current.at = now; slowest = current;
    }
    flush_wait.add(wait_us); flush_submit.add(submit_us);
    if (latency_valid) vsync_latency.add(latency_us);
    if (have_previous) refresh_interval.add(now - previous);
    previous = now;
    have_previous = true;
    remote_down.finish(now, remote_press);
    remote_up.finish(now, remote_release);
    physical_down.finish(now, physical_press);
    physical_up.finish(now, physical_release);
  }
  void touch(uint32_t now, bool remote, bool release) {
    (remote ? (release ? remote_up : remote_down) : (release ? physical_up : physical_down)).mark(now);
  }
  std::string json() const {
    return ",\"responsiveness\":{\"schema\":1,\"refresh_work\":" + refresh_work.json() +
           ",\"refresh_interval\":" + refresh_interval.json() +
           ",\"remote_press\":" + remote_press.json() + ",\"remote_release\":" + remote_release.json() +
           ",\"physical_press\":" + physical_press.json() + ",\"physical_release\":" + physical_release.json() +
           ",\"layout_sync\":" + layout_sync.json() + ",\"render_work\":" + render_work.json() +
           ",\"flush_wait\":" + flush_wait.json() + ",\"flush_submit\":" + flush_submit.json() +
           ",\"vsync_latency\":" + vsync_latency.json() +
           ",\"empty_refresh_work\":" + empty_refresh_work.json() + ",\"slowest\":" + slowest.json() + "}";
  }
  std::function<std::string()> deferred_json() const {
    // Copy on the owner task. Encode only this immutable copy on the HTTP task.
    auto copy = std::make_shared<const Recorder>(*this);
    return [copy]() { return copy->json(); };
  }
};
inline Recorder recorder EXT_RAM_BSS_ATTR;
}  // namespace responsiveness
