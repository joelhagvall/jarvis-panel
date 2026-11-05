#pragma once
// The dashboard weather card's days and the weather page (weather_page.py; server/agents/weather.py panel()).
// Pure helpers, no LVGL: the generated lambdas do the drawing. Rows come as "a|b|c;d|e|f".
#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <string>
#include <vector>

namespace weather_view {

using Row = std::vector<std::string>;

inline std::vector<Row> table(const std::string &text) {
  std::vector<Row> rows;
  if (text.empty()) return rows;
  size_t start = 0;
  while (start <= text.size()) {
    const size_t end = std::min(text.find(';', start), text.size());
    Row row;
    size_t at = start;
    while (true) {
      const size_t bar = text.find('|', at);
      if (bar == std::string::npos || bar >= end) { row.push_back(text.substr(at, end - at)); break; }
      row.push_back(text.substr(at, bar - at));
      at = bar + 1;
    }
    rows.push_back(row);
    start = end + 1;
  }
  return rows;
}

// Material Design Icons (gen.py's MDI font) by server/agents/weather.py's icon names; a cloud for anything else.
inline const char *glyph(const std::string &name) {
  if (name == "sun") return "\U000F0599";
  if (name == "moon") return "\U000F0594";
  if (name == "partly") return "\U000F0595";
  if (name == "partly_night") return "\U000F0F31";
  if (name == "fog") return "\U000F0591";
  if (name == "rain") return "\U000F0597";
  if (name == "pour") return "\U000F0596";
  if (name == "thunder") return "\U000F067E";
  if (name == "sleet") return "\U000F067F";
  if (name == "snow") return "\U000F0598";
  return "\U000F0590";
}

// The page's 24-hour chart (weather_page.py CHART): hour i of 24 across, a temperature between lo and hi down.
struct Chart { int left, right, top, bottom; };
constexpr Chart CHART{48, 976, 196, 300};
constexpr int HOURS = 24, BAR_BASE = 380, BAR_MAX = 60;

inline int x_at(int i) { return CHART.left + (CHART.right - CHART.left) * i / (HOURS - 1); }

inline void range(const std::vector<float> &temps, float &lo, float &hi) {
  lo = temps.empty() ? 0 : *std::min_element(temps.begin(), temps.end()) - 1;
  hi = temps.empty() ? 1 : *std::max_element(temps.begin(), temps.end()) + 1;
}

inline int y_at(float t, float lo, float hi) {
  const float share = hi > lo ? (t - lo) / (hi - lo) : 0.5f;
  return CHART.bottom - int(std::lround(share * (CHART.bottom - CHART.top)));
}

// mm in that hour as a bar: 30 px a millimetre, at most BAR_MAX; nothing below 0.05 mm.
inline int bar_h(float mm) { return mm < 0.05f ? 0 : std::min(BAR_MAX, std::max(2, int(std::lround(mm * 30)))); }

inline std::string deg(float t) { return std::to_string(int(std::lround(t))) + "°"; }

}  // namespace weather_view
