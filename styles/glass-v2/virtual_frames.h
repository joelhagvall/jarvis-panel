#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <algorithm>

namespace packed_orb {
constexpr uint16_t NO_FRAME = 1023;
// A recipe whose second is WAVE draws the voice wave (voice_wave.h) on its first frame, the empty glass.
constexpr uint16_t WAVE = 1022;
struct Recipe { uint16_t first{}, second{}, weight{}, next{NO_FRAME}; };

inline void virtual_sequence(Recipe *recipes, size_t output, size_t count, size_t first, size_t sources,
                             bool loop, uint16_t after = NO_FRAME) {
  const size_t denominator = loop ? count : count - 1;
  for (size_t i = 0; i < count; ++i) {
    const size_t position = i * (loop ? sources : sources - 1);
    const size_t a = position / denominator;
    const size_t b = a + 1 < sources ? a + 1 : loop ? 0 : a;
    recipes[output + i] = {uint16_t(first + a), uint16_t(first + b),
                          uint16_t((position % denominator) * 256 / denominator),
                          uint16_t(i + 1 < count ? output + i + 1 : loop ? output : after)};
  }
}

// Interpolate at the panel's RGB565 precision, with a separate straight A8 plane.
// Endpoints preserve every source byte. New frames retain the original cycle duration.
inline void interpolate565a8_original(uint8_t *out, const uint8_t *a, const uint8_t *b, size_t pixels, unsigned weight) {
  if (!weight) { std::memcpy(out, a, pixels * 3); return; }
  if (weight == 256) { std::memcpy(out, b, pixels * 3); return; }
  const unsigned inverse = 256 - weight;
  for (size_t i = 0; i < pixels; ++i) {
    const uint16_t x = uint16_t(a[i * 2]) | (uint16_t(a[i * 2 + 1]) << 8);
    const uint16_t y = uint16_t(b[i * 2]) | (uint16_t(b[i * 2 + 1]) << 8);
    const auto mix = [inverse, weight](unsigned x, unsigned y) { return (x * inverse + y * weight + 128) >> 8; };
    const uint16_t color = uint16_t((mix(x >> 11, y >> 11) << 11) |
                                  (mix((x >> 5) & 63, (y >> 5) & 63) << 5) | mix(x & 31, y & 31));
    out[i * 2] = uint8_t(color); out[i * 2 + 1] = uint8_t(color >> 8);
    out[pixels * 2 + i] = uint8_t(mix(a[pixels * 2 + i], b[pixels * 2 + i]));
  }
}

inline uint16_t interpolate_color(uint32_t x, uint32_t y, unsigned weight) {
  const unsigned inverse = 256 - weight;
  // Independent 16-bit lanes keep red/blue rounding exact while sharing multiplies.
  const uint32_t rb_x = ((x & 0xf800) << 5) | (x & 31), rb_y = ((y & 0xf800) << 5) | (y & 31);
  const uint32_t rb = ((rb_x * inverse + rb_y * weight + 0x00800080) >> 8) & 0x001f001f;
  const uint32_t green = (((x & 0x7e0) * inverse + (y & 0x7e0) * weight + 0x1000) >> 8) & 0x7e0;
  return uint16_t(((rb >> 5) & 0xf800) | green | (rb & 31));
}
// Only call these after checking every address. memcpy preserves C++ aliasing;
// known alignment lets the P4 compiler emit lw/sw instead of byte/stack copies.
inline uint32_t aligned_word(const uint8_t *p) {
  uint32_t value; std::memcpy(&value, __builtin_assume_aligned(p, 4), 4); return value;
}
inline void aligned_word(uint8_t *p, uint32_t value) { std::memcpy(__builtin_assume_aligned(p, 4), &value, 4); }
inline bool word_aligned(const uint8_t *out, const uint8_t *a, const uint8_t *b) {
  return !((reinterpret_cast<uintptr_t>(out) | reinterpret_cast<uintptr_t>(a) | reinterpret_cast<uintptr_t>(b)) & 3);
}
inline uint32_t interpolate_alpha_word(uint32_t x, uint32_t y, unsigned weight) {
  const uint32_t low = (((x & 0x00ff00ff) * (256 - weight) + (y & 0x00ff00ff) * weight +
                         0x00800080) >> 8) & 0x00ff00ff;
  const uint32_t high = (((x >> 8) & 0x00ff00ff) * (256 - weight) +
                          ((y >> 8) & 0x00ff00ff) * weight + 0x00800080) & 0xff00ff00;
  return low | high;
}
inline void interpolate_colors(uint8_t *out, const uint8_t *a, const uint8_t *b, size_t pixels, unsigned weight) {
  size_t i = 0;
  if (word_aligned(out, a, b)) for (; i + 1 < pixels; i += 2) {
    const uint32_t x = aligned_word(a + i * 2), y = aligned_word(b + i * 2);
    const uint32_t result = x == y ? x : uint32_t(interpolate_color(x & 65535, y & 65535, weight)) |
        (uint32_t(interpolate_color(x >> 16, y >> 16, weight)) << 16);
    aligned_word(out + i * 2, result);
  }
  for (; i < pixels; ++i) {
    const uint32_t x = uint32_t(a[i * 2]) | (uint32_t(a[i * 2 + 1]) << 8);
    const uint32_t y = uint32_t(b[i * 2]) | (uint32_t(b[i * 2 + 1]) << 8);
    const uint16_t color = interpolate_color(x, y, weight);
    out[i * 2] = uint8_t(color); out[i * 2 + 1] = uint8_t(color >> 8);
  }
}
inline void interpolate_alpha(uint8_t *out, const uint8_t *a, const uint8_t *b, size_t bytes, unsigned weight) {
  size_t i = 0;
  if (word_aligned(out, a, b)) for (; i + 3 < bytes; i += 4) {
    const uint32_t x = aligned_word(a + i), y = aligned_word(b + i);
    uint32_t result = x;
    if (x != y) result = interpolate_alpha_word(x, y, weight);
    aligned_word(out + i, result);
  }
  for (; i < bytes; ++i) out[i] = uint8_t((a[i] * (256 - weight) + b[i] * weight + 128) >> 8);
}
inline void interpolate565a8(uint8_t *out, const uint8_t *a, const uint8_t *b, size_t pixels, unsigned weight) {
  if (!weight) { std::memcpy(out, a, pixels * 3); return; }
  if (weight == 256) { std::memcpy(out, b, pixels * 3); return; }
  interpolate_colors(out, a, b, pixels, weight);
  interpolate_alpha(out + pixels * 2, a + pixels * 2, b + pixels * 2, pixels, weight);
}
struct ChangedWord { uint32_t at{}, second{}; };
constexpr size_t SPARSE_CAPACITY = 16384, SPARSE_BYTES = SPARSE_CAPACITY * sizeof(ChangedWord);
struct SparseMix {
  ChangedWord *words{};
  size_t capacity{}, count{}, cached_pixels{};
  uint32_t first{UINT32_MAX}, second{UINT32_MAX};
  bool valid{};
  void blend(uint8_t *out, const uint8_t *a, const uint8_t *b, size_t pixels, unsigned weight,
             uint32_t a_index, uint32_t b_index) {
    // Generic odd sizes or unaligned buffers keep the exact linear implementation.
    if (!words || (pixels & 3) || !word_aligned(out, a, b)) {
      interpolate565a8(out, a, b, pixels, weight); return;
    }
    if (!weight || weight == 256) { std::memcpy(out, weight ? b : a, pixels * 3); return; }
    if (!valid || first != a_index || second != b_index || cached_pixels != pixels) {
      valid = false; count = 0;
      for (size_t at = 0; at < pixels * 3; at += 4) {
        const uint32_t x = aligned_word(a + at), y = aligned_word(b + at);
        if (x == y) continue;
        if (count == capacity) { interpolate565a8(out, a, b, pixels, weight); return; }
        words[count++] = {uint32_t(at), y};
      }
      first = a_index; second = b_index; cached_pixels = pixels; valid = true;
    }
    std::memcpy(out, a, pixels * 3);
    for (size_t i = 0; i < count; ++i) {
      const auto change = words[i];
      const uint32_t x = aligned_word(out + change.at);
      const uint32_t value = change.at < pixels * 2 ?
          uint32_t(interpolate_color(x & 65535, change.second & 65535, weight)) |
            (uint32_t(interpolate_color(x >> 16, change.second >> 16, weight)) << 16) :
          interpolate_alpha_word(x, change.second, weight);
      aligned_word(out + change.at, value);
    }
  }
};
constexpr size_t INTERPOLATION_CHUNK = 2048, INTERPOLATION_SCRATCH = 3 * INTERPOLATION_CHUNK;
inline void interpolate565a8_staged(uint8_t *out, const uint8_t *a, const uint8_t *b, size_t pixels,
                                   unsigned weight, uint8_t *scratch) {
  auto *sa = scratch, *sb = scratch + INTERPOLATION_CHUNK, *so = scratch + 2 * INTERPOLATION_CHUNK;
  for (size_t at = 0; at < pixels;) {
    const size_t count = std::min(pixels - at, INTERPOLATION_CHUNK / 2);
    std::memcpy(sa, a + at * 2, count * 2); std::memcpy(sb, b + at * 2, count * 2);
    interpolate_colors(so, sa, sb, count, weight);
    std::memcpy(out + at * 2, so, count * 2); at += count;
  }
  for (size_t at = 0; at < pixels;) {
    const size_t count = std::min(pixels - at, INTERPOLATION_CHUNK);
    std::memcpy(sa, a + pixels * 2 + at, count); std::memcpy(sb, b + pixels * 2 + at, count);
    interpolate_alpha(so, sa, sb, count, weight);
    std::memcpy(out + pixels * 2 + at, so, count); at += count;
  }
}
}  // namespace packed_orb
