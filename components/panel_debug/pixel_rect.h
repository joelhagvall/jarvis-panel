#pragma once
#include <cstddef>
#include <cstdint>

namespace panel_debug_copy {
struct Rect { int32_t x{}, y{}, width{}, height{}; };
inline bool contains(int32_t width, int32_t height, const Rect &r) {
  return width > 0 && height > 0 && r.x >= 0 && r.y >= 0 && r.width > 0 && r.height > 0 &&
         int64_t(r.x) + r.width <= width && int64_t(r.y) + r.height <= height;
}
inline bool valid_rect_copy(int32_t dw, int32_t dh, size_t destination_size, const Rect &to,
                            int32_t sw, int32_t sh, const Rect &from) {
  return contains(dw, dh, to) && contains(sw, sh, from) && to.width == from.width &&
         to.height == from.height && uint64_t(dw) * dh * 2 <= destination_size;
}
}  // namespace panel_debug_copy
