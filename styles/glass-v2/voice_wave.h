#pragma once
#include <algorithm>
#include <array>
#include <atomic>
#include <cmath>
#include <cstddef>
#include <cstdint>

// The speaking wave drawn on the panel, so it can follow Jarvis's voice instead of a drawn loop.
// It reproduces orbs/line_orb.py's wave: three glowing lines free on the painting (the Jarvis line, chosen
// 2026-10-28 over the glass sphere), a white core (sigma 1 px, x0.8) and a coloured halo (sigma 3.4 px)
// around each, quiet and fading out towards their ends. The light is tone-mapped as 1 - exp(-1.15 light)
// with the brightest channel as alpha, and laid over the frame it is drawn on (hollow_00, fully
// transparent), so the painting shows wherever the line is dark. The distance to a line is its vertical
// distance corrected by the local slope (one evaluation per pixel and line, cheap enough for the panel at
// 60 frames per second); tests/test_voice_wave.py compares with talk_*.png.
namespace voice_wave {
constexpr int SIDE = 220;
// RADIUS scales the wave's width and its harmonics, HEIGHT its lines' height (the glass wave's), FADE the part
// of each half over which the brightness eases to 0 at the ends (orbs/line_orb.py WIDTH, HEIGHT, FADE).
constexpr float CENTER = SIDE / 2.0f, RADIUS = SIDE * 0.42f, HEIGHT = SIDE * 0.33f, END = RADIUS * 0.94f;
constexpr float FADE = 0.35f;
constexpr int FIRST = int(CENTER - END), LAST = int(CENTER + END);   // columns with a sample
constexpr int SAMPLES = LAST - FIRST + 1;
// sqrt(Tables::D2_MAX): no light farther from a line. 11 px (was 12.65): the halo there is 0.5 % of its peak.
constexpr float REACH = 11.0f;
constexpr int NEIGHBOURS = 3;
// Per line and column: y, slope, 1/(1+slope^2); the bands (top and bottom rows as int16); then per row the
// light summed in one column (r, g, b).
constexpr size_t BAND_FLOATS = (3 * SIDE * 2 * sizeof(int16_t) + sizeof(float) - 1) / sizeof(float);
constexpr size_t SCRATCH_FLOATS = 9 * SIDE + BAND_FLOATS + 3 * SIDE;
constexpr size_t SCRATCH_BYTES = SCRATCH_FLOATS * sizeof(float);
constexpr float PI = 3.14159265358979f;

// Where the last render spent its time, for status (tools/panel wave --measure): the columns and bands, then
// the pixels. clock_us is set by the panel (esphome::micros); without it nothing is timed.
inline uint32_t (*clock_us)() = nullptr;
inline std::atomic<uint32_t> last_prep_us{0}, last_pixels_us{0};

struct Params {
  float t{};          // the wave's phase, in loops (talk_k is t = k / 72)
  float amp[3]{};     // each line's height, 0..1.1; talk_* used 0.75 + 0.25 sin(4 pi t)
};

struct Term { float harmonic, cycles, phase; };
struct Line { float r, g, b, amp; Term terms[3]; };
constexpr Line LINES[3] = {
    {0x38 / 255.f, 0xD9 / 255.f, 1.f, 0.55f, {{1.5f, 1, 0.0f}, {3.0f, 2, 1.3f}, {5.0f, -2, 2.1f}}},   // cyan
    {0x8C / 255.f, 0x5C / 255.f, 1.f, 0.40f, {{2.0f, -1, 0.7f}, {3.5f, 2, 2.5f}, {6.0f, 3, 0.4f}}},   // violet
    {0xBF / 255.f, 0xF4 / 255.f, 1.f, 0.28f, {{2.5f, 2, 1.9f}, {4.5f, -1, 0.2f}, {7.0f, 3, 1.1f}}},   // ice
};

struct Tables {
  static constexpr int D2_STEPS = 8, D2_MAX = 121, W_STEPS = 32, W_MAX = 8;
  std::array<float, SAMPLES> x{}, envelope{}, envelope_slope{};
  std::array<float, SIDE> fade{};   // brightness by pixel column
  std::array<uint16_t, D2_MAX * D2_STEPS + 1> core{}, halo{};
  std::array<float, W_MAX * W_STEPS + 1> screen{};
  Tables() {
    for (int n = 0; n < SAMPLES; ++n) {
      x[n] = std::clamp(FIRST + n + 0.5f - CENTER, -END, END);
      const float u = std::clamp(x[n] / RADIUS, -1.f, 1.f);
      envelope[n] = std::pow(std::cos(u * PI / 2), 1.6f);
      const float cu = std::cos(u * PI / 2), su = std::sin(u * PI / 2);
      envelope_slope[n] = cu > 0 ? -1.6f * std::pow(cu, 0.6f) * su * PI / (2 * RADIUS) : 0;
    }
    for (int i = 0; i <= D2_MAX * D2_STEPS; ++i) {
      const float d2 = float(i) / D2_STEPS;
      core[i] = uint16_t(std::lround(65535 * std::exp(-d2 / 2)));
      halo[i] = uint16_t(std::lround(65535 * std::exp(-d2 / (2 * 3.4f * 3.4f))));
    }
    for (int i = 0; i <= W_MAX * W_STEPS; ++i) screen[i] = 1 - std::exp(-1.15f * float(i) / W_STEPS);
    for (int X = 0; X < SIDE; ++X) {
      const float m = std::clamp((END - std::fabs(X + 0.5f - CENTER)) / (FADE * END), 0.f, 1.f);
      fade[X] = m * m * (3 - 2 * m);
    }
  }
};
inline const Tables &tables() { static const Tables t; return t; }

// Draw the wave over a 220x220 RGB565+A8 frame (hollow_00: transparent). scratch: SCRATCH_BYTES.
inline void render(uint8_t *frame, const Params &p, float *scratch) {
  const Tables &tb = tables();
  const uint32_t began = clock_us ? clock_us() : 0;
  float *ys[3], *slopes[3], *ks[3];
  for (int w = 0; w < 3; ++w) { ys[w] = scratch + w * SIDE; slopes[w] = scratch + (3 + w) * SIDE; ks[w] = scratch + (6 + w) * SIDE; }
  int16_t *bands = reinterpret_cast<int16_t *>(scratch + 9 * SIDE);   // per line and column: top, bottom
  for (int w = 0; w < 3; ++w) {
    const Line &line = LINES[w];
    const float scale = line.amp * HEIGHT * p.amp[w] / 1.9f;
    // sin and cos of each term along the columns by rotation, one step per column: no trig per column.
    float s[3], c[3], rs[3], rc[3], a[3], weight[3];
    for (int k = 0; k < 3; ++k) {
      a[k] = line.terms[k].harmonic * PI / RADIUS;
      weight[k] = 1 / (1 + k * 0.7f);
      const float arg = a[k] * tb.x[0] + 2 * PI * line.terms[k].cycles * p.t + line.terms[k].phase;
      s[k] = std::sin(arg); c[k] = std::cos(arg);
      rs[k] = std::sin(a[k]); rc[k] = std::cos(a[k]);
    }
    for (int n = 0; n < SAMPLES; ++n) {
      if (n > 0 && tb.x[n] != tb.x[n - 1])
        for (int k = 0; k < 3; ++k) {
          const float step = tb.x[n] - tb.x[n - 1];   // 1 px inside, less at a clamped end
          const float ss = step == 1.f ? rs[k] : std::sin(a[k] * step), cc = step == 1.f ? rc[k] : std::cos(a[k] * step);
          const float ns = s[k] * cc + c[k] * ss;
          c[k] = c[k] * cc - s[k] * ss;
          s[k] = ns;
        }
      float y = 0, dy = 0;
      for (int k = 0; k < 3; ++k) { y += s[k] * weight[k]; dy += c[k] * a[k] * weight[k]; }
      const float slope = scale * (dy * tb.envelope[n] + y * tb.envelope_slope[n]);
      const int X = FIRST + n;
      ys[w][X] = y * scale * tb.envelope[n];
      slopes[w][X] = slope;
      ks[w][X] = 1 / (1 + slope * slope);
    }
  }
  // Each line's rows per column: only the NEIGHBOURS columns either side can give a pixel its distance (as a
  // sample, or a tangent whose foot lies within 0.5 px of that column, so within half the slope of its
  // height), so rows farther than REACH from those are never lit. Exact for the distance below, and far
  // narrower than the old +-BAND columns where the line is steep (2026-10-28, about half the pixels).
  const int x0 = FIRST, x1 = LAST;    // the fade is 0 past the ends
  int top = SIDE, bottom = -1;
  for (int w = 0; w < 3; ++w)
    for (int X = x0; X <= x1; ++X) {
      float lo = 1e9f, hi = -1e9f;
      const int c0 = std::max(FIRST, X - NEIGHBOURS), c1 = std::min(LAST, X + NEIGHBOURS);
      for (int c = c0; c <= c1; ++c) {
        const float y = ys[w][c], ext = 0.5f * std::fabs(slopes[w][c]);
        lo = std::min(lo, y - ext); hi = std::max(hi, y + ext);
      }
      int16_t *b = bands + 2 * (w * SIDE + X);
      b[0] = int16_t(std::max(0, int(std::floor(lo + CENTER - REACH))));
      b[1] = int16_t(std::min(SIDE - 1, int(std::ceil(hi + CENTER + REACH))));
      top = std::min<int>(top, b[0]); bottom = std::max<int>(bottom, b[1]);
    }
  const uint32_t prepared = clock_us ? clock_us() : 0;
  uint16_t *colors = reinterpret_cast<uint16_t *>(frame);   // little-endian RGB565, as LVGL stores it
  uint8_t *alpha = frame + SIDE * SIDE * 2;
  auto screen = [&](float v) {   // linear between the table's steps
    const float at = std::min(v * Tables::W_STEPS, float(Tables::W_MAX * Tables::W_STEPS) - 0.001f);
    const int i = int(at);
    return tb.screen[i] + (tb.screen[i + 1] - tb.screen[i]) * (at - i);
  };
  // Column by column: each line lights only the rows of its band in that column, summed into acc (internal
  // RAM), then only the rows some line reached are laid over the frame. The same light per pixel as walking
  // every row of the whole box and testing each line's band (2026-10-28, which took 15.6 ms on the panel).
  float *acc = scratch + 9 * SIDE + BAND_FLOATS;     // per row: r, g, b
  (void)top; (void)bottom;
  for (int X = x0; X <= x1; ++X) {
    const float px = X + 0.5f - CENTER, bright = tb.fade[X];
    if (bright <= 0) continue;                     // past the line's ends
    int lo = SIDE, hi = -1;
    for (int w = 0; w < 3; ++w) {
      const int16_t *b = bands + 2 * (w * SIDE + X);
      lo = std::min<int>(lo, b[0]); hi = std::max<int>(hi, b[1]);
    }
    if (hi < lo) continue;
    std::fill(acc + 3 * lo, acc + 3 * (hi + 1), 0.f);
    const int c_lo = std::max(FIRST, X - NEIGHBOURS), c_hi = std::min(LAST, X + NEIGHBOURS);
    for (int w = 0; w < 3; ++w) {
      const int16_t *b = bands + 2 * (w * SIDE + X);
      const float own_y = ys[w][X], own_lean = slopes[w][X] * ks[w][X];
      const float core_scale = 0.8f * bright / 65535, halo_scale = bright / 65535;
      const float lr_ = LINES[w].r, lg_ = LINES[w].g, lb_ = LINES[w].b;
      for (int Y = b[0]; Y <= b[1]; ++Y) {
        const float py = Y + 0.5f - CENTER;
        // The nearest of three columns' tangents (where the closest point on one lies inside its column, so a
        // steep tangent cannot carry the glow past a peak) and samples: around the column the own tangent
        // points at, at most NEIGHBOURS away. 3 columns instead of 7, and nearer the true curve
        // (2026-10-28: mean error 4.2 instead of 6.8 against talk_*, about a quarter faster on the Mac).
        float d2 = 1e9f;
        const float lean = own_lean * (py - own_y);
        int aim = X + int(lean + (lean >= 0 ? 0.5f : -0.5f));
        aim = std::clamp(aim, c_lo, c_hi);
        for (int c = std::max(FIRST, aim - 1); c <= std::min(LAST, aim + 1); ++c) {
          const float dx = px - (c + 0.5f - CENTER), dy = py - ys[w][c], s = slopes[w][c], k = ks[w][c];
          d2 = std::min(d2, dx * dx + dy * dy);
          const float along = (dx + s * dy) * k;     // the foot's offset from the column centre
          if (along >= -0.5f && along <= 0.5f) { const float e = dy - s * dx; d2 = std::min(d2, e * e * k); }
        }
        if (d2 >= Tables::D2_MAX) continue;
        const int i = int(d2 * Tables::D2_STEPS);
        const float core = tb.core[i] * core_scale, halo = tb.halo[i] * halo_scale;
        float *at = acc + 3 * Y;
        at[0] += core + lr_ * halo;
        at[1] += core + lg_ * halo;
        at[2] += core + lb_ * halo;
      }
    }
    for (int Y = lo; Y <= hi; ++Y) {
      const float acc_r = acc[3 * Y], acc_g = acc[3 * Y + 1], acc_b = acc[3 * Y + 2];
      if (acc_r <= 0 && acc_g <= 0 && acc_b <= 0) continue;
      // The line's light, premultiplied, over what the frame holds there.
      const float lr = screen(acc_r), lg = screen(acc_g), lb = screen(acc_b);
      const float a = std::max(lr, std::max(lg, lb));
      const size_t at = size_t(Y) * SIDE + X;
      const uint8_t below = alpha[at];
      float out_a = a, r, g, bl;
      if (below == 0) {                              // hollow_00 is transparent: nothing to blend with
        if (a <= 0) continue;
        const float inv = 1 / a;
        r = lr * inv; g = lg * inv; bl = lb * inv;
      } else {
        const uint16_t base = colors[at];
        const float under = below / 255.f * (1 - a);
        out_a = a + under;
        const float inv = 1 / out_a;
        r = (lr + (base >> 11) / 31.f * under) * inv;
        g = (lg + ((base >> 5) & 63) / 63.f * under) * inv;
        bl = (lb + (base & 31) / 31.f * under) * inv;
      }
      colors[at] = uint16_t((int(std::min(r, 1.f) * 31 + 0.5f) << 11) | (int(std::min(g, 1.f) * 63 + 0.5f) << 5) |
                            int(std::min(bl, 1.f) * 31 + 0.5f));
      alpha[at] = uint8_t(int(255 * out_a + 0.5f));
    }
  }
  if (clock_us) {
    const uint32_t done = clock_us();
    last_prep_us.store(prepared - began, std::memory_order_relaxed);
    last_pixels_us.store(done - prepared, std::memory_order_relaxed);
  }
}
}  // namespace voice_wave
