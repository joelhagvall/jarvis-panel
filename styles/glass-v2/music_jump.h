#pragma once
// Spelas nu after Jarvis starts music (Joel, 2026-10-30): "spela X på Spotify" or "sätt på P3" ends on the Spelas nu
// page, the record turning for music and the walnut radio for a station (the page picks by source itself).
// Armed when the panel starts listening, with what plays then; open from then until 20 s after the voice turn.
// Music that appears or changes in that window is taken as his: a new Spotify track, or another station (a
// station's song changes do not count). A Spotify track that simply ran out does not count either. Wakeups and
// other announcements never listen, so they never arm it. Board wiring in now_playing.board().
#include <cstdint>
#include <string>

namespace panel_music_jump {

constexpr uint32_t TURN_MS = 120000, AFTER_MS = 20000, NATURAL_END_MS = 5000;

inline bool armed = false, pending = false;
inline uint32_t until = 0, track_end = 0;   // millis(); track_end 0 when the playing track's end is unknown
inline std::string seen;

inline std::string identity(const std::string &state, const std::string &source, const std::string &title) {
  if (state != "playing") return {};
  return source == "Spotify" || source == "Musik" ? source + "\n" + title : source;
}

inline bool open(uint32_t now) {
  if (armed && int32_t(now - until) >= 0) armed = false;
  return armed;
}

// remaining_ms: what is left of the playing Spotify track, 0 when unknown.
inline void arm(uint32_t now, const std::string &id, uint32_t remaining_ms) {
  if (open(now)) return;   // a follow-up question keeps the first snapshot
  armed = true;
  pending = false;
  seen = id;
  until = now + TURN_MS;
  track_end = remaining_ms ? now + remaining_ms : 0;
}

// The voice turn is over: the music may still take a few seconds to start.
inline void settle(uint32_t now) {
  if (armed) until = now + AFTER_MS;
}

inline bool started(uint32_t now, const std::string &id) {
  if (!open(now) || id.empty() || id == seen) return false;
  const bool spotify_before = seen.rfind("Spotify\n", 0) == 0, spotify_now = id.rfind("Spotify\n", 0) == 0;
  if (spotify_before && spotify_now && track_end && int32_t(now - (track_end - NATURAL_END_MS)) >= 0) {
    seen = id;   // the next song on its own; a later "spela X" still counts
    track_end = 0;
    return false;
  }
  armed = false;
  return true;
}

inline bool take() {
  const bool was = pending;
  pending = false;
  return was;
}

}  // namespace panel_music_jump
