#pragma once
// How Spelas nu's record moves (vinyl.h draws it): 33 1/3 rpm while it plays; a pause lifts the tonearm and the
// record slows to a stop in about a second; play starts it again in 0.6 s. No LVGL here, so tests run it natively.
#include <cmath>

namespace panel_vinyl {
constexpr float DEG_PER_S = 33.333f * 6.0f;   // 33 1/3 rpm
constexpr float STOP_S = 1.0f, START_S = 0.6f, LIFT_S = 0.4f;
constexpr float MAX_DT_S = 0.1f;              // a late frame (or a reopened sheet) does not jump
// Below this share of full speed the precomputed records (3.33 degrees apart on the page) change less often than a
// record turned live to its exact angle can be drawn (one in 14-24 ms, so about 30 a second): slowing down and
// spinning up moved in visible steps (Joel, 2026-10-28: "vinylen laggar" at pause and play). There the worker turns
// the cover live; from here up the records are smoother again.
constexpr float LIVE_BELOW = 0.5f;
constexpr float LIVE_AHEAD_S = 2.0f / 60;      // a live record is drawn about two display frames after it is asked

// Whether the record is turned live now: it moves, slower than LIVE_BELOW.
inline bool live_turn(float speed) { return speed > 0 && speed < LIVE_BELOW; }

// The angle a live record should show when it reaches the screen, LIVE_AHEAD_S after it is asked for.
inline float live_angle(float angle, float speed, float rpm_scale) {
  return std::fmod(angle + DEG_PER_S * speed * rpm_scale * LIVE_AHEAD_S, 360.0f);
}

struct Motion {
  float angle{0}, speed{1}, lift{0};          // degrees [0, 360); 0..1 of full speed; 0 on the record, 1 lifted
  float rpm_scale{1};                         // 45 / 33 1/3 while the knob says 45

  // Straight to the resting state for `playing`: a sheet opened on a paused song shows the arm already up.
  void settle(bool playing) {
    speed = playing ? 1.0f : 0.0f;
    lift = playing ? 0.0f : 1.0f;
  }

  // Advance by dt seconds. True while anything still moves, so a stopped record costs no redraws.
  bool step(float dt, bool playing) {
    if (dt <= 0) return moving(playing);
    if (dt > MAX_DT_S) dt = MAX_DT_S;
    float target = playing ? 1.0f : 0.0f;
    if (speed < target) speed = std::fmin(target, speed + dt / START_S);
    else if (speed > target) speed = std::fmax(target, speed - dt / STOP_S);
    float up = playing ? 0.0f : 1.0f;
    if (lift < up) lift = std::fmin(up, lift + dt / LIFT_S);
    else if (lift > up) lift = std::fmax(up, lift - dt / LIFT_S);
    angle = std::fmod(angle + DEG_PER_S * speed * rpm_scale * dt, 360.0f);
    return moving(playing);
  }

  bool moving(bool playing) const {
    return speed > 0 || lift != (playing ? 0.0f : 1.0f);
  }

