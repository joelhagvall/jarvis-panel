#pragma once
#include <cstdint>
// Spelas nu's sleeve (Joel, 2026-10-29): a tap on the turning record shows its cover as a sleeve with the record half
// out of it; a tap on the sleeve brings the turntable back. Only a cover can be a sleeve: radio, nothing playing
// or a cover still on its way show the turntable/radio as before, and the tap goes back as anywhere else.
// Leaving the page puts the turntable back, so Spelas nu always opens as before.
namespace panel_sleeve {
struct Sleeve {
  static constexpr int CX = 216, CY = 254, R = 144;             // the turntable's record (now_playing.py DISC_XY)
  static constexpr int X0 = 29, Y0 = 110, X1 = 436, Y1 = 398;   // the sleeve and the record's strip (sleeve.py)
  // A song change on Spotify drops the cover for about a second (1.3 s, 2026-10-29): the turntable blinked in
  // between. The sleeve waits this long for a cover, as Hold waits for a new song's; then the turntable shows.
  static constexpr uint32_t GRACE_MS = 4000;
  bool on{};          // the sleeve is chosen instead of the turntable
  bool available{};   // a tap may swap: the sleeve or a cover's record shows (music_render sets it)
  bool waiting{};     // shown without a cover, until GRACE_MS after the last one
  uint32_t seen{};    // when music_render last had a cover

  bool hit(int x, int y) const {
    if (on) return x >= X0 && x < X1 && y >= Y0 && y < Y1;
    const int dx = x - CX, dy = y - CY;
    return dx * dx + dy * dy <= R * R;
  }
  // A tap on the page at (x, y): true when it swapped the record and the sleeve, false when it is a tap to go back.
  bool tap(int x, int y) {
    if (!available || !hit(x, y)) return false;
    on = !on;
    waiting = false;
    return true;
  }
  // What music_render shows: the sleeve while there is a cover, or for GRACE_MS after the last one. The choice
  // itself stays until a tap or leaving the page.
  bool shown(bool cover, uint32_t now) {
    if (cover) seen = now;
    waiting = on && !cover && now - seen < GRACE_MS;
    const bool show = on && (cover || waiting);
    available = cover || show;
    return show;
  }
  // True once the wait ran out with the sleeve still up: music_render then shows the turntable.
  bool lapsed(uint32_t now) const { return waiting && now - seen >= GRACE_MS; }
  void leave() { on = false; waiting = false; }
};
inline Sleeve sleeve;
}  // namespace panel_sleeve
