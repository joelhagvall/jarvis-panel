#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace panel_debug_copy {
// A bounded internal-RAM staging buffer separates external-memory reads and writes.
// Source and destination must be disjoint, as required by memcpy and LVGL's image path.
inline void staged_copy(void *destination, const void *source, size_t bytes, void *scratch, size_t capacity) {
  auto *out = static_cast<uint8_t *>(destination);
  const auto *in = static_cast<const uint8_t *>(source);
  if (!scratch || !capacity) { std::memcpy(out, in, bytes); return; }
  while (bytes) {
    const size_t n = std::min(bytes, capacity);
    std::memcpy(scratch, in, n);
    std::memcpy(out, scratch, n);
    out += n; in += n; bytes -= n;
  }
}
}  // namespace panel_debug_copy