  int rotation_tenths() const { return int(angle * 10.0f) % 3600; }   // LVGL's unit
};

// The tonearm in the sheet's coordinates (Joel's T4, 2026-10-28: matte black, a silver ring at the pivot, a
// counterweight behind it, a white cartridge, and a rest on the right it lies on when lifted), for a record centred
// at (cx, cy) with radius r. Sizes scale with r (the sheet's record is 72, the page's 144).
struct Arm {
  float pivot_x, pivot_y, end_x, end_y, head_x, head_y;
  float weight_x, weight_y;                     // the counterweight's end, behind the pivot
  float cart_x0, cart_y0, cart_x1, cart_y1;     // the cartridge on the headshell
  float rest_x, rest_y;                         // the rest, under the lifted arm
};

// groove: how far through the song (0 at its start, 1 at its end). The stylus follows the groove inward like a real
// one (Joel, 2026-10-28): from OUTER to INNER of the record's radius, the arm turning about its pivot at a fixed
// length. lift: 0 on the record, 1 lifted straight down from the pivot onto the rest, off the record.
constexpr float OUTER = 0.92f, INNER = 0.42f, ARM_LENGTH = 1.08f;
inline Arm arm(float cx, float cy, float r, float lift, float groove = 0.0f) {
  constexpr float PI = 3.14159265f;
  const float k = r / 144.0f;                   // T4 was drawn for the page's record
  // The pivot well right of the record, and the rest beside it (Joel: they were too close, 2026-10-28).
  const float px = cx + r * 1.22f, py = cy - r * 0.78f;
  const float length = r * ARM_LENGTH;
  groove = groove < 0 ? 0 : groove > 1 ? 1 : groove;
  const float rho = r * (OUTER + (INNER - OUTER) * groove);   // the stylus's distance from the spindle
  // The stylus is where a circle of the arm's length about the pivot meets one of radius rho about the spindle:
  // the angle at the pivot between the spindle and the stylus, on the side towards the front of the record.
  const float dx = cx - px, dy = cy - py, d = std::hypot(dx, dy);
  float c = (length * length + d * d - rho * rho) / (2 * length * d);
  c = c < -1 ? -1 : c > 1 ? 1 : c;
  const float base = std::atan2(dy, dx) - std::acos(c);   // minus: the front, below the pivot
  const float lifted = PI / 2;                  // straight down from the pivot
  const float a = base + (lifted - base) * lift;
  const float ex = px + length * std::cos(a), ey = py + length * std::sin(a);
  const float h = a + 0.4f, hl = 26 * k;        // the headshell: 26 px at 0.4 rad off the arm
  Arm out{};
  out.pivot_x = px; out.pivot_y = py; out.end_x = ex; out.end_y = ey;
  out.head_x = ex + hl * std::cos(h); out.head_y = ey + hl * std::sin(h);
  out.weight_x = px - 34 * k * std::cos(a); out.weight_y = py - 34 * k * std::sin(a);
  out.cart_x0 = ex + hl * 0.45f * std::cos(h); out.cart_y0 = ey + hl * 0.45f * std::sin(h);
  out.cart_x1 = ex + hl * 0.95f * std::cos(h); out.cart_y1 = ey + hl * 0.95f * std::sin(h);
  out.rest_x = px; out.rest_y = py + length * 0.7f;   // straight under the pivot, 0.7 of the way out
  return out;
}
}  // namespace panel_vinyl

