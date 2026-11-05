#pragma once
// Two redraw savings for pages that start with an opaque full-screen background image.
//
// 1. cover_backdrop(): LVGL 9.5's lv_image inherits lv_obj's cover check, which answers "does not
//    cover" for any object without its own opaque bg, and its image check never answers "covers".
//    So every redrawn area of such a page first filled the screen's bg colour and then drew the
//    opaque image over it: a full-screen software fill into PSRAM on each page change, and one under
//    every orb frame. The callback reports the cover LVGL's image check intended, so LVGL starts
//    drawing at the image. The fill was always overdrawn; the shown pixels do not change.
//
// 2. A page change between two screens with the same opaque full-screen backdrop redraws only
//    what differs, instead of all 1024x600 pixels.
//
// lv_screen_load() invalidates the whole new screen as its very last step (LVGL 9.5
// load_new_screen). Gå hemifrån and Hemmet both start with the same opaque `bg` image at 0,0,
// so outside their own widgets the two screens have identical pixels. Before the next refresh
// that one full-screen area is replaced by what LVGL itself would invalidate if a single shared
// screen hid the old widgets and showed the new ones: lv_obj_invalidate() of every visible
// object that draws something, on both screens. Every other invalidation made in between is
// kept. Anything unexpected (another backdrop, an animation, a second page change before the
// refresh, a second full-screen area) leaves LVGL's own full redraw untouched.
//
// The areas are computed as lv_obj_invalidate() does (coords plus ext draw size, clipped by
// lv_obj_area_is_visible) without calling it: in LVGL 9.5 every lv_obj_invalidate() also walks the
// whole active, previous and layer trees for blurred widgets, about a hundred tree walks per page
// change. That walk can only add blur/drop-shadow widgets; with any such style the full redraw stays.
#include "lvgl.h"
#include "src/display/lv_display_private.h"
#include "src/core/lv_obj_draw_private.h"
#include "src/core/lv_refr_private.h"
#include "src/misc/lv_area_private.h"
#include "src/core/lv_obj_event_private.h"
#include "src/misc/lv_event_private.h"
#include "src/core/lv_obj_private.h"
#include "src/core/lv_obj_style_private.h"
#include <chrono>
#include <cstdint>
#include <cstring>
#include <initializer_list>

#define HALLPANEL_SCREEN_SWAP 1

