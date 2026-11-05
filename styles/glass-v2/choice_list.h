#pragma once
// Inställningar's lists (Bakgrund, Ljud ut): the choices Home Assistant offers and the current one.
// Home Assistant sends the choices as one string, one per line (the select's panel_options attribute),
// which is also the form LVGL's dropdown takes. At most MAX: the panel has one pick script per entry.
#include <cstddef>
#include <string>
#include <vector>

namespace choice_list {
constexpr std::size_t MAX = 8;
struct List {
  std::vector<std::string> names;
  std::string current;
  // True when the choices changed, so the dropdown needs its options again.
  bool options(const std::string &joined) {
    std::vector<std::string> next;
    std::size_t start = 0;
    while (start <= joined.size() && next.size() < MAX) {
      auto end = joined.find('\n', start);
      if (end == std::string::npos) end = joined.size();
      if (end > start) next.push_back(joined.substr(start, end - start));
      start = end + 1;
    }
    if (next == names) return false;
    names = std::move(next);
    return true;
  }
  std::string joined() const {
    std::string out;
    for (const auto &name : names) out += (out.empty() ? "" : "\n") + name;
    return out.empty() ? "–" : out;
  }
  int index() const {
    for (std::size_t i = 0; i < names.size(); ++i)
      if (names[i] == current) return int(i);
    return -1;
  }
};
inline List bg, out;
}  // namespace choice_list
