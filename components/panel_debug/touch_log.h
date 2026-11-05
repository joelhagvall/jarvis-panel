#pragma once
// Real finger samples as LVGL read them, and replay scripts made from them. Pure C++ (tested on the Mac).
#include <cstddef>
#include <cstdint>
#include <string_view>

namespace panel_debug_touch {
constexpr int WIDTH = 1024, HEIGHT = 600;
constexpr size_t MAX_REPLAY_SAMPLES = 3000;
constexpr uint32_t MAX_REPLAY_MS = 30000;
constexpr size_t MAX_REPLAY_BODY = 48 * 1024;

struct Sample {
  uint32_t t_ms;
  int16_t x, y;
  uint8_t pressed;
};

// The last N reads of the physical touchscreen while a finger was down, plus each release.
template<size_t N> struct Ring {
  Sample samples[N]{};
  uint32_t count{};  // total recorded; the newest is samples[(count - 1) % N]
  bool down{false};
  // Records only what matters: every read while pressed and the read that released it.
  bool observe(uint32_t now_ms, int x, int y, bool pressed) {
    if (!pressed && !down) return false;
    down = pressed;
    samples[count % N] = Sample{now_ms, int16_t(x), int16_t(y), uint8_t(pressed ? 1 : 0)};
    ++count;
    return true;
  }
  size_t size() const { return count < N ? count : N; }
  const Sample &at(size_t i) const { return samples[(count - size() + i) % N]; }  // 0 = oldest kept
};

inline bool unsigned_number(std::string_view text, uint32_t &value, uint32_t limit) {
  if (text.empty() || text.size() > 6) return false;
  value = 0;
  for (char c : text) {
    if (c < '0' || c > '9') return false;
    value = value * 10 + uint32_t(c - '0');
  }
  return value <= limit;
}

// "t,x,y,p;t,x,y,p;..." with t in ms from the start. The script must begin with a press, keep time
// non-decreasing, stay on the screen and end released, so a replay can never leave the pointer held down.
inline size_t parse_replay(std::string_view body, Sample *out, size_t capacity) {
  if (body.empty() || body.size() > MAX_REPLAY_BODY) return 0;
  size_t n = 0, start = 0;
  uint32_t previous = 0;
  while (start <= body.size()) {
    size_t end = body.find(';', start);
    if (end == std::string_view::npos) end = body.size();
    const auto item = body.substr(start, end - start);
    if (item.empty() || n >= capacity || n >= MAX_REPLAY_SAMPLES) return 0;
    uint32_t field[4];
    size_t at = 0;
    for (int k = 0; k < 4; ++k) {
      const size_t comma = k < 3 ? item.find(',', at) : item.size();
      if (comma == std::string_view::npos) return 0;
      const uint32_t limit = k == 0 ? MAX_REPLAY_MS : k == 1 ? WIDTH - 1 : k == 2 ? HEIGHT - 1 : 1;
      if (!unsigned_number(item.substr(at, comma - at), field[k], limit)) return 0;
      at = comma + 1;
    }
    if (at != item.size() + 1 || field[0] < previous) return 0;
    previous = field[0];
    out[n++] = Sample{field[0], int16_t(field[1]), int16_t(field[2]), uint8_t(field[3])};
    if (end == body.size()) break;
    start = end + 1;
  }
  if (n < 2 || !out[0].pressed || out[n - 1].pressed) return 0;
  return n;
}

// Index of the sample in force at elapsed ms (the last one whose time has come).
inline size_t sample_at(const Sample *samples, size_t count, size_t from, uint32_t elapsed) {
  while (from + 1 < count && samples[from + 1].t_ms <= elapsed) ++from;
  return from;
}
}  // namespace panel_debug_touch
