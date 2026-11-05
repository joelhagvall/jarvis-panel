#pragma once
// The Spelas nu page's radio (radio_cabinet.py draws its cabinet): the needle stands on the station's FM frequency
// in Stockholm and glides there when the station changes (Joel's choice B1, 2026-10-28). The panel plays Sveriges
// Radio's web streams, so the frequency is the familiar one, not where the sound comes from. Stations without one
// show their name and the needle rests in the middle.
#include <cstdio>
#include <cstring>
#include <string>

namespace panel_radio {
constexpr int DIAL_X0 = 186, DIAL_X1 = 338;      // radio_cabinet.py's DIAL, inside the cabinet
constexpr float LOW = 88.0f, HIGH = 108.0f;

struct Station {
  const char *prefix;
  float mhz;
};
// Sveriges Radio in Stockholm; the panel names a station by its channel ("P4 Stockholm" starts with "P4").
constexpr Station STATIONS[] = {{"P1", 92.4f}, {"P2", 96.2f}, {"P3", 99.3f}, {"P4", 103.3f}, {"Din Gata", 100.6f}};

inline float mhz(const std::string &station) {
  for (const auto &s : STATIONS)
    if (station.compare(0, std::strlen(s.prefix), s.prefix) == 0) return s.mhz;
  return 0;
}

// The needle's x inside the cabinet, for a frequency (the middle without one).
inline int needle_x(float frequency) {
  if (frequency <= 0) frequency = (LOW + HIGH) / 2;
  return DIAL_X0 + 12 + int((DIAL_X1 - DIAL_X0 - 24) * (frequency - LOW) / (HIGH - LOW) + 0.5f) - 1;
}

// "FM 99,3", or the station's own name when it has no frequency.
inline std::string label(const std::string &station) {
  const float f = mhz(station);
  if (f <= 0) return station;
  char text[16];
  std::snprintf(text, sizeof text, "FM %d,%d", int(f), int(f * 10 + 0.5f) % 10);
  return text;
}
}  // namespace panel_radio
