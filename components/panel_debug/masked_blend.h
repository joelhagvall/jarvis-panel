#pragma once
#include <cstddef>
#include <cstdint>

namespace panel_debug_copy {
struct MaskStats { uint32_t pixels{}, skipped{}, opaque{}; };
// LVGL 9.4 RGB565 mixing quantizes (alpha+4)>>3. Alpha <=3 preserves the
// background exactly; alpha >=252 selects the foreground exactly. Native tests
// check these thresholds against LVGL itself, not a duplicated mixing formula.
template<bool Constant, class Mix>
MaskStats masked_rows(uint8_t *destination, size_t destination_stride, const uint8_t *source,
                     size_t source_stride, uint16_t color, const uint8_t *mask,
                     size_t mask_stride, size_t width, size_t height, Mix mix) {
  MaskStats stats{};
  for (size_t y = 0; y < height; ++y) {
    auto *out = reinterpret_cast<uint16_t *>(destination + y * destination_stride);
    const uint16_t *in = nullptr;
    if constexpr (!Constant) in = reinterpret_cast<const uint16_t *>(source + y * source_stride);
    const auto *alpha = mask + y * mask_stride;
    for (size_t x = 0; x < width; ++x) {
      const uint8_t amount = alpha[x];
      ++stats.pixels;
      if (amount <= 3) { ++stats.skipped; continue; }
      const uint16_t foreground = Constant ? color : in[x];
      if (amount >= 252) { out[x] = foreground; ++stats.opaque; }
      else out[x] = mix(foreground, out[x], amount);
    }
  }
  return stats;
}
}  // namespace panel_debug_copy