namespace screen_swap {

constexpr int kCapacity = 192;  // collected areas; a full list is merged, never dropped
// Areas handed back to LVGL (at most LV_INV_BUF_SIZE, 32). Measured 2026-10-30 on the panel, release to drawn
// frame for Gå hemifrån / Hemmet: 24 areas 54.9 / 41.0 ms, 6 areas 40.2 / 36.1, 3 areas 40.5 / 36.5, LVGL's full
// redraw 41.9 / 37.3. Each area walks the widget tree and starts its own backdrop copy again.
constexpr int kBudget = 6;

enum Fallback : uint32_t { kNone, kNested, kScreen, kBackdrop, kFullAreas, kTransform, kBlur };

struct Stats {
  uint32_t applied{}, fallbacks{}, last_fallback{}, last_areas{}, last_pixels{}, last_us{};
  uint32_t last_old_us{}, last_collected{};
  uint32_t last_layout_us{}, last_collect_us{}, last_reduce_us{}, last_budget{};
};
// Areas handed back to LVGL. An experiment may cycle through several budgets, one per page change; 0 keeps
// LVGL's own full-screen redraw (docs/responsiveness.md). Use an odd count: alternating two pages with an even
// one pairs every budget with the same page (2026-10-30). Summarise with tools/page_draw_summary.py.
inline int budget_cycle[8]{};
inline int budget_cycle_count = 0;
inline uint32_t budget_turn = 0;

// Clipped screen areas: width * height < 2^31, so 32-bit arithmetic suffices on the 32-bit core.
inline int32_t size_of(const lv_area_t &a) { return (a.x2 - a.x1 + 1) * (a.y2 - a.y1 + 1); }
inline bool contains(const lv_area_t &outer, const lv_area_t &inner) {
  return inner.x1 >= outer.x1 && inner.y1 >= outer.y1 && inner.x2 <= outer.x2 && inner.y2 <= outer.y2;
}
inline lv_area_t bounds(const lv_area_t &a, const lv_area_t &b) {
  return {a.x1 < b.x1 ? a.x1 : b.x1, a.y1 < b.y1 ? a.y1 : b.y1, a.x2 > b.x2 ? a.x2 : b.x2, a.y2 > b.y2 ? a.y2 : b.y2};
}

// A short list of rectangles that together cover every added rectangle.
struct Areas {
  lv_area_t a[kCapacity];
  int32_t size[kCapacity];
  int32_t best_cost[kCapacity];
  int16_t best[kCapacity];
  bool dead[kCapacity];
  int n{};
  void clear() { n = 0; }
  void add(const lv_area_t &v) {
    if (v.x2 < v.x1 || v.y2 < v.y1) return;
    if (n == kCapacity) reduce(kCapacity / 2);
    a[n++] = v;
  }
  // Pixels the bounding box of i and j adds beyond both (LVGL's own join test is this < 0).
  int32_t waste(int i, int j) const { return size_of(bounds(a[i], a[j])) - size[i] - size[j]; }
  void find_best(int i) {
    best_cost[i] = INT32_MAX;
    best[i] = -1;
    for (int j = 0; j < n; ++j)
      if (j != i && !dead[j]) {
        const int32_t w = waste(i, j);
        if (w < best_cost[i]) { best_cost[i] = w; best[i] = int16_t(j); }
      }
  }
  // Merge every pair whose bounding box adds no pixels (LVGL's own join test), as cheaply as possible: sorted by
  // x1, a rectangle can only join one that starts at most one pixel after it ends, so most pairs are never tried.
  // On the panel this cut ~110 collected areas to a few dozen before the costly merging below.
  void premerge() {
    for (int i = 1; i < n; ++i)  // insertion sort by x1: n is at most kCapacity and nearly sorted already
      for (int k = i; k > 0 && a[k].x1 < a[k - 1].x1; --k) { const lv_area_t t = a[k]; a[k] = a[k - 1]; a[k - 1] = t; }
    for (int i = 0; i < n; ++i) dead[i] = false;
    bool merged = true;
    while (merged) {
      merged = false;
      for (int i = 0; i < n; ++i) {
        if (dead[i]) continue;
        for (int j = i + 1; j < n && a[j].x1 <= a[i].x2 + 1; ++j) {
          if (dead[j]) continue;
          const lv_area_t box = bounds(a[i], a[j]);
          if (size_of(box) - size_of(a[i]) - size_of(a[j]) > 0) continue;
          a[i] = box;   // x1 can only stay or shrink, so the order still holds for i's later partners
          dead[j] = true;
          merged = true;
        }
      }
    }
    int m = 0;
    for (int i = 0; i < n; ++i)
      if (!dead[i]) a[m++] = a[i];
    n = m;
  }
  // Drop contained rectangles, then merge the pair whose bounding box adds the fewest pixels
  // until `target` remain. Each rectangle caches its best partner, so a merge rescans only the
  // rectangles that pointed at the merged pair instead of every pair.
  void reduce(int target) {
    if (n > target) premerge();
    for (int i = 0; i < n; ++i) dead[i] = false;
    for (int i = 0; i < n; ++i)
      for (int j = 0; j < n && !dead[i]; ++j)
        if (j != i && !dead[j] && contains(a[j], a[i]) && (!contains(a[i], a[j]) || j < i)) dead[i] = true;
    int alive = 0;
    for (int i = 0; i < n; ++i) alive += !dead[i];
    if (alive > target) {
      for (int i = 0; i < n; ++i) size[i] = size_of(a[i]);
      for (int i = 0; i < n; ++i)
        if (!dead[i]) find_best(i);
      while (alive > target) {
        int i = -1;
        for (int k = 0; k < n; ++k)
          if (!dead[k] && best[k] >= 0 && (i < 0 || best_cost[k] < best_cost[i])) i = k;
        const int j = best[i];
        a[i] = bounds(a[i], a[j]);
        size[i] = size_of(a[i]);
        dead[j] = true;
        --alive;
        for (int k = 0; k < n; ++k)  // the merged box may now contain others
          if (k != i && !dead[k] && contains(a[i], a[k])) { dead[k] = true; --alive; }
        for (int k = 0; k < n; ++k) {
          if (dead[k] || k == i) continue;
          if (best[k] < 0 || dead[best[k]] || best[k] == i) find_best(k);
          else {
            const int32_t w = waste(k, i);
            if (w < best_cost[k]) { best_cost[k] = w; best[k] = int16_t(i); }
          }
        }
        find_best(i);
      }
    }
    int m = 0;
    for (int i = 0; i < n; ++i)
      if (!dead[i]) a[m++] = a[i];
    n = m;
  }
};

struct State {
  lv_display_t *display{};
  enum Mode { kIdle, kArmed } mode{kIdle};
  bool nested{}, transformed{}, blurred{};
  lv_obj_t *from{}, *to{};
  Areas areas;  // everything to redraw: earlier invalidations, both pages' widgets, later ones
  int full_areas{};
  Stats stats;
};
inline State state;

inline bool is_full(const lv_display_t *d, const lv_area_t &a) {
  return a.x1 <= 0 && a.y1 <= 0 && a.x2 >= lv_display_get_horizontal_resolution(d) - 1 &&
         a.y2 >= lv_display_get_vertical_resolution(d) - 1;
}

// The image draws every pixel of its own coordinates, fully opaque, untransformed.
inline bool opaque_image(lv_obj_t *image) {
  if (!lv_obj_check_type(image, &lv_image_class) || lv_obj_has_flag(image, LV_OBJ_FLAG_HIDDEN)) return false;
  const void *src = lv_image_get_src(image);
  if (!src || lv_image_src_get_type(src) != LV_IMAGE_SRC_VARIABLE) return false;
  const auto *dsc = static_cast<const lv_image_dsc_t *>(src);
  const auto cf = dsc->header.cf;
  if (cf != LV_COLOR_FORMAT_RGB565 && cf != LV_COLOR_FORMAT_RGB888 && cf != LV_COLOR_FORMAT_XRGB8888) return false;
  if (!dsc->data || dsc->data_size < uint32_t(dsc->header.stride) * dsc->header.h) return false;
  if (lv_image_get_src_width(image) != lv_obj_get_width(image) ||
      lv_image_get_src_height(image) != lv_obj_get_height(image)) return false;
  constexpr lv_part_t main = LV_PART_MAIN;
  return lv_obj_get_style_opa(image, main) == LV_OPA_COVER && lv_obj_get_style_image_opa(image, main) == LV_OPA_COVER &&
         lv_obj_get_style_blend_mode(image, main) == LV_BLEND_MODE_NORMAL &&
         lv_image_get_blend_mode(image) == LV_BLEND_MODE_NORMAL && lv_obj_get_style_radius(image, main) == 0 &&
         !lv_obj_get_style_clip_corner(image, main) && lv_obj_get_style_transform_width(image, main) == 0 &&
         lv_obj_get_style_transform_height(image, main) == 0 && lv_image_get_scale_x(image) == LV_SCALE_NONE &&
         lv_image_get_scale_y(image) == LV_SCALE_NONE && lv_image_get_rotation(image) == 0 &&
         lv_image_get_offset_x(image) == 0 && lv_image_get_offset_y(image) == 0 &&
         !lv_image_get_bitmap_map_src(image) && lv_obj_get_layer_type(image) == LV_LAYER_TYPE_NONE;
}

inline void cover_event(lv_event_t *e) {
  auto *info = static_cast<lv_cover_check_info_t *>(lv_event_get_param(e));
  lv_obj_t *image = lv_event_get_current_target_obj(e);
  if (!info || info->res == LV_COVER_RES_MASKED || !opaque_image(image)) return;
  lv_area_t coords;
  lv_obj_get_coords(image, &coords);
  if (lv_area_is_in(info->area, &coords, 0)) info->res = LV_COVER_RES_COVER;
}

// Put the cover callback on a page's first child when that is an image (checked again on every use).
inline void cover_backdrop(lv_obj_t *screen) {
  if (!screen || lv_obj_get_child_count(screen) == 0) return;
  lv_obj_t *image = lv_obj_get_child(screen, 0);
  if (!lv_obj_check_type(image, &lv_image_class)) return;
  const uint32_t count = lv_obj_get_event_count(image);
  for (uint32_t i = 0; i < count; ++i)
    if (lv_event_dsc_get_cb(lv_obj_get_event_dsc(image, i)) == cover_event) return;
  lv_obj_add_event_cb(image, cover_event, LV_EVENT_COVER_CHECK, nullptr);
}

inline bool draw_events(lv_obj_t *obj) {
  const uint32_t count = lv_obj_get_event_count(obj);
  for (uint32_t i = 0; i < count; ++i) {
    const auto *dsc = lv_obj_get_event_dsc(obj, i);
    if (dsc && dsc->cb == cover_event) continue;  // ours: changes no pixel
    const uint32_t filter = dsc ? dsc->filter & ~uint32_t(LV_EVENT_PREPROCESS | LV_EVENT_MARKED_DELETING) : 0;
    if (filter == LV_EVENT_ALL || (filter >= LV_EVENT_COVER_CHECK && filter <= LV_EVENT_DRAW_TASK_ADDED)) return true;
  }
  return false;
}

// Conservative: only a plain object with nothing to draw is skipped (its children are still visited).
inline bool draws_itself(lv_obj_t *obj) {
  if (!lv_obj_check_type(obj, &lv_obj_class)) return true;  // labels, images, buttons, sliders ...
  constexpr lv_part_t main = LV_PART_MAIN;
  if (lv_obj_get_style_bg_opa(obj, main) > LV_OPA_TRANSP || lv_obj_get_style_bg_image_src(obj, main)) return true;
  if (lv_obj_get_style_border_width(obj, main) > 0 && lv_obj_get_style_border_opa(obj, main) > LV_OPA_TRANSP &&
      lv_obj_get_style_border_side(obj, main) != LV_BORDER_SIDE_NONE) return true;
  if (lv_obj_get_style_outline_width(obj, main) > 0 && lv_obj_get_style_outline_opa(obj, main) > LV_OPA_TRANSP) return true;
  if (lv_obj_get_style_shadow_opa(obj, main) > LV_OPA_TRANSP &&
      (lv_obj_get_style_shadow_width(obj, main) > 0 || lv_obj_get_style_shadow_spread(obj, main) > 0)) return true;
  if (lv_obj_has_flag(obj, LV_OBJ_FLAG_SCROLLABLE) && lv_obj_get_scrollbar_mode(obj) != LV_SCROLLBAR_MODE_OFF) return true;
  return draw_events(obj);
}

// Any style with blur or drop shadow, in any state: LVGL then also redraws such a widget wholly.
inline bool blur_style(const lv_obj_t *obj) {
  for (uint32_t i = 0; i < obj->style_cnt; ++i) {
    const lv_obj_style_t &entry = obj->styles[i];
    if (entry.is_disabled) continue;
    lv_style_value_t v;
    if (lv_style_get_prop(entry.style, LV_STYLE_BLUR_RADIUS, &v) == LV_STYLE_RES_FOUND ||
        lv_style_get_prop(entry.style, LV_STYLE_DROP_SHADOW_OPA, &v) == LV_STYLE_RES_FOUND) return true;
  }
  return false;
}

inline void find_blur(const lv_obj_t *obj) {
  if (blur_style(obj)) state.blurred = true;
  const uint32_t count = lv_obj_get_child_count(obj);
  for (uint32_t i = 0; i < count && !state.blurred; ++i) find_blur(lv_obj_get_child(obj, int32_t(i)));
}

// The area lv_obj_invalidate() would add for each visible object that draws something. LVGL clips
// a child to its parent unless the parent lets it overflow, so below an object whose own area is
// taken no child adds pixels; those children are still checked for transforms and blur.
inline void collect(lv_obj_t *obj, bool covered = false) {
  if (lv_obj_has_flag(obj, LV_OBJ_FLAG_HIDDEN)) return;
  // LVGL samples a transformed layer in strips that start at the redrawn area's edge, so its
  // pixels depend on that area by a rounding step. Such a page keeps the full redraw.
  if (lv_obj_get_layer_type(obj) == LV_LAYER_TYPE_TRANSFORM) state.transformed = true;
  if (blur_style(obj)) state.blurred = true;
  const bool draws = !covered && draws_itself(obj);
  if (draws) {
    lv_area_t area = obj->coords;
    const int32_t ext = lv_obj_get_ext_draw_size(obj);
    lv_area_increase(&area, ext, ext);
    if (lv_obj_area_is_visible(obj, &area)) state.areas.add(area);
  }
  const bool children_covered = (covered || draws) && !lv_obj_has_flag(obj, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
  const uint32_t count = lv_obj_get_child_count(obj);
  for (uint32_t i = 0; i < count; ++i) collect(lv_obj_get_child(obj, int32_t(i)), children_covered);
}

// The screen's first child, when it is a plain opaque full-screen image the screen draws nothing over.
inline lv_obj_t *backdrop(lv_display_t *d, lv_obj_t *screen) {
  if (!screen || lv_obj_get_child_count(screen) == 0) return nullptr;
  if (lv_obj_has_flag(screen, LV_OBJ_FLAG_SCROLLABLE) || draw_events(screen) ||
      lv_obj_get_style_opa(screen, LV_PART_MAIN) != LV_OPA_COVER ||
      lv_obj_get_layer_type(screen) != LV_LAYER_TYPE_NONE) return nullptr;
  lv_obj_t *image = lv_obj_get_child(screen, 0);
  if (!opaque_image(image) || draw_events(image) || lv_obj_get_style_image_recolor_opa(image, LV_PART_MAIN) != LV_OPA_TRANSP)
    return nullptr;
  lv_area_t coords;
  lv_obj_get_coords(image, &coords);
  return coords.x1 == 0 && coords.y1 == 0 && coords.x2 == lv_display_get_horizontal_resolution(d) - 1 &&
         coords.y2 == lv_display_get_vertical_resolution(d) - 1 ? image : nullptr;
}

inline bool same_backdrop(lv_display_t *d, lv_obj_t *a, lv_obj_t *b) {
  lv_obj_t *x = backdrop(d, a), *y = backdrop(d, b);
  return x && y && lv_image_get_src(x) == lv_image_get_src(y) &&
         lv_image_get_inner_align(x) == lv_image_get_inner_align(y) &&
         lv_image_get_antialias(x) == lv_image_get_antialias(y);
}

inline void fallback(uint32_t reason) {
  ++state.stats.fallbacks;
  state.stats.last_fallback = reason;
  state.mode = State::kIdle;
}

inline uint32_t elapsed_us(std::chrono::steady_clock::time_point since) {
  return uint32_t(std::chrono::duration_cast<std::chrono::microseconds>(std::chrono::steady_clock::now() - since).count());
}

// The old screen is still active and its widgets are where the display shows them.
inline void unload_start(lv_obj_t *old_screen) {
  lv_display_t *d = state.display;
  if (state.mode != State::kIdle) {
    state.nested = true;
    return;
  }
  if (d->render_mode == LV_DISPLAY_RENDER_MODE_FULL || !backdrop(d, old_screen)) return;
  const auto started = std::chrono::steady_clock::now();
  state.from = old_screen;
  state.to = nullptr;
  state.nested = state.transformed = state.blurred = false;
  state.full_areas = 0;
  state.areas.clear();
  for (uint32_t i = 0; i < d->inv_p; ++i) state.areas.add(d->inv_areas[i]);
  const uint32_t count = lv_obj_get_child_count(old_screen);
  for (uint32_t i = 1; i < count; ++i) collect(lv_obj_get_child(old_screen, int32_t(i)));
  state.mode = State::kArmed;
  state.stats.last_old_us = elapsed_us(started);
}

// Runs at REFR_START, before LVGL updates the layout, joins and synchronizes areas, and renders.
inline void refresh_start() {
  if (state.mode != State::kArmed) return;
  lv_display_t *d = state.display;
  const auto started = std::chrono::steady_clock::now();
  if (state.nested) return fallback(kNested);
  if (d->act_scr != state.to || state.to == state.from || d->scr_to_load || d->prev_scr) return fallback(kScreen);
  lv_obj_update_layout(state.to);  // still kArmed: anything it invalidates is kept
  const uint32_t layout_us = elapsed_us(started);
  if (!same_backdrop(d, state.from, state.to)) return fallback(kBackdrop);
  if (state.full_areas != 1) return fallback(kFullAreas);
  const int budget = budget_cycle_count ? budget_cycle[budget_turn++ % uint32_t(budget_cycle_count)] : kBudget;
  state.stats.last_budget = uint32_t(budget);
  if (budget <= 0) {  // LVGL's full-screen area stays
    state.mode = State::kIdle;
    state.stats.last_layout_us = layout_us;
    return;
  }
  state.mode = State::kIdle;
  const uint32_t count = lv_obj_get_child_count(state.to);
  for (uint32_t i = 1; i < count; ++i) collect(lv_obj_get_child(state.to, int32_t(i)));
  for (lv_obj_t *layer : {d->top_layer, d->sys_layer, d->bottom_layer})
    if (layer && !state.blurred) find_blur(layer);
  if (state.transformed) return fallback(kTransform);  // LVGL's full-screen area is still in its list
  if (state.blurred) return fallback(kBlur);
  state.stats.last_collected = uint32_t(state.areas.n);
  const uint32_t collect_us = elapsed_us(started);
  state.areas.reduce(budget > kBudget ? kBudget : budget);
  state.stats.last_layout_us = layout_us;
  state.stats.last_collect_us = collect_us - layout_us;
  state.stats.last_reduce_us = elapsed_us(started) - collect_us;
  lv_inv_area(d, nullptr);
  int32_t pixels = 0;
  for (int i = 0; i < state.areas.n; ++i) {
    lv_inv_area(d, &state.areas.a[i]);
    pixels += size_of(state.areas.a[i]);
  }
  state.stats.applied++;
  state.stats.last_fallback = kNone;
  state.stats.last_areas = uint32_t(state.areas.n);
  state.stats.last_pixels = uint32_t(pixels);
  state.stats.last_us = elapsed_us(started);
}

inline void on_event(lv_event_t *e) {
  const lv_event_code_t code = lv_event_get_code(e);
  if (code == LV_EVENT_INVALIDATE_AREA) {
    const auto *area = static_cast<const lv_area_t *>(lv_event_get_param(e));
    if (!area || state.mode != State::kArmed) return;
    if (is_full(state.display, *area)) ++state.full_areas;
    else state.areas.add(*area);
  } else if (code == LV_EVENT_SCREEN_UNLOAD_START) {
    unload_start(static_cast<lv_obj_t *>(lv_event_get_param(e)));
  } else if (code == LV_EVENT_SCREEN_LOAD_START) {
    if (state.mode == State::kArmed && !state.to) state.to = static_cast<lv_obj_t *>(lv_event_get_param(e));
    else if (state.mode == State::kArmed) state.nested = true;
  } else if (code == LV_EVENT_REFR_START) {
    refresh_start();
  }
}

inline void install(lv_display_t *d = lv_display_get_default()) {
  if (!d || state.display) return;
  state.display = d;
  for (const auto code : {LV_EVENT_INVALIDATE_AREA, LV_EVENT_SCREEN_UNLOAD_START, LV_EVENT_SCREEN_LOAD_START,
                          LV_EVENT_REFR_START})
    lv_display_add_event_cb(d, on_event, code, nullptr);
}

}  // namespace screen_swap
