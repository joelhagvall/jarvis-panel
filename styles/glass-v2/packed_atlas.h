#pragma once
#include <cstddef>
#include <cstdint>
#include <cstring>

namespace packed_orb {
constexpr size_t SIDE = 220, FRAME_BYTES = SIDE * SIDE * 3, MAX_BYTES = 8 * 1024 * 1024;
inline uint32_t read32(const uint8_t *p) {
  return uint32_t(p[0]) | (uint32_t(p[1]) << 8) | (uint32_t(p[2]) << 16) | (uint32_t(p[3]) << 24);
}
class Atlas {
 public:
  bool open(const uint8_t *data, size_t size, size_t expected) {
    data_ = nullptr;
    count_ = 0;
    if (!data || size < 16 || size > MAX_BYTES || std::memcmp(data, "HPA3", 4) ||
        (data[4] | (uint32_t(data[5]) << 8)) != SIDE || read32(data + 8) != size) return false;
    const size_t count = data[6] | (size_t(data[7]) << 8);
    if (!count || count != expected || count > (size - 16) / 4) return false;
    const size_t table_end = 12 + 4 * (count + 1);
    if (read32(data + 12) != table_end || read32(data + 12 + count * 4) != size) return false;
    for (size_t i = 0; i < count; ++i) {
      const size_t begin = read32(data + 12 + i * 4), end = read32(data + 16 + i * 4);
      if (begin < table_end || begin > size || end > size || end < begin || (begin & 3) || (end & 3) ||
          end - begin < 16) return false;
      const uint32_t compressed = read32(data + begin + 4);
      if (read32(data + begin) != 2 || read32(data + begin + 8) != FRAME_BYTES || !compressed ||
          compressed > end - begin - 12 || end - begin - 12 - compressed > 3) return false;
      for (size_t p = begin + 12 + compressed; p < end; ++p) if (data[p]) return false;
    }
    data_ = data;
    count_ = count;
    return true;
  }
  size_t count() const { return count_; }
  const uint8_t *frame(size_t i) const {
    return data_ && i < count_ ? data_ + read32(data_ + 12 + i * 4) : nullptr;
  }
  size_t frame_size(size_t i) const { auto *p = frame(i); return p ? 12 + read32(p + 4) : 0; }
 private:
  const uint8_t *data_{nullptr};
  size_t count_{0};
};
}  // namespace packed_orb
