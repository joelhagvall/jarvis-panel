#pragma once
#include <cerrno>
#include <string_view>

namespace panel_debug_wire {
template<class Send>
bool send_all(std::string_view wire, Send send) {
  size_t offset = 0;
  while (offset < wire.size()) {
    const auto count = send(wire.data() + offset, wire.size() - offset);
    if (count < 0 && errno == EINTR) continue;
    if (count <= 0 || size_t(count) > wire.size() - offset) return false;
    offset += size_t(count);
  }
  return true;
}
}  // namespace panel_debug_wire
