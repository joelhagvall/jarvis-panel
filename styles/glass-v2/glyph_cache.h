#pragma once
// Pre-rendered glyphs: ESPHome's fonts are 4 bpp, and esphome::font::Font::get_glyph_bitmap unpacks each glyph to
// A8 pixel by pixel every time LVGL draws it, in every refresh area it crosses. This keeps each glyph's A8 bitmap
// after its first unpack and hands LVGL the same bytes again (lv_draw_sw_letter.c reads glyph_data as a draw buf),
// so the pixels are identical. Bounded: past LIMIT bytes, or when PSRAM is short, glyphs unpack as before.
// docs/responsiveness.md "Page changes"; the counters appear in page_draw's log line.
#include <cstdint>
#include <unordered_map>
#include "esp_timer.h"
#include "lvgl.h"

namespace glyph_cache {

static constexpr size_t LIMIT = 384 * 1024;

struct Stats {
  uint32_t hits = 0, misses = 0, uncached = 0, glyphs = 0;
  size_t bytes = 0;
  int64_t miss_us = 0;   // unpack time of the misses and uncached glyphs
};
static Stats stats;
static std::unordered_map<uint64_t, lv_draw_buf_t *> glyphs;
static const void *(*unpack)(lv_font_glyph_dsc_t *, lv_draw_buf_t *) = nullptr;


inline const void *bitmap(lv_font_glyph_dsc_t *g, lv_draw_buf_t *scratch) {
  if (scratch == nullptr || g->box_w == 0 || g->box_h == 0) return unpack(g, scratch);
  const uint64_t key = ((uint64_t) (uintptr_t) g->resolved_font << 32) ^ g->gid.index;
  auto found = glyphs.find(key);
  if (found != glyphs.end()) {
    stats.hits++;
    return found->second;
  }
  const int64_t start = esp_timer_get_time();
  lv_draw_buf_t *kept = nullptr;
  const size_t size = (size_t) g->box_w * g->box_h;
  if (stats.bytes + size <= LIMIT)
    kept = lv_draw_buf_create(g->box_w, g->box_h, LV_COLOR_FORMAT_A8, LV_STRIDE_AUTO);
  if (kept == nullptr || kept->header.stride != lv_draw_buf_width_to_stride(g->box_w, LV_COLOR_FORMAT_A8)) {
    if (kept) lv_draw_buf_destroy(kept);
    const void *out = unpack(g, scratch);
    stats.uncached++;
    stats.miss_us += esp_timer_get_time() - start;
    return out;
  }
  if (unpack(g, kept) == nullptr) {
    lv_draw_buf_destroy(kept);
    return nullptr;
  }
  glyphs.emplace(key, kept);
  stats.misses++;
  stats.glyphs++;
  stats.bytes += kept->data_size;
  stats.miss_us += esp_timer_get_time() - start;
  return kept;
}

// Every font shares esphome::font::Font::get_glyph_bitmap; wrap it once per font.
inline void install(const lv_font_t *font) {
  auto *f = const_cast<lv_font_t *>(font);
  if (f->get_glyph_bitmap == bitmap) return;
  if (unpack == nullptr) unpack = f->get_glyph_bitmap;
  if (f->get_glyph_bitmap != unpack) return;   // a font with another bitmap callback keeps it
  f->get_glyph_bitmap = bitmap;
}

}  // namespace glyph_cache
