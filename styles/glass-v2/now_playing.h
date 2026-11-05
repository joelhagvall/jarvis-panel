#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <cstdio>
#include <string>

namespace panel_music {
struct State {
  std::string state{"idle"}, source, title, artist;
  std::string place;              // the Bluetooth speaker it plays on ("JBL Charge 4"), or "" when unknown
  bool next{false}, previous{false};
  // The music plays elsewhere (Jarvis's Bluetooth speaker): the panel's own player going idle must not hide it.
  bool remote{false};
  bool active() const { return state == "playing" || state == "paused"; }
  std::string caption() const {
    std::string value = source;
    if (!place.empty()) value += " på " + place;
    if (state == "paused") value += " · Pausat";
    if (!artist.empty()) value += " · " + artist;
    else if (state != "paused") value += " · Spelas nu";
    return value;
  }
  // The row's second line under the title (Joel's choice, 2026-10-28, as in Spotify's own mini player): the artist
  // first, then where it plays: "Coldplay · Spotify på JBL Charge 4", "Sveriges Radio · Radio · Pausat".
  std::string byline() const {
    std::string value = artist.empty() ? source : artist + " · " + source;
    if (!place.empty()) value += " på " + place;
    if (state == "paused") value += " · Pausat";
    return value;
  }
  void clear() { *this = State{}; }
  bool wire(const std::string &value) {
    if (value.size() > 1024) return false;
    // Six fields, or seven when the seventh says where it plays: the speaker's name, "1" (not on the panel,
    // name unknown) or "0" (the panel).
    std::array<std::string, 7> fields;
    size_t start = 0, count = 0;
    while (count < fields.size()) {
      const auto end = value.find('|', start);
      fields[count++] = value.substr(start, end == std::string::npos ? end : end - start);
      if (end == std::string::npos) break;
      start = end + 1;
      if (count == fields.size()) return false;
    }
    if (count < 6) return false;
    if (fields[0] != "idle" && fields[0] != "playing" && fields[0] != "paused") return false;
    if ((fields[4] != "0" && fields[4] != "1") || (fields[5] != "0" && fields[5] != "1")) return false;
    if (count == 7 && (fields[6].empty() || fields[6].size() > 64)) return false;
    state = fields[0]; source = fields[1]; title = fields[2]; artist = fields[3];
    next = fields[4] == "1"; previous = fields[5] == "1";
    remote = count == 7 && fields[6] != "0";
    place = remote && fields[6] != "1" ? fields[6] : "";
    return true;
  }
  void native(const std::string &value) {
    if (value == "idle" || value == "off") {
      if (!remote) clear();
    } else if (value == "playing" || value == "paused") {
      remote = false;               // the panel plays itself again; Home Assistant's wire follows
      place.clear();
      state = value;
      if (title.empty()) title = "Musik på hallpanelen";
      if (source.empty()) source = "Musik";
    }
    // Announcements never create a music session and do not hide ongoing music.
  }
};
inline State state;
struct Pending {
  enum class Action { NONE, TOGGLE, STOP, NEXT, PREVIOUS };
  enum class Result { NONE, WAITING, DONE, FAILED, TIMEOUT };
  bool waiting{false};
  uint32_t started{0};
  uint32_t ticket{0}, ended{0}, position_before{0};
  Action action{Action::NONE};
  Result result{Result::NONE};
  State before;
  bool accepted{false}, changed{false};
  void start(uint32_t now) { waiting = true; started = now; result = Result::WAITING; }
  void finish() { waiting = false; }
  bool begin(Action which, const State &state, uint32_t position, uint32_t now) {
    if (waiting && which != Action::STOP) return false;
    if (++ticket == 0) ++ticket;
    action = which; before = state; position_before = position;
    accepted = changed = false; start(now); return true;
  }
  bool current(uint32_t id) const { return waiting && ticket == id; }
  bool complete(uint32_t now) {
    if (!waiting || !accepted || !changed) return false;
    waiting = false; result = Result::DONE; ended = now; return true;
  }
  bool acknowledge(uint32_t id, uint32_t now) {
    if (!current(id)) return false;
    accepted = true; return complete(now);
  }
  bool observe(const State &state, uint32_t position, uint32_t now) {
    if (!waiting) return false;
    if (action == Action::TOGGLE)
      changed = state.state == (before.state == "paused" ? "playing" : "paused");
    else if (action == Action::STOP) changed = !state.active();
    else if (action == Action::NEXT || action == Action::PREVIOUS)
      changed = state.active() && (state.title != before.title || state.artist != before.artist ||
                state.source != before.source || (position_before > 1500 && position < position_before - 1500));
    return complete(now);
  }
  bool fail(uint32_t id, uint32_t now) {
    if (!current(id)) return false;
    waiting = false; result = Result::FAILED; ended = now; return true;
  }
  bool expire(uint32_t now) {
    if (waiting && uint32_t(now - started) >= 8000) {
      waiting = false; result = Result::TIMEOUT; ended = now;
      return true;
    }
    return false;
  }
  bool tick(uint32_t now) {
    if (expire(now)) return true;
    if (!waiting && result != Result::NONE && uint32_t(now - ended) >=
        (result == Result::DONE ? 1200u : 4000u)) {
      result = Result::NONE; return true;
    }
    return false;
  }
  const char *message() const {
    if (result == Result::FAILED) return "Kunde inte styra musiken. Försök igen.";
    if (result == Result::TIMEOUT) return "Ingen bekräftelse från spelaren. Försök igen.";
    if (result == Result::NONE) return "";
    if (result == Result::DONE) {
      if (action == Action::TOGGLE) return before.state == "paused" ? "Spelar" : "Pausat";
      if (action == Action::STOP) return "Stoppat";
      return "Låt bytt";
    }
    if (action == Action::TOGGLE) return before.state == "paused" ? "Startar …" : "Pausar …";
    if (action == Action::STOP) return "Stoppar …";
    return action == Action::PREVIOUS ? "Föregående låt …" : "Byter låt …";
  }
};
inline Pending pending;
struct Artwork {
  std::string wanted, loading, attempted, completed;
  // Some cover of this source is in: a new song's cover replaces it when it arrives, instead of the row showing no
  // cover meanwhile (the thumbnail blinked at every song, 2026-10-28). Another source or a stop clears it.
  bool any{false};
  uint32_t finished{0};               // covers downloaded since boot: a new song's comes after its text (Hold)
  // The key Home Assistant last sent. HA sends a key only when it changes, so after the panel dropped its cover
  // (its own player went idle while the radio moved back to the speaker, 2026-10-27) this brings it back.
  std::string offered;
  bool busy{false};
  bool select(const std::string &key) {
    if (!key.empty() && (key.size() != 32 || key.find_first_not_of("0123456789abcdef") != std::string::npos)) return false;
    if (key != wanted) { wanted = key; completed.clear(); attempted.clear(); }
    if (key.empty()) any = false;          // stopped: the next music starts without an old cover
    return true;
  }
  bool offer(const std::string &key) {
    if (!select(key)) return false;
    offered = key;
    return true;
  }
  void restore() { if (wanted.empty()) select(offered); }
  void forget() { select(""); offered.clear(); any = false; }
  bool begin() {
    if (busy || wanted.empty() || attempted == wanted) return false;
    loading = attempted = wanted; busy = true; return true;
  }
  void finish(bool ok) {
    busy = false;
    completed = ok && loading == wanted ? loading : "";
    if (!completed.empty()) { any = true; finished++; }
  }
  bool ready() const { return !wanted.empty() && completed == wanted; }
  bool shown() const { return !wanted.empty() && any; }   // a cover to show: this song's, or until it is in the last
};
inline Artwork artwork;
// Where the song is, for the Spelas nu page's time line: Home Assistant's "position ms|duration ms|1 while
// playing", and the panel's clock when it came, so the line moves on by itself between updates.
struct Progress {
  uint32_t position_ms{0}, duration_ms{0}, received_ms{0};
  bool running{false};
  bool wire(const std::string &value, uint32_t now) {
    unsigned long position = 0, duration = 0, run = 0;
    if (std::sscanf(value.c_str(), "%lu|%lu|%lu", &position, &duration, &run) != 3 || duration > 24ul * 3600 * 1000) {
      duration_ms = 0;
      return false;
    }
    position_ms = position > duration ? duration : position;
    duration_ms = duration;
    running = run == 1;
    received_ms = now;
    return true;
  }
  uint32_t at(uint32_t now) const {
    const uint64_t moved = running ? uint64_t(now - received_ms) : 0;
    return uint32_t(std::min<uint64_t>(position_ms + moved, duration_ms));
  }
  static std::string clock(uint32_t ms) {
    char text[16];
    const uint32_t s = ms / 1000;
    std::snprintf(text, sizeof text, "%u:%02u", unsigned(s / 60), unsigned(s % 60));
    return text;
  }
};
inline Progress progress;
// The album and its year from Home Assistant ("Ghost Stories (2014)"), for the Musik page; empty for radio.
inline std::string album;

// A new song on Spotify shows all at once (Joel, 2026-10-28): its text came at once and its cover one to two
// seconds later. Hold keeps what was shown (the song, its album, its time line) until the new cover is in and the
// record shows it, or HOLD_MS at most; another source, a stop or radio shows at once.
struct Hold {
  static constexpr uint32_t HOLD_MS = 4000;
  bool on{false};
  uint32_t since{0};
  uint32_t downloads_before{0};       // Artwork::finished when the song changed: the new song's cover comes after
                                      // (HA's cover key and song text are separate attributes, in either order)
  State shown;
  std::string shown_album;
  Progress shown_progress;
  void start(const State &before, const std::string &album_before, const Progress &progress_before,
             uint32_t downloads, uint32_t now) {
    if (!on) { shown = before; shown_album = album_before; shown_progress = progress_before; }
    on = true; since = now; downloads_before = downloads;
  }
  // True when it lets go now: the new cover is in (and the record turns it), or it waited long enough.
  bool release(bool cover_in, uint32_t now) {
    if (on && (cover_in || now - since >= HOLD_MS)) { on = false; return true; }
    return false;
  }
};
inline Hold hold;
// Spotify starting with no cover on screen yet (the first music after boot or a stop): hold the row back until
// its cover is in, so title and cover appear together (Joel, 2026-10-29: after boot the cover came after the title).
inline bool hold_first_cover(const State &before, const State &now, const Artwork &art) {
  return now.active() && !before.active() && now.source == "Spotify" && !art.any;
}
// A held song lets go as soon as its cover is downloaded: title and row cover change together (Joel, 2026-10-29:
// waiting for the Spelas nu record too took about 4 s after next). The record follows when its new atlas turns.
inline bool cover_in(const Artwork &art, uint32_t downloads_before) {
  return art.finished != downloads_before && art.ready();
}
// Another source drops the old cover. The first music after boot or a stop has no old source: its cover key may
// have come before its text (HA's attributes come in either order), and dropping it left the row without a cover
// and the record black until the next song (Joel, 2026-10-29).
inline bool source_changed(const std::string &before, const std::string &now) {
  return !before.empty() && before != now;
}
// Spelas nu's speaker icons (Joel, 2026-10-29): one 5 % stop quieter or louder, from any value (52 -> 50 or 55).
inline int volume_step(int value, int delta) {
  if (value < 0) value = 0;
  if (value > 100) value = 100;
  return delta < 0 ? (value <= 0 ? 0 : ((value - 1) / 5) * 5) : (value / 5 + 1) * 5 > 100 ? 100 : (value / 5 + 1) * 5;
}
// What the panel shows: the held song while a new one's cover is on its way, else the music as it is.
inline const State &view() { return hold.on ? hold.shown : state; }
inline const std::string &view_album() { return hold.on ? hold.shown_album : album; }
inline const Progress &view_progress() { return hold.on ? hold.shown_progress : progress; }
}  // namespace panel_music
