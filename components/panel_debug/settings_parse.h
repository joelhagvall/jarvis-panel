#pragma once
// The settings an agent may read and set over HTTP, and the only values it may give them. Pure C++.
#include <cstdint>
#include <string_view>

namespace panel_debug_settings {
enum class Key { NONE, DAY_VOLUME, BRIGHTNESS, NIGHT_LIGHT, NIGHT_START, NIGHT_END };

struct Value {
  Key key{Key::NONE};
  int a{}, b{};  // percent in a; or hour in a and minute in b
};

inline bool digits(std::string_view text, int &value, int low, int high) {
  if (text.empty() || text.size() > 3) return false;
  value = 0;
  for (char c : text) {
    if (c < '0' || c > '9') return false;
    value = value * 10 + (c - '0');
  }
  return value >= low && value <= high;
}

// Volume 0–100 % (the day preference, capped at night like the slider); brightness and night light 1–100 %
// (0 would leave a black screen); night times "HH:MM".
inline Value parse(std::string_view key, std::string_view value) {
  Value out;
  const bool time = key == "night_start" || key == "night_end";
  if (time) {
    if (value.size() != 5 || value[2] != ':' || !digits(value.substr(0, 2), out.a, 0, 23) ||
        !digits(value.substr(3, 2), out.b, 0, 59)) return {};
    out.key = key == "night_start" ? Key::NIGHT_START : Key::NIGHT_END;
    return out;
  }
  if (key == "day_volume") out.key = Key::DAY_VOLUME;
  else if (key == "brightness") out.key = Key::BRIGHTNESS;
  else if (key == "night_light") out.key = Key::NIGHT_LIGHT;
  else return {};
  if (!digits(value, out.a, out.key == Key::DAY_VOLUME ? 0 : 1, 100)) return {};
  return out;
}
}  // namespace panel_debug_settings