#include <cstdint>
namespace panel_vinyl {
// Turns a square RGB565 cover by `degrees` into dst (size x size): inside the record's circle each pixel is the
// cover bilinearly sampled at the rotated point; outside it is `outside` (the still layer covers it anyway).
// src may be larger than dst (src_size >= size): the cover is then sampled down by src_size / size. Fixed-point
// steps along each row, only the circle's span per row, in TILE x TILE blocks: a whole row of the 288 px record
// reads a diagonal through the 166 kB cover in PSRAM, a block reads a small patch that stays in the cache. LVGL's
// own transform took 20 ms a frame for the 144 px record with the sheet open; whole rows took 3.5 ms (144 px) and
// 14.1 ms (288 px) on the panel (2026-10-28).
constexpr int TILE = 32;
// With a still layer (layer_rgb: size x size RGB565, layer_alpha: its A8 plane, as LVGL's RGB565A8 stores them) it
// is blended over the turned cover in the same pass, so LVGL copies one opaque image instead of blending a second
// one each frame; outside the circle the layer's own pixels are written.
inline void rotate(const uint16_t *src, int src_size, uint16_t *dst, int size, float degrees, uint16_t outside,
                   const uint16_t *layer_rgb = nullptr, const uint8_t *layer_alpha = nullptr) {
  constexpr float PI = 3.14159265f;
  const float a = degrees * PI / 180, k = float(src_size) / size;
  const float c = std::cos(a) * k, s = std::sin(a) * k;
  const int32_t du = int32_t(c * 65536), dv = int32_t(-s * 65536);      // one dst pixel right, in src
  const float half = size / 2.0f, src_half = src_size / 2.0f;
  const int limit = src_size - 1;
  const float r = half - 0.5f;
  // Spread RGB565 as 0b00000gggggg00000rrrrr000000bbbbb so one multiply blends all three channels.
  auto spread = [](uint32_t p) { return (p | (p << 16)) & 0x07E0F81Fu; };
  auto pack = [](uint32_t p) { p &= 0x07E0F81Fu; return uint16_t(p | (p >> 16)); };
  for (int y = 0; y < size; ++y) {       // first the circle's span per row, and the card outside it
    uint16_t *row = dst + y * size;
    const float fy = y + 0.5f - half, span2 = r * r - fy * fy;
    int x0 = size, x1 = -1;
    if (span2 > 0) {
      const float span = std::sqrt(span2);
      x0 = int(std::ceil(half - span - 0.5f)), x1 = int(std::floor(half + span - 0.5f));
      if (x0 < 0) x0 = 0;
      if (x1 > size - 1) x1 = size - 1;
    }
    for (int x = 0; x < size; ++x)
      if (x < x0 || x > x1) row[x] = layer_rgb ? layer_rgb[y * size + x] : outside;
  }
  for (int ty = 0; ty < size; ty += TILE)
    for (int tx = 0; tx < size; tx += TILE)
      for (int y = ty; y < ty + TILE && y < size; ++y) {
        const float fy = y + 0.5f - half, span2 = r * r - fy * fy;
        if (span2 <= 0) continue;
        const float span = std::sqrt(span2);
        int x0 = int(std::ceil(half - span - 0.5f)), x1 = int(std::floor(half + span - 0.5f));
        if (x0 < tx) x0 = tx;
        if (x1 > tx + TILE - 1) x1 = tx + TILE - 1;
        if (x1 > size - 1) x1 = size - 1;
        if (x0 > x1) continue;
        uint16_t *row = dst + y * size;
        // The source point of dst pixel x0's centre, rotated back by the angle about the centre (minus half a
        // source pixel, so the integer part is the top-left sample).
        const float fx = x0 + 0.5f - half;
        int32_t u = int32_t((src_half + fx * c + fy * s - 0.5f) * 65536);
        int32_t v = int32_t((src_half - fx * s + fy * c - 0.5f) * 65536);
        for (int x = x0; x <= x1; ++x, u += du, v += dv) {
          int iu = u >> 16, iv = v >> 16;
          uint32_t wu = (u >> 11) & 31, wv = (v >> 11) & 31;            // 5-bit weights
          if (iu < 0) { iu = 0; wu = 0; }
          if (iv < 0) { iv = 0; wv = 0; }
          if (iu >= limit) { iu = limit - 1; wu = 31; }
          if (iv >= limit) { iv = limit - 1; wv = 31; }
          const uint16_t *p = src + iv * src_size + iu;
          const uint32_t p00 = spread(p[0]), p10 = spread(p[1]), p01 = spread(p[src_size]), p11 = spread(p[src_size + 1]);
          const uint32_t top = ((p00 * (32 - wu) + p10 * wu) >> 5) & 0x07E0F81Fu;
          const uint32_t bottom = ((p01 * (32 - wu) + p11 * wu) >> 5) & 0x07E0F81Fu;
          uint32_t pixel = ((top * (32 - wv) + bottom * wv) >> 5) & 0x07E0F81Fu;
          if (layer_alpha) {
            const uint32_t a = (uint32_t(layer_alpha[y * size + x]) + 4) >> 3;      // 0..32
            if (a) pixel = ((pixel * (32 - a) + spread(layer_rgb[y * size + x]) * a) >> 5) & 0x07E0F81Fu;
          }
          row[x] = pack(pixel);
        }
      }
}
// The other half turn: the record upside down, inside its circle the pixel order reversed (one sequential
// copy), outside it the still layer's own pixels, so what lies around the record (the turntable's wood on the page)
// keeps its way up instead of flipping every half turn.
inline void flip_record(const uint16_t *src, uint16_t *dst, int size, const uint16_t *layer_rgb) {
  const float half = size / 2.0f, r = half - 0.5f;
  const int last = size * size - 1;
  for (int y = 0; y < size; ++y) {
    const float fy = y + 0.5f - half, span2 = r * r - fy * fy;
    int x0 = size, x1 = -1;
    if (span2 > 0) {
      const float span = std::sqrt(span2);
      x0 = int(std::ceil(half - span - 0.5f)), x1 = int(std::floor(half + span - 0.5f));
      if (x0 < 0) x0 = 0;
      if (x1 > size - 1) x1 = size - 1;
    }
    const int row = y * size;
    for (int x = 0; x < size; ++x) {
      const int i = row + x;
      dst[i] = (x >= x0 && x <= x1) || !layer_rgb ? src[last - i] : layer_rgb[i];
    }
  }
}
}  // namespace panel_vinyl
