#pragma once
// Where a page change's render time goes, per LVGL draw task type, on the panel itself (docs/responsiveness.md).
// Armed only for the first refresh after the active screen changed. A draw unit that never claims a task sees
// every task as it is created; without an OS LVGL executes it synchronously right after (lv_draw.c
// lv_draw_finalize_task_creation -> lv_draw_dispatch), so the time until the next task's creation, or until
// REFR_READY for the last one, is charged to that task's type. "widgets" is the time before the first task:
// this callback runs first at REFR_START (installed before screen_swap's), so it is split into "decision"
// (screen_swap's, which also lays the new page out; its own last_us when it applied), "frame wait" (the previous
// frame still being presented, direct_display.h), "layout+sync" (the rest until RENDER_START: layout left, joining
// areas, copying the previous frame's areas into this buffer in lv_refr.c refr_sync_areas) and "walk"
// (RENDER_START to the first task). report() logs on the main loop, outside the refresh.
#include <cstdint>
#include "esp_log.h"
#include "esp_timer.h"
#include "lvgl.h"
#include "src/draw/lv_draw_private.h"
#include "src/display/lv_display_private.h"
#include "esphome/components/panel_debug/pixel_copy.h"
#if __has_include("label_cache.h")
#include "label_cache.h"
#define PAGE_DRAW_LABELS 1
#endif
#if __has_include("glyph_cache.h")
#include "glyph_cache.h"
#define PAGE_DRAW_GLYPHS 1
#endif

