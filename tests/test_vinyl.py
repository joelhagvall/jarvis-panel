"""Spelas nu's spinning record: its motion, its still layer and its wiring; no device or network."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from PIL import Image, ImageChops

ROOT = Path(__file__).resolve().parents[1]
GLASS = ROOT / "styles/glass-v2"


class VinylTest(unittest.TestCase):
    def test_motion_spins_stops_on_pause_and_lifts_the_arm(self):
        source = r'''
#include "styles/glass-v2/vinyl_motion.h"
#include <cassert>
#include <cmath>
using namespace panel_vinyl;
int main() {
  Motion m; m.settle(true);
  for (int i = 0; i < 30; i++) assert(m.step(1.0f / 30, true));
  assert(std::fabs(m.angle - 200.0f) < 0.5f && m.lift == 0);         // 33 1/3 rpm: 200 degrees a second
  m.step(0.5f, true); assert(std::fabs(m.angle - 220.0f) < 0.5f);     // a late frame counts as 0.1 s at most
  for (int i = 0; i < 9; i++) m.step(0.1f, false);
  assert(m.speed > 0 && m.lift == 1);                                 // the arm is up in 0.4 s, the record still slows
  m.step(0.1f, false); m.step(0.1f, false);
  assert(m.speed == 0 && !m.step(0.1f, false));                       // stopped: no more redraws
  float still = m.angle; m.step(0.1f, false); assert(m.angle == still);
  for (int i = 0; i < 6; i++) m.step(0.1f, true);
  assert(std::fabs(m.speed - 1) < 1e-4f && m.lift == 0);
  Motion paused; paused.settle(false); assert(paused.speed == 0 && paused.lift == 1 && !paused.moving(false));
  for (int i = 0; i < 400; i++) m.step(0.1f, true);
  assert(m.angle >= 0 && m.angle < 360 && m.rotation_tenths() >= 0 && m.rotation_tenths() < 3600);
  Arm down = arm(100, 148, 72, 0), up = arm(100, 148, 72, 1);
  assert(down.pivot_x > 172 && down.pivot_x + 12 < 216);             // right of the record, left of the title (x 216)
  assert(std::hypot(down.end_x - 100, down.end_y - 148) < 72);        // the stylus is on the record
  assert(up.end_x > down.end_x && up.pivot_x == down.pivot_x);        // lifting swings it out to the right
  // Lifted, the arm and its rest are off the record, with room between (Joel: too close, 2026-10-28).
  assert(std::hypot(down.rest_x - 100, down.rest_y - 148) > 72 + 4.5f + 6);
  assert(std::hypot(up.head_x - 100, up.head_y - 148) > 72 + 6);
  assert(std::fabs(up.end_x - up.pivot_x) < 0.01f && down.rest_x == down.pivot_x);   // straight under the pivot
  // The stylus follows the song inward: at its start near the edge, at its end near the label, always on the record.
  Arm first = arm(100, 148, 72, 0, 0), last = arm(100, 148, 72, 0, 1);
  const float r0 = std::hypot(first.end_x - 100, first.end_y - 148), r1 = std::hypot(last.end_x - 100, last.end_y - 148);
  assert(std::fabs(r0 - 72 * OUTER) < 0.5f && std::fabs(r1 - 72 * INNER) < 0.5f && r1 < r0);
  assert(first.end_y > first.pivot_y && last.end_y > last.pivot_y);         // the front of the record, below the pivot
  // Slowing down and spinning up below half speed are turned live (Joel: "vinylen laggar", 2026-10-28): there the
  // 3.33-degree records changed less than 30 times a second. Still and full speed keep the records.
  assert(!live_turn(0) && live_turn(0.01f) && live_turn(0.49f) && !live_turn(0.5f) && !live_turn(1));
  Motion slow; slow.settle(true);
  int live_frames = 0;
  for (int i = 0; i < 60; i++) { slow.step(1.0f / 60, false); live_frames += live_turn(slow.speed); }
  assert(live_frames >= 29 && live_frames <= 31);                     // the last half second of a 1 s stop
  assert(std::fabs(live_angle(10, 0.25f, 1) - (10 + 200 * 0.25f * 2 / 60)) < 1e-3f);   // two frames ahead
  assert(live_angle(359.9f, 0.4f, 1) < 360 && live_angle(359.9f, 0.4f, 1) >= 0);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cpp = Path(tmp) / "vinyl.cpp"; cpp.write_text(source); binary = Path(tmp) / "vinyl"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                           check=True)
            subprocess.run([str(binary)], check=True)

    def test_rotate_turns_the_cover_inside_the_circle_and_fills_outside(self):
        source = r'''
#include "styles/glass-v2/vinyl_motion.h"
#include <cassert>
#include <cstdlib>
#include <vector>
using namespace panel_vinyl;
static int diff(uint16_t a, uint16_t b) {
  return std::abs((a >> 11) - (b >> 11)) + std::abs(((a >> 5) & 63) - ((b >> 5) & 63)) + std::abs((a & 31) - (b & 31));
}
int main() {
  const int n = 144; const uint16_t out = 0x10C4;
  std::vector<uint16_t> src(n * n), dst(n * n), big(2 * n * 2 * n), small(n * n);
  for (int y = 0; y < n; y++) for (int x = 0; x < n; x++)
    src[y * n + x] = uint16_t(((x * 31 / n) << 11) | ((y * 63 / n) << 5) | ((x + y) * 31 / (2 * n)));
  auto inside = [&](int x, int y) { float dx = x + 0.5f - n / 2.0f, dy = y + 0.5f - n / 2.0f; return dx * dx + dy * dy < 68 * 68; };
  rotate(src.data(), n, dst.data(), n, 0, out);
  for (int y = 0; y < n; y++) for (int x = 0; x < n; x++) {
    if (inside(x, y)) assert(diff(dst[y * n + x], src[y * n + x]) <= 3);   // 0 degrees: the cover itself
  }
  assert(dst[0] == out && dst[n - 1] == out && dst[n * n - 1] == out);      // the corners are the card
  rotate(src.data(), n, dst.data(), n, 90, out);
  for (int y = 0; y < n; y++) for (int x = 0; x < n; x++)                     // clockwise: dst(x, y) = src(y, n-1-x)
    if (inside(x, y)) assert(diff(dst[y * n + x], src[(n - 1 - x) * n + y]) <= 4);
  for (int y = 0; y < 2 * n; y++) for (int x = 0; x < 2 * n; x++) big[y * 2 * n + x] = src[(y / 2) * n + x / 2];
  rotate(big.data(), 2 * n, small.data(), n, 0, out);                        // a 288 px cover for the 144 px record
  for (int y = 0; y < n; y++) for (int x = 0; x < n; x++)
    if (inside(x, y)) assert(diff(small[y * n + x], src[y * n + x]) <= 4);
  for (float a = 0; a < 360; a += 7.3f) rotate(src.data(), n, dst.data(), n, a, out);   // never reads outside
  // Upside down: inside the circle the reversed pixels, around it the layer's, the right way up.
  std::vector<uint16_t> flipped(n * n), around(n * n);
  for (int i = 0; i < n * n; i++) around[i] = uint16_t(i * 7);
  flip_record(src.data(), flipped.data(), n, around.data());
  assert(flipped[72 * n + 72] == src[n * n - 1 - (72 * n + 72)]);
  assert(flipped[0] == around[0] && flipped[n * n - 1] == around[n * n - 1]);
  // The still layer in the same pass: opaque pixels are the layer, clear ones the cover, outside the circle the layer.
  std::vector<uint16_t> rgb(n * n, 0xFFFF); std::vector<uint8_t> alpha(n * n, 0);
  alpha[72 * n + 72] = 255; alpha[72 * n + 40] = 128; rgb[0] = 0x1234;
  rotate(src.data(), n, dst.data(), n, 0, out, rgb.data(), alpha.data());
  assert(dst[72 * n + 72] == 0xFFFF);                                        // opaque layer: white
  assert(diff(dst[72 * n + 20], src[72 * n + 20]) <= 3);                    // clear layer: the cover
  const uint16_t c = src[72 * n + 40], h = dst[72 * n + 40];                 // half: between the cover and white
  assert((h >> 11) >= (c >> 11) && (h & 31) >= (c & 31) && diff(h, c) > 10 && diff(h, 0xFFFF) > 10);
  assert(dst[0] == 0x1234);                                                  // outside: the layer's own pixel
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cpp = Path(tmp) / "rotate.cpp"; cpp.write_text(source); binary = Path(tmp) / "rotate"
            subprocess.run(["c++", "-std=c++17", "-O1", "-g", "-fsanitize=address,undefined", "-Wall", "-Wextra",
                            "-Werror", "-I", str(ROOT), str(cpp), "-o", str(binary)], check=True)
            subprocess.run([str(binary)], check=True)

    def test_the_radio_needle_finds_its_station(self):
        source = r'''
#include <cassert>
#include <cstdint>
#include <cstring>
#include <string>
namespace esphome { inline uint32_t millis() { return 0; } inline uint32_t micros() { return 0; } }
#include "styles/glass-v2/radio_dial.h"
int main() {
  assert(panel_radio::label("P3") == "FM 99,3" && panel_radio::label("P4 Stockholm") == "FM 103,3");
  assert(panel_radio::label("Ekot") == "Ekot");                               // no frequency: its name
  assert(panel_radio::needle_x(88) < panel_radio::needle_x(99.3f) && panel_radio::needle_x(108) <= panel_radio::DIAL_X1);
  assert(panel_radio::needle_x(0) == panel_radio::needle_x(98));             // without one: the middle
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cpp = Path(tmp) / "order.cpp"; cpp.write_text(source); binary = Path(tmp) / "order"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                           check=True)
            subprocess.run([str(binary)], check=True)

    def test_the_turntable_and_the_page_records_layer_are_what_the_generator_makes(self):
        sys.path.insert(0, str(GLASS))
        try:
            import turntable
        finally:
            sys.path.remove(str(GLASS))
        plinth = turntable.render()
        for made, path in ((plinth, turntable.OUT), (turntable.record_layer(plinth), turntable.LAYER)):
            committed = Image.open(path).convert("RGBA")
            self.assertEqual(committed.size, made.size)
            diff = ImageChops.difference(made, committed).getextrema()
            self.assertLessEqual(max(hi for _, hi in diff), 2, "run styles/glass-v2/turntable.py and commit both PNGs")
        # Opaque wood all behind the record: translucent strokes once left holes the page showed through.
        left, top = turntable.CX - turntable.RECORD - turntable.ORIGIN[0], turntable.CY - turntable.RECORD - turntable.ORIGIN[1]
        behind = Image.open(turntable.OUT).convert("RGBA").crop((left, top, left + 288, top + 288))
        self.assertEqual(min(behind.split()[3].tobytes()), 255)
        layer = Image.open(turntable.LAYER).convert("RGBA")
        self.assertEqual(layer.getpixel((2, 2)), behind.getpixel((2, 2)))     # around the record: the plinth
        sys.path.insert(0, str(GLASS))
        try:
            import now_playing
        finally:
            sys.path.remove(str(GLASS))
        self.assertEqual(now_playing.TURNTABLE_XY, turntable.ORIGIN)           # the page puts it where it was drawn
        self.assertEqual(now_playing.TURNTABLE_PIECES, turntable.PIECES)      # the same four pieces
        for name, image in turntable.pieces(plinth).items():
            committed = Image.open(turntable.piece_path(name)).convert("RGBA")
            self.assertEqual(ImageChops.difference(image, committed).getbbox(), None, name)
            if name in ('left','right'):
                self.assertEqual(committed.getchannel('A').getextrema(),(255,255))
            x0, y0, x1, y1 = turntable.PIECES[name]
            sx0, sy0, sx1, sy1 = turntable.SQUARE
            self.assertTrue(x1 <= sx0 or x0 >= sx1 or y1 <= sy0 or y0 >= sy1, f"{name} lies under the record")
        self.assertEqual(turntable.MARGIN, 0)                                   # no shadow film around it
        # Dithered onto the panel's RGB565 levels: undithered, the grain rounded away to nothing (2026-10-28).
        import numpy as np
        rgb = np.asarray(Image.open(turntable.piece_path("top")).convert("RGB")).astype(int)
        self.assertTrue(((rgb[..., 0] % 8) == 0).all() and ((rgb[..., 1] % 4) == 0).all() and ((rgb[..., 2] % 8) == 0).all())
        self.assertGreater(len({tuple(c) for c in rgb[20, 40:200]}), 6)        # the grain shows as several shades

    def test_the_sleeve_record_is_what_its_generator_makes_and_fits_beside_the_sleeve(self):
        sys.path.insert(0, str(GLASS))
        try:
            import sleeve, turntable, now_playing
        finally:
            sys.path.remove(str(GLASS))
        made, committed = sleeve.render(), Image.open(sleeve.OUT).convert("RGBA")
        self.assertEqual(committed.size, (sleeve.STRIP_W, sleeve.SIZE))
        self.assertLessEqual(max(hi for _, hi in ImageChops.difference(made, committed).getextrema()), 2,
                             "run styles/glass-v2/sleeve.py and commit the PNG")
        self.assertEqual(committed.getchannel("A").getextrema(), (255, 255))   # opaque: drawn once, never blended
        # Outside the record exactly the colour the page shows, not dithered round it (a lighter box, 2026-10-29).
        self.assertEqual(sleeve.PAGE, (16, 24, 32))
        for corner in ((sleeve.STRIP_W - 1, 0), (sleeve.STRIP_W - 1, sleeve.SIZE - 1), (0, 0), (sleeve.STRIP_W - 4, 6)):
            self.assertEqual(committed.convert("RGB").getpixel(corner), sleeve.PAGE)
        self.assertEqual(now_playing.SLEEVE_XY, sleeve.SLEEVE_XY)
        self.assertEqual(now_playing.SLEEVE_W, sleeve.SIZE + sleeve.STRIP_W)
        self.assertEqual(sleeve.RECORD_X + sleeve.SIZE, turntable.PLINTH[2])  # no further right than the turntable
        # music_sleeve.h's tap areas are the record's circle and the drawn sleeve.
        header = (GLASS / "music_sleeve.h").read_text()
        x, y = sleeve.SLEEVE_XY
        self.assertIn(f"X0 = {x}, Y0 = {y}, X1 = {x + now_playing.SLEEVE_W}, Y1 = {y + sleeve.SIZE};", header)
        self.assertIn(f"CX = {turntable.CX}, CY = {turntable.CY}, R = {turntable.RECORD};", header)

    def test_a_tap_on_a_covers_record_swaps_it_for_the_sleeve(self):
        source = r'''
#include "styles/glass-v2/music_sleeve.h"
#include <cassert>
int main() {
  panel_sleeve::Sleeve s;
  assert(!s.shown(false, 0) && !s.tap(216, 254));       // nothing playing / radio / no cover yet: a tap goes back
  assert(!s.shown(true, 100));
  assert(!s.tap(70, 120) && !s.on);                     // the square's corner is outside the round record
  assert(!s.tap(600, 300));                             // the text column: back
  assert(s.tap(216, 254) && s.on && s.shown(true, 200)); // the record: the sleeve
  assert(s.tap(60, 120) && !s.on);                      // the sleeve's corner: the turntable again
  assert(s.tap(216, 254) && s.tap(430, 254) && !s.on);  // the record's strip out of the sleeve counts too
  // A song change drops the cover for a moment: the sleeve stays up, no turntable in between.
  assert(s.tap(216, 254) && s.shown(true, 1000));
  assert(s.shown(false, 2300) && !s.lapsed(2300) && s.shown(true, 2400) && !s.waiting);
  // No cover for longer: the turntable, once, and the sleeve again when a cover comes while still chosen.
  assert(s.shown(false, 3000) && !s.lapsed(6399) && s.lapsed(6400) && !s.shown(false, 6400) && !s.lapsed(7500));
  assert(!s.tap(216, 254) && s.on && s.shown(true, 8000));
  s.leave(); assert(!s.on && !s.shown(true, 9000));      // leaving the page puts the turntable back
  // millis() wraps after 49 days: the wait still counts from the last cover.
  assert(s.tap(216, 254) && s.shown(true, 0xFFFFFF00u) && s.shown(false, 0xFFFFFF00u + 3000u) && s.lapsed(0xFFFFFF00u + 4000u));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            cpp = Path(tmp) / "sleeve.cpp"; cpp.write_text(source); binary = Path(tmp) / "sleeve"
            subprocess.run(["c++", "-std=c++17", "-Wall", "-Wextra", "-Werror", "-I", str(ROOT), str(cpp), "-o", str(binary)],
                           check=True)
            subprocess.run([str(binary)], check=True)

    def test_the_radio_cabinet_is_what_its_generator_makes(self):
        sys.path.insert(0, str(GLASS))
        try:
            import radio_cabinet
        finally:
            sys.path.remove(str(GLASS))
        made, committed = radio_cabinet.render(), Image.open(radio_cabinet.OUT).convert("RGBA")
        self.assertEqual(committed.size, (radio_cabinet.W, radio_cabinet.H))
        diff = ImageChops.difference(made, committed).getextrema()
        self.assertLessEqual(max(hi for _, hi in diff), 2, "run styles/glass-v2/radio_cabinet.py and commit the PNG")
        # Opaque inside: PIL strokes with alpha replace pixels, and the grain left 8,503 holes (2026-10-28).
        self.assertEqual(min(committed.crop((30, 30, 330, 220)).split()[3].tobytes()), 255)
        import numpy as np
        rgb = np.asarray(committed.convert("RGB")).astype(int)
        self.assertTrue(((rgb[..., 0] % 8) == 0).all() and ((rgb[..., 1] % 4) == 0).all())   # on RGB565 levels

    def test_the_committed_overlay_is_what_the_generator_makes(self):
        sys.path.insert(0, str(GLASS))
        try:
            import vinyl_overlay
        finally:
            sys.path.remove(str(GLASS))
        for size in vinyl_overlay.SIZES:
            made = vinyl_overlay.render(size)
            committed = Image.open(vinyl_overlay.out(size)).convert("RGBA")
            self.assertEqual(committed.size, (size, size))
            diff = ImageChops.difference(made, committed).getextrema()
            self.assertLessEqual(max(hi for _, hi in diff), 2, "run styles/glass-v2/vinyl_overlay.py and commit the PNGs")
            # Outside the circle it is the card itself, fully opaque, so the turning square's corners never show.
            e = size - 1
            for xy in ((0, 0), (e, 0), (0, e), (e, e), (size // 14, size // 14)):
                self.assertEqual(committed.getpixel(xy), (*vinyl_overlay.SHEET_BG, 255))
            self.assertLess(committed.getpixel((size * 40 // 144, size // 2))[3], 60)   # the cover shows through
            # Upside down it is the same layer (vinyl.h shows half of every turn as the records reversed).
            flipped = committed.rotate(180)
            diff = ImageChops.difference(committed, flipped).getextrema()
            self.assertLessEqual(max(hi for _, hi in diff), 24, "the still layer must look the same half a turn on")

    def test_spelas_nu_turns_tracks_and_keeps_radio_square(self):
        cfg = json.loads(subprocess.check_output([sys.executable, str(ROOT / "board.py"), "--voice",
            str(GLASS / "jarvis_orb.py"), "--remote", "http://example.test"], cwd=GLASS))
        includes = [Path(p).name for p in cfg["esphome"]["includes"]]
        self.assertLess(includes.index("vinyl_motion.h"), includes.index("vinyl.h"))
        boot = " ".join(json.dumps(b) for b in cfg["esphome"]["on_boot"])
        self.assertIn('"script.execute": "vinyl_setup"', boot)
        self.assertIn("bar_hook", boot)                                   # added to on_boot, not replacing it
        setup = next(s for s in cfg["script"] if s["id"] == "vinyl_setup")["then"][0]["lambda"]
        self.assertNotIn("panel_vinyl::sheet", json.dumps(cfg))    # the sheet and its record are gone (2026-10-28)
        self.assertIn('panel_vinyl::page.setup("Page", 144, 24, 2,', setup)    # continuous worker, no angular atlas
        self.assertIn("&direct_display::clock.count", setup)                       # paced by the display
        render = next(s for s in cfg["script"] if s["id"] == "music_render")["then"][0]["lambda"]
        self.assertIn('panel_vinyl::page.set(m.state == "playing");', render)
        self.assertIn('panel_vinyl::page.source(nullptr, panel_vinyl::Kind::BLANK);', render)   # always there
        for key in ("np_vinyl_page", "turntable_p1_top", "turntable_p1_bottom"):
            image = next(i for i in cfg["image"] if i["id"] == key)
            self.assertEqual(image["transparency"], "alpha_channel")
        for key in ('turntable_p1_left','turntable_p1_right'):
            image=next(i for i in cfg['image'] if i['id']==key)
            self.assertEqual(image.get('transparency','opaque'),'opaque')
        self.assertIn("id(np_vinyl_page)->get_lv_image_dsc()", setup)   # the page's record on the turntable
        # One 288 px download: the Spelas nu record shows it, the row a 144 px copy made once per cover.
        art = next(i for i in cfg["image"] if i["id"] == "np_art")
        self.assertEqual(art["platform"], "async_artwork")
        done = json.dumps(art["on_download_finished"])
        self.assertIn("panel_cover::small.attach(id(np_art).small_data())", done)
        self.assertIn("panel_cover::show(id(np_small_art_frame_image))", done)
        self.assertIn("panel_vinyl::page.source(id(np_art)->get_lv_image_dsc(), ", done)
        self.assertIn("panel_vinyl::Kind::PICTURE : panel_vinyl::Kind::LABEL", done)   # radio: the logo as label

    def test_spelas_nu_is_a_tab_between_hemmet_and_tavla_and_fits_the_baked_bar(self):
        sys.path.insert(0, str(GLASS))
        try:
            import gen
        finally:
            sys.path.remove(str(GLASS))
        self.assertEqual(gen.TABS, ["Gå hemifrån", "Hemmet", "Spelas nu", "Tavla", "Natt"])
        self.assertEqual(gen.SWIPE_ORDER.index(gen.MUSIC), gen.TABS.index("Spelas nu"))   # tab_go uses one index
        self.assertEqual(len(gen.TAB_WIDTHS), len(gen.TABS))
        self.assertEqual(sum(gen.TAB_WIDTHS) + 48, gen.RECTS["tabs"][2] - 12)            # tabs + cog fill the bar
        from PIL import ImageFont
        font = ImageFont.truetype(str(GLASS / ".esphome/font/Inter@600@False@v1.ttf"), 15) \
            if (GLASS / ".esphome/font/Inter@600@False@v1.ttf").exists() else None
        if font:
            for name, width in zip(gen.TABS, gen.TAB_WIDTHS):
                self.assertGreaterEqual(width - font.getlength(name), 18, name)            # room round each label
        cfg = json.loads(subprocess.check_output([sys.executable, str(ROOT / "board.py"), "--voice",
            str(GLASS / "jarvis_orb.py"), "--remote", "http://example.test"], cwd=GLASS))
        page = next(p for p in cfg["lvgl"]["pages"] if p["id"] == gen.MUSIC)
        self.assertIn("on_swipe_left", page)                                    # swipes move along the tabs
        # The whole screen, no painting around it (Joel, 2026-10-28): the card's colour, which the records'
        # still layer has outside their circle.
        self.assertEqual(page["bg_color"], "0x141826")
        self.assertFalse(any("image" in w and w["image"].get("src") == "art" for w in page["widgets"]))
        ids = json.dumps(page)
        for key in ("mp_out", "mp_out_fixed", "mp_progress", "s_musicpage"):
            self.assertIn(f'"id": "{key}"', ids)
        # LEFT_MID's y counts from the parent's middle (mp_group's): the column was 254 px too low, then 50 px too
        # high (2026-10-28).
        sys.path.insert(0, str(GLASS))
        try:
            import now_playing
        finally:
            sys.path.remove(str(GLASS))
        column = next(w for w in page["widgets"] if w.get("obj", {}).get("id") == "mp_group")["obj"]["widgets"][0]["obj"]
        self.assertEqual(column["align"], "LEFT_MID")
        group = next(w for w in page["widgets"] if w.get("obj", {}).get("id") == "mp_group")["obj"]
        self.assertEqual(group["height"] // 2 + column["y"], now_playing.PAGE_MID)
        # Taps beside the controls reach the page's back area: no full-width holder catches them (2026-10-28),
        # and a drag on the volume is never a swipe to another tab.
        def find(node, key):
            if isinstance(node, dict):
                for kind, body in node.items():
                    if isinstance(body, dict) and body.get("id") == key:
                        return body
                for v in node.values():
                    hit = find(v, key)
                    if hit: return hit
            elif isinstance(node, list):
                for v in node:
                    hit = find(v, key)
                    if hit: return hit
        for key in ("mp_group", "mp_empty", "mp_radio", "mp_out_row", "mp_progress_row", "mp_turntable"):
            self.assertIs(find(page, key).get("clickable"), False, key)
        slider = find(page, "s_musicpage")
        self.assertIs(slider["gesture_bubble"], False)
        self.assertTrue(page["widgets"][0]["obj"].get("on_click"))             # the back area, under everything
        sensors = {t.get("id"): t for t in cfg["text_sensor"]}
        self.assertIn("id(mp_out)", json.dumps(sensors["out_options"]))         # Inställningar's Ljud ut entries
        self.assertEqual(sensors["music_progress"]["attribute"], "progress")
        setup = next(s for s in cfg["script"] if s["id"] == "vinyl_setup")["then"][0]["lambda"]
        self.assertIn("id(musicvol_sync).execute()", setup)

    def test_no_header_returns_an_initializer_list(self):
        """A function returning std::initializer_list hands out a pointer to its own dead array: vinyl.h's
        all_parts() did, and that firmware crashed at boot and was rolled back (2026-10-28)."""
        import re
        for header in sorted(GLASS.glob("*.h")):
            text = header.read_text()
            self.assertIsNone(re.search(r"std::initializer_list<[^>]*>\s+\w+\s*\([^)]*\)\s*(const)?\s*\{", text),
                              f"{header.name} returns an initializer_list")

    def test_lambdas_name_every_id_literally(self):
        """ESPHome resolves id(...) in a lambda by matching the text, so a C macro that pastes a name together
        (id(k##_base)) is never resolved and fails to compile; it nearly went into vinyl_setup (2026-10-28)."""
        import re
        cfg = subprocess.check_output([sys.executable, str(ROOT / "board.py"), "--voice",
            str(GLASS / "jarvis_orb.py"), "--remote", "http://example.test"], cwd=GLASS).decode()
        self.assertEqual(re.findall(r"id\([^)]*##[^)]*\)", cfg), [])

    def test_no_font_lists_a_glyph_twice(self):
        """ESPHome rejects a font whose glyphs repeat: a "back" icon with prev's code point stopped the build's
        config check (2026-10-28) though every unit test passed."""
        cfg = json.loads(subprocess.check_output([sys.executable, str(ROOT / "board.py"), "--voice",
            str(GLASS / "jarvis_orb.py"), "--remote", "http://example.test"], cwd=GLASS))
        for font in cfg["font"]:
            glyphs = font.get("glyphs") or []
            self.assertEqual(len(glyphs), len(set(glyphs)), f"{font['id']} repeats a glyph")

    def test_headers_only_use_lvgl_widgets_the_config_builds(self):
        """ESPHome compiles LVGL with only the widget types the config uses: vinyl.h's lv_line_create failed to
        compile on the panel (2026-10-28) though every free test passed. A header may only call widget functions
        whose type appears in the generated lvgl tree."""
        import re
        cfg = json.loads(subprocess.check_output([sys.executable, str(ROOT / "board.py"), "--voice",
            str(GLASS / "jarvis_orb.py"), "--remote", "http://example.test"], cwd=GLASS))
        used = set()
        def walk(node):
            if isinstance(node, dict):
                used.update(node)
                for v in node.values(): walk(v)
            elif isinstance(node, list):
                for v in node: walk(v)
        walk(cfg["lvgl"])
        optional = {"line", "arc", "bar", "canvas", "chart", "spinner", "roller", "dropdown", "checkbox", "switch",
                    "keyboard", "textarea", "table", "led", "meter", "tabview", "tileview", "msgbox", "spinbox",
                    "buttonmatrix", "animimg", "qrcode", "slider", "label", "image", "button"}
        for header in cfg["esphome"]["includes"]:
            path = Path(header)
            if path.suffix != ".h" or not path.exists():
                continue
            text = path.read_text()
            # lvgl.h does not declare the image cache's API: vinyl.h's lv_image_cache_drop failed to compile
            # (2026-10-28). packed_orb.h includes it and turns the cache off at boot, so no one needs to drop.
            if "lv_image_cache_" in text:
                self.assertIn("lv_image_cache.h", text, f"{path.name} uses the image cache without its header")
            calls = set(re.findall(r"\blv_([a-z]+)_(?:create|set_|get_)", text))
            for widget in sorted(calls & optional):
                self.assertIn(widget, used, f"{path.name} calls lv_{widget}_* but the config has no {widget} widget")


if __name__ == "__main__":
    unittest.main()
