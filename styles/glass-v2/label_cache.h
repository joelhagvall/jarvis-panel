#pragma once
// Whole text lines as ready masks (docs/responsiveness.md, 2026-10-30). Text was 10–15 ms of a page change, of which
// only 4–6 ms was blending pixels: the rest was LVGL handling each glyph, in each of the redraw areas. This draw
// unit takes a plain label it has seen before, builds its A8 mask once with LVGL's own layout
// (lv_draw_label_iterate_characters, the glyphs' own bitmaps), keeps it, and blends the whole label in one step
// through the same RGB565 colour blend. Pixels stay identical: a label whose glyphs overlap (where per-glyph
// blending would mix twice) is never cached, and the first sighting of a label draws as before, so text that changes
// every frame (scrolling) never builds masks. tools/check_label_cache.py proves it against LVGL itself.
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <new>
#include "lvgl.h"
#include "src/draw/lv_draw_private.h"
#include "src/draw/lv_draw_label_private.h"
#include "src/draw/sw/blend/lv_draw_sw_blend_private.h"
#include "src/draw/sw/blend/lv_draw_sw_blend.h"
#include "src/draw/sw/lv_draw_sw.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#endif

namespace label_cache {

constexpr size_t LIMIT = 384 * 1024;   // masks kept, least recently drawn dropped first
constexpr int32_t SCORE = 10;          // preferred over the software unit for the labels it takes

struct Stats { uint32_t hits = 0, builds = 0, overlaps = 0, seen = 0; size_t bytes = 0; };
inline Stats stats;

// Everything lives in PSRAM: std::string/vector bookkeeping took internal RAM below the gates' 96 KiB floor
// (2026-10-30, the first panel install).
struct Entry {
  char *text{};          // PSRAM copy of the label's text
  size_t length{};
  const lv_font_t *font{};
  int32_t w{}, h{}, letter{}, line{}, ofs_x{}, ofs_y{};
  uint8_t align{}, flag{}, dir{};
  bool built{}, overlap{};
  lv_area_t box{};      // the glyphs' bounding box, relative to the label's coords
  uint8_t *mask{};
  size_t bytes{};
  uint32_t used{}, first_ms{}, chars{};
};
constexpr int CAPACITY = 192;
inline Entry *entries = nullptr;   // CAPACITY entries in PSRAM, made by install()
inline int count = 0;
inline uint32_t tick = 0;
inline lv_draw_unit_t *unit = nullptr;

inline uint8_t *alloc(size_t n) {
#ifdef USE_ESP32
  return static_cast<uint8_t *>(heap_caps_malloc(n, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
#else
  return static_cast<uint8_t *>(std::malloc(n));
#endif
}
inline void release(Entry &e) {
  if (e.mask) { std::free(e.mask); stats.bytes -= e.bytes; }
  e.mask = nullptr; e.bytes = 0; e.built = false;
}
inline void forget(int i) {
  release(entries[i]);
  std::free(entries[i].text);
  entries[i] = entries[--count];
  entries[count] = Entry{};
}

inline bool plain(const lv_draw_label_dsc_t *d) {
  return d->text && d->font && d->rotation == 0 && d->decor == LV_TEXT_DECOR_NONE &&
         !(d->flag & LV_TEXT_FLAG_RECOLOR) && d->outline_stroke_width == 0 && d->opa > LV_OPA_MIN &&
         (d->sel_start == LV_DRAW_LABEL_NO_TXT_SEL || d->sel_end == LV_DRAW_LABEL_NO_TXT_SEL);
}

inline Entry *find(const lv_draw_label_dsc_t *d, const lv_area_t &coords, bool add) {
  // text_length counts characters (0: all), and may exceed the string: key on the string itself plus that count.
  const size_t length = std::strlen(d->text);
  const int32_t w = lv_area_get_width(&coords), h = lv_area_get_height(&coords);
  if (!entries) return nullptr;
  for (int i = 0; i < count; ++i) {
    Entry &e = entries[i];
    if (e.font == d->font && e.w == w && e.h == h && e.letter == d->letter_space && e.line == d->line_space &&
        e.ofs_x == d->ofs_x && e.ofs_y == d->ofs_y && e.align == d->align && e.flag == d->flag &&
        e.dir == d->bidi_dir && e.chars == d->text_length && e.length == length &&
        std::memcmp(e.text, d->text, length) == 0)
      return &e;
  }
  if (!add) return nullptr;
  if (count == CAPACITY) {   // bounded bookkeeping: forget the least recently drawn
    int oldest = 0;
    for (int i = 1; i < count; ++i) if (entries[i].used < entries[oldest].used) oldest = i;
    forget(oldest);
  }
  char *text = reinterpret_cast<char *>(alloc(length + 1));
  if (!text) return nullptr;
  std::memcpy(text, d->text, length);
  text[length] = 0;
  Entry &e = entries[count++];
  e = Entry{};
  e.text = text; e.length = length;
  e.font = d->font; e.w = w; e.h = h; e.letter = d->letter_space; e.line = d->line_space;
  e.ofs_x = d->ofs_x; e.ofs_y = d->ofs_y; e.align = d->align; e.flag = d->flag; e.dir = d->bidi_dir;
  e.chars = d->text_length;
  e.used = ++tick;
  ++stats.seen;
  return &e;
}

// Collecting one label's glyphs. pass 0 measures the box, pass 1 fills the mask.
struct Build { int pass{}; int32_t x0{}, y0{}; lv_area_t box{}; bool any{}, bad{}, overlap{}; uint8_t *mask{}; int32_t stride{}; };
inline Build *building = nullptr;

inline void glyph(lv_draw_task_t *, lv_draw_glyph_dsc_t *g, lv_draw_fill_dsc_t *fill, const lv_area_t *) {
  Build &b = *building;
  if (fill || !g) { b.bad = true; return; }                         // selection or decoration: not a plain label
  if (g->format == LV_FONT_GLYPH_FORMAT_NONE) {
#if LV_USE_FONT_PLACEHOLDER
    if (g->bg_coords && g->g && g->g->is_placeholder) b.bad = true; // the software unit draws a box for it
#endif
    return;
  }
  if (g->format < LV_FONT_GLYPH_FORMAT_A1 || g->format > LV_FONT_GLYPH_FORMAT_A8) { b.bad = true; return; }
  const lv_area_t &a = *g->letter_coords;
  if (b.pass == 0) {
    const lv_area_t r{a.x1 - b.x0, a.y1 - b.y0, a.x2 - b.x0, a.y2 - b.y0};
    if (!b.any) { b.box = r; b.any = true; }
    else { b.box.x1 = LV_MIN(b.box.x1, r.x1); b.box.y1 = LV_MIN(b.box.y1, r.y1);
           b.box.x2 = LV_MAX(b.box.x2, r.x2); b.box.y2 = LV_MAX(b.box.y2, r.y2); }
    return;
  }
  const auto *buf = static_cast<const lv_draw_buf_t *>(lv_font_get_glyph_bitmap(g->g, g->_draw_buf));
  if (!buf) return;                                                 // the software unit skips it too
  const int32_t gw = lv_area_get_width(&a), gh = lv_area_get_height(&a);
  const uint32_t src_stride = buf->header.stride;
  for (int32_t y = 0; y < gh; ++y) {
    const uint8_t *src = buf->data + size_t(y) * src_stride;
    uint8_t *dst = b.mask + size_t(a.y1 - b.y0 - b.box.y1 + y) * b.stride + (a.x1 - b.x0 - b.box.x1);
    for (int32_t x = 0; x < gw; ++x) {
      if (!src[x]) continue;
      if (dst[x]) { b.overlap = true; return; }                     // blended twice by the software unit
      dst[x] = src[x];
    }
  }
}

inline void make_room(size_t need) {
  while (stats.bytes + need > LIMIT) {
    Entry *oldest = nullptr;
    for (int i = 0; i < count; ++i) if (entries[i].mask && (!oldest || entries[i].used < oldest->used)) oldest = &entries[i];
    if (!oldest) return;
    release(*oldest);
  }
}

inline bool build(lv_draw_task_t *t, const lv_draw_label_dsc_t *d, const lv_area_t &coords, Entry &e) {
  Build b; b.x0 = coords.x1; b.y0 = coords.y1;
  lv_draw_task_t whole = *t;                                         // LVGL's layout, without the area's clipping
  whole.clip_area = {-32768 / 2, -32768 / 2, 32767 / 2, 32767 / 2};
  building = &b;
  lv_draw_label_iterate_characters(&whole, d, &coords, glyph);
  if (b.bad) { building = nullptr; e.overlap = true; return false; }
  if (!b.any) { building = nullptr; e.built = true; e.box = {0, 0, -1, -1}; return true; }   // only spaces
  b.stride = lv_area_get_width(&b.box);
  const size_t bytes = size_t(b.stride) * lv_area_get_height(&b.box);
  make_room(bytes);
  b.mask = bytes <= LIMIT ? alloc(bytes) : nullptr;
  if (!b.mask) { building = nullptr; return false; }
  std::memset(b.mask, 0, bytes);
  b.pass = 1;
  lv_draw_label_iterate_characters(&whole, d, &coords, glyph);
  building = nullptr;
  if (b.overlap || b.bad) { std::free(b.mask); e.overlap = true; ++stats.overlaps; return false; }
  e.mask = b.mask; e.bytes = bytes; e.box = b.box; e.built = true;
  stats.bytes += bytes; ++stats.builds;
  return true;
}

inline void draw(lv_draw_task_t *t) {
  const auto *d = static_cast<const lv_draw_label_dsc_t *>(t->draw_dsc);
  const lv_area_t &coords = t->area;
  Entry *e = find(d, coords, false);
  if (!e || e->overlap || (!e->built && !build(t, d, coords, *e)) || e->overlap) {
    lv_draw_sw_label(t, d, &coords);                                 // as LVGL would
    return;
  }
  e->used = ++tick;
  if (!e->mask) return;                                             // nothing but spaces
  ++stats.hits;
  const lv_area_t area{coords.x1 + e->box.x1, coords.y1 + e->box.y1, coords.x1 + e->box.x2, coords.y1 + e->box.y2};
  lv_draw_sw_blend_dsc_t blend{};
  blend.color = d->color;
  blend.opa = d->opa;
  blend.mask_buf = e->mask;
  blend.mask_area = &area;
  blend.mask_stride = lv_area_get_width(&area);
  blend.blend_area = &area;
  blend.mask_res = LV_DRAW_SW_MASK_RES_CHANGED;
  lv_draw_sw_blend(t, &blend);
}

inline int32_t evaluate(lv_draw_unit_t *u, lv_draw_task_t *t) {
  if (t->type != LV_DRAW_TASK_TYPE_LABEL) return 0;
  const auto *d = static_cast<const lv_draw_label_dsc_t *>(t->draw_dsc);
  if (!plain(d)) return 0;
  Entry *e = find(d, t->area, true);
  if (!e || e->overlap) return 0;
  // The first refresh that draws a label leaves it to the software unit (in all its areas); one at least 50 ms
  // later builds the mask. Scrolling text has a new offset every frame, so it never gets that far.
  const uint32_t now = lv_tick_get();
  if (!e->built) {
    if (!e->first_ms) { e->first_ms = now ? now : 1; return 0; }
    if (lv_tick_diff(now, e->first_ms) < 50) return 0;
  }
  if (t->preference_score > SCORE) { t->preference_score = SCORE; t->preferred_draw_unit_id = u->idx; }
  return 1;
}

inline int32_t dispatch(lv_draw_unit_t *u, lv_layer_t *layer) {
  lv_draw_task_t *t = lv_draw_get_available_task(layer, nullptr, u->idx);
  if (!t || t->preferred_draw_unit_id != u->idx) return LV_DRAW_UNIT_IDLE;
  if (lv_draw_layer_alloc_buf(layer) == nullptr) return LV_DRAW_UNIT_IDLE;
  t->state = LV_DRAW_TASK_STATE_IN_PROGRESS;
  t->draw_unit = u;
  draw(t);
  t->state = LV_DRAW_TASK_STATE_FINISHED;
  lv_draw_dispatch_request();
  return 1;
}

inline void install() {
  if (unit) return;
  entries = reinterpret_cast<Entry *>(alloc(sizeof(Entry) * CAPACITY));
  if (!entries) return;
  for (int i = 0; i < CAPACITY; ++i) new (&entries[i]) Entry{};
  unit = static_cast<lv_draw_unit_t *>(lv_draw_create_unit(sizeof(lv_draw_unit_t)));
  unit->evaluate_cb = evaluate;
  unit->dispatch_cb = dispatch;
  unit->name = "LABEL_CACHE";
}

}  // namespace label_cache
