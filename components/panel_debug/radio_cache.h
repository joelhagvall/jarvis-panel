#pragma once
#include <cstdint>

namespace esphome::panel_debug {
// Owned by the sampling task. RSSI is sampled at most once per interval (1 s by default).
struct RadioCache {
  bool attempted{}, valid{};
  uint32_t sampled_at{};
  int value{};
  bool due(uint32_t now, uint32_t interval_ms = 1000) const {
    return !attempted || uint32_t(now - sampled_at) >= interval_ms;
  }
  void record(uint32_t now, bool success, int rssi) { attempted = true; valid = success; sampled_at = now; value = rssi; }
  void clear() { attempted = valid = false; }
  uint32_t age(uint32_t now) const { return now - sampled_at; }
};
}  // namespace esphome::panel_debug
