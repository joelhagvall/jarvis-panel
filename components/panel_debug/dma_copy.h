#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace panel_debug_copy {
enum class CopyResult { UNSUPPORTED, COMPLETE, FAILED };
// The destination and length follow the DMA write burst (IDF's RX alignment); the
// source follows its own, looser requirement. Both must be powers of two.
template<class SubmitAndWait>
CopyResult dma_chunks(void *destination, const void *source, size_t bytes, SubmitAndWait submit,
                      size_t source_alignment = 16, size_t burst = 16) {
  if (!source_alignment || (source_alignment & (source_alignment - 1)) || !burst || (burst & (burst - 1)) ||
      burst > 256 * 1024) return CopyResult::UNSUPPORTED;
  const size_t destination_alignment = std::max(burst, source_alignment);
  if (!destination || !source || bytes < 4096 ||
      ((reinterpret_cast<uintptr_t>(destination) | bytes) & (destination_alignment - 1)) ||
      (reinterpret_cast<uintptr_t>(source) & (source_alignment - 1))) return CopyResult::UNSUPPORTED;
  auto *out = static_cast<uint8_t *>(destination);
  const auto *in = static_cast<const uint8_t *>(source);
  while (bytes) {
    const size_t chunk = std::min(bytes, size_t(256 * 1024));
    if (!submit(out, in, chunk)) return CopyResult::FAILED;
    out += chunk; in += chunk; bytes -= chunk;
  }
  return CopyResult::COMPLETE;
}
}  // namespace panel_debug_copy