namespace page_draw {

enum Kind { WIDGETS, FILL, BORDER, SHADOW, LABEL, IMAGE, LAYER, OTHER, KINDS };
static const char *const NAMES[KINDS] = {"widgets", "fill", "border", "shadow", "label", "image", "layer", "other"};

struct Sample {
  int64_t us[KINDS];
  uint64_t px[KINDS];
  uint32_t tasks[KINDS];
  int64_t total_us, decision_us, frame_wait_us, sync_us, walk_us;
  int64_t previous_age_us;     // the refresh before this one: how long before this REFR_START it ended
  uint32_t previous_px;        // and how many pixels it drew
  const char *page;
  // Fills by drawn object (one object crossing several areas makes several tasks): where, rounding, opacity, time.
  struct Fill { int16_t x, y, w, h, radius; uint8_t opa; uint16_t tasks; int64_t us; } fills[24];
  int fill_count;
  uint32_t budget, areas, layout_us, collect_us, reduce_us;
  PanelPixelCopyStats blend;   // pixel_copy's counters at the start, then the page change's share
#ifdef PAGE_DRAW_LABELS
  label_cache::Stats labels;
#endif
#ifdef PAGE_DRAW_GLYPHS
  glyph_cache::Stats glyphs;   // the cache's counters at the start, then the page change's share
#endif
};

static Sample current, done;
static bool armed = false, fresh = false;
static int kind = WIDGETS;
static int64_t mark = 0, started = 0, render_start = 0, wait_at_start = 0;
static bool first_task = false;
static int fill_index = -1;
static uint32_t applied_at_start = 0;
// Every refresh: when it ended and what it drew, for the page change after it.
static int64_t last_ready = 0;
static uint32_t last_px = 0, refresh_px = 0;
static lv_obj_t *last_screen = nullptr;
static const char *(*page_name)() = nullptr;

inline int kind_of(lv_draw_task_type_t t) {
  switch (t) {
    case LV_DRAW_TASK_TYPE_FILL: return FILL;
    case LV_DRAW_TASK_TYPE_BORDER: return BORDER;
    case LV_DRAW_TASK_TYPE_BOX_SHADOW: return SHADOW;
    case LV_DRAW_TASK_TYPE_LABEL:
    case LV_DRAW_TASK_TYPE_LETTER: return LABEL;
    case LV_DRAW_TASK_TYPE_IMAGE: return IMAGE;
    case LV_DRAW_TASK_TYPE_LAYER: return LAYER;
    default: return OTHER;
  }
}

inline void charge(int64_t now) {
  current.us[kind] += now - mark;
  if (kind == FILL && fill_index >= 0) current.fills[fill_index].us += now - mark;
  mark = now;
}

inline void note_fill(const lv_draw_task_t *t) {
  fill_index = -1;
  const auto *d = (const lv_draw_fill_dsc_t *) t->draw_dsc;
  const lv_area_t &a = t->area;
  const int16_t x = a.x1, y = a.y1, w = lv_area_get_width(&a), h = lv_area_get_height(&a);
  for (int i = 0; i < current.fill_count; i++) {
    auto &f = current.fills[i];
    if (f.x == x && f.y == y && f.w == w && f.h == h) { f.tasks++; fill_index = i; return; }
  }
  if (current.fill_count == 24) return;
  fill_index = current.fill_count++;
  current.fills[fill_index] = {x, y, w, h, (int16_t) d->radius, d->opa, 1, 0};
}

inline int32_t evaluate(lv_draw_unit_t *, lv_draw_task_t *t) {
  if (!armed) return 0;
  const int64_t now = esp_timer_get_time();
  if (first_task) {
    first_task = false;
    if (render_start) current.walk_us = now - render_start;
  }
  charge(now);
  kind = kind_of(t->type);
  if (kind == FILL) note_fill(t);
  current.tasks[kind]++;
  current.px[kind] += (uint64_t) lv_area_get_width(&t->area) * lv_area_get_height(&t->area);
  return 0;
}

inline int32_t dispatch(lv_draw_unit_t *, lv_layer_t *) { return LV_DRAW_UNIT_IDLE; }

inline void install(const char *(*name)()) {
  page_name = name;
  auto *unit = (lv_draw_unit_t *) lv_draw_create_unit(sizeof(lv_draw_unit_t));
  unit->evaluate_cb = evaluate;
  unit->dispatch_cb = dispatch;
  unit->name = "PAGE_DRAW";
  auto *display = lv_display_get_default();
  last_screen = lv_screen_active();
  lv_display_add_event_cb(display, [](lv_event_t *) {
    lv_obj_t *screen = lv_screen_active();
    refresh_px = 0;
    if (screen == last_screen) return;
    last_screen = screen;
    current = Sample{};
    current.page = page_name ? page_name() : "unknown";
    current.previous_px = last_px;
#ifdef PAGE_DRAW_GLYPHS
    current.glyphs = glyph_cache::stats;
#endif
    current.blend = panel_pixel_copy_snapshot();
#ifdef PAGE_DRAW_LABELS
    current.labels = label_cache::stats;
#endif
    kind = WIDGETS;
    started = mark = esp_timer_get_time();
    current.previous_age_us = last_ready ? started - last_ready : -1;
    render_start = 0;
    first_task = true;
#ifdef HALLPANEL_DIRECT_DISPLAY
    wait_at_start = direct_display::wait_us_total;
#endif
#ifdef HALLPANEL_SCREEN_SWAP
    applied_at_start = screen_swap::state.stats.applied;
#endif
    armed = true;
  }, LV_EVENT_REFR_START, nullptr);
  lv_display_add_event_cb(display, [](lv_event_t *) {
    auto *d = lv_display_get_default();
    for (uint32_t i = 0; i < d->inv_p; i++)
      if (!d->inv_area_joined[i]) refresh_px += lv_area_get_size(&d->inv_areas[i]);
    if (!armed || render_start) return;
    render_start = esp_timer_get_time();
#ifdef HALLPANEL_DIRECT_DISPLAY
    current.frame_wait_us = direct_display::wait_us_total - wait_at_start;
#endif
#ifdef HALLPANEL_SCREEN_SWAP
    {
      const auto &st = screen_swap::state.stats;
      current.budget = st.last_budget;
      current.layout_us = st.last_layout_us;
      if (st.applied != applied_at_start) {
        current.decision_us = st.last_us;
        current.areas = st.last_areas;
        current.collect_us = st.last_collect_us;
        current.reduce_us = st.last_reduce_us;
      }
    }
#endif
    current.sync_us = render_start - started - current.decision_us - current.frame_wait_us;
  }, LV_EVENT_RENDER_START, nullptr);
  lv_display_add_event_cb(display, [](lv_event_t *) {
    int64_t now = esp_timer_get_time();
    if (refresh_px) {   // a refresh that drew something; empty ones are not the frame a page change waits for
      last_ready = now;
      last_px = refresh_px;
    }
    if (!armed) return;
    charge(now);
    armed = false;
    current.total_us = now - started;
    {
      const auto b = panel_pixel_copy_snapshot();
      current.blend.mask_blends = b.mask_blends - current.blend.mask_blends;
      current.blend.mask_pixels = b.mask_pixels - current.blend.mask_pixels;
      current.blend.mask_us = b.mask_us - current.blend.mask_us;
      current.blend.ppa_copies = b.ppa_copies - current.blend.ppa_copies;
      current.blend.ppa_bytes = b.ppa_bytes - current.blend.ppa_bytes;
      current.blend.ppa_us = b.ppa_us - current.blend.ppa_us;
      current.blend.ppa_sync_us = b.ppa_sync_us - current.blend.ppa_sync_us;
    }
#ifdef PAGE_DRAW_LABELS
    {
      const auto &l = label_cache::stats;
      current.labels = {l.hits - current.labels.hits, l.builds - current.labels.builds,
                        l.overlaps - current.labels.overlaps, l.seen, l.bytes};
    }
#endif
#ifdef PAGE_DRAW_GLYPHS
    const auto &g = glyph_cache::stats;
    current.glyphs = {g.hits - current.glyphs.hits, g.misses - current.glyphs.misses,
                      g.uncached - current.glyphs.uncached, g.glyphs, g.bytes, g.miss_us - current.glyphs.miss_us};
#endif
    done = current;
    fresh = true;
  }, LV_EVENT_REFR_READY, nullptr);
}

inline void report() {
  if (!fresh) return;
  fresh = false;
  char line[480];
  int n = snprintf(line, sizeof(line), "to %s %.1f ms: [previous refresh %.1f ms before, %u px; decision %.1f frame wait %.1f layout+sync %.1f "
                   "walk %.1f]", done.page, done.total_us / 1000.0, done.previous_age_us / 1000.0,
                   (unsigned) done.previous_px, done.decision_us / 1000.0, done.frame_wait_us / 1000.0, done.sync_us / 1000.0, done.walk_us / 1000.0);
  for (int k = 0; k < KINDS && n < (int) sizeof(line); k++)
    if (done.tasks[k] || done.us[k] >= 100)
      n += snprintf(line + n, sizeof(line) - n, " %s %.1f ms/%u/%llukpx", NAMES[k], done.us[k] / 1000.0,
                    (unsigned) done.tasks[k], (unsigned long long) (done.px[k] / 1000));
#ifdef PAGE_DRAW_GLYPHS
  if (n < (int) sizeof(line))
    snprintf(line + n, sizeof(line) - n, " | glyphs hit %u miss %u uncached %u unpack %.1f ms, kept %u/%u kB",
             (unsigned) done.glyphs.hits, (unsigned) done.glyphs.misses, (unsigned) done.glyphs.uncached,
             done.glyphs.miss_us / 1000.0, (unsigned) done.glyphs.glyphs, (unsigned) (done.glyphs.bytes / 1024));
#endif
  ESP_LOGI("page_draw", "%s", line);
#ifdef HALLPANEL_SCREEN_SWAP
  ESP_LOGI("page_draw", "  swap budget %u -> %u areas; decision: layout %.1f collect %.1f reduce %.1f ms",
           (unsigned) done.budget, (unsigned) done.areas, done.layout_us / 1000.0, done.collect_us / 1000.0,
           done.reduce_us / 1000.0);
#endif
  ESP_LOGI("page_draw", "  backdrop PPA copies %u, %u kB: %.1f ms, of which cache write-back %.1f ms",
           (unsigned) done.blend.ppa_copies, (unsigned) (done.blend.ppa_bytes / 1024), done.blend.ppa_us / 1000.0,
           done.blend.ppa_sync_us / 1000.0);
  ESP_LOGI("page_draw", "  mask blends (text, rounded edges) %.1f ms in %u calls, %u kpx",
           done.blend.mask_us / 1000.0, (unsigned) done.blend.mask_blends, (unsigned) (done.blend.mask_pixels / 1000));
#ifdef PAGE_DRAW_LABELS
  ESP_LOGI("page_draw", "  label masks: %u drawn, %u built, %u refused (overlap); %u kB kept",
           (unsigned) done.labels.hits, (unsigned) done.labels.builds, (unsigned) done.labels.overlaps,
           (unsigned) (done.labels.bytes / 1024));
#endif
  // The six costliest fills.
  bool used[24] = {};
  for (int shown = 0; shown < 6; shown++) {
    int best = -1;
    for (int i = 0; i < done.fill_count; i++)
      if (!used[i] && (best < 0 || done.fills[i].us > done.fills[best].us)) best = i;
    if (best < 0 || done.fills[best].us < 200) break;
    used[best] = true;
    const auto &f = done.fills[best];
    ESP_LOGI("page_draw", "  fill %dx%d at %d,%d radius %d opa %u: %.1f ms in %u tasks", f.w, f.h, f.x, f.y,
             f.radius, (unsigned) f.opa, f.us / 1000.0, (unsigned) f.tasks);
  }
}

}  // namespace page_draw
