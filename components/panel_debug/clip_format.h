#pragma once
// A short burst of presented frames: which part of the screen, how long, and the bytes each frame sends.
// Pure C++ (tested on the Mac); the panel side is in diagnostics.cpp, the reader in tools/panel_diag.py.
#include <cstdint>
#include <cstring>

namespace panel_debug_clip {
constexpr int WIDTH = 1024, HEIGHT = 600;
constexpr uint32_t MIN_MS = 100, MAX_MS = 3000;
constexpr int SLOTS = 4;                    // frames waiting for the JPEG encoder
constexpr uint32_t FRAME_HEADER = 20;       // bytes before each frame's JPEG

struct Plan {
  uint32_t duration_ms{};
  int x{}, y{}, w{}, h{}, scale{1}, quality{75};
  int out_w{}, out_h{};  // the picture the host gets
  int enc_w{}, enc_h{};  // padded to the encoder's 16x16 blocks (YUV420); the host crops back to out_*
};

// Region inside the screen, at least 64 px a side; scale 1 or 2 must divide it evenly; quality 30–95.
inline bool plan(uint32_t duration_ms, int x, int y, int w, int h, int scale, int quality, Plan &out) {
  if (duration_ms < MIN_MS || duration_ms > MAX_MS || (scale != 1 && scale != 2) || quality < 30 || quality > 95)
    return false;
  if (x < 0 || y < 0 || w < 64 || h < 64 || x + w > WIDTH || y + h > HEIGHT || w % scale || h % scale) return false;
  out = Plan{duration_ms, x, y, w, h, scale, quality, w / scale, h / scale, 0, 0};
  out.enc_w = (out.out_w + 15) / 16 * 16;
  out.enc_h = (out.out_h + 15) / 16 * 16;
  return true;
}

inline void put32(uint8_t *at, uint32_t value) {
  for (int i = 0; i < 4; ++i) at[i] = uint8_t(value >> (8 * i));
}

// Little-endian: JPEG bytes, frame sequence, presentation time (us), grab time (us), main-task grab cost (us).
inline void frame_header(uint8_t *at, uint32_t jpeg, uint32_t sequence, uint32_t presented_us, uint32_t grabbed_us,
                         uint32_t cost_us) {
  put32(at, jpeg);
  put32(at + 4, sequence);
  put32(at + 8, presented_us);
  put32(at + 12, grabbed_us);
  put32(at + 16, cost_us);
}
}  // namespace panel_debug_clip
