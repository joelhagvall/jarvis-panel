// Real LVGL, double-buffered direct mode: a page change through screen_swap.h must show
// exactly the pixels of LVGL's own full-screen redraw, frame after frame.
//
// Usage: screen-swap <mode> <seed> <steps>; mode bit 1: cover_backdrop, bit 2: page-change areas
// Prints one line per refresh, "frame <step> <fnv1a of the presented buffer>", then a JSON summary.
#include "lvgl.h"
#include "screen_swap.h"
#include "src/misc/cache/instance/lv_image_cache.h"
#include <cassert>
#include <chrono>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t size) { return std::malloc(size); }
void lv_free_core(void *ptr) { std::free(ptr); }
void *lv_realloc_core(void *ptr, size_t size) { return std::realloc(ptr, size); }
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *monitor) { std::memset(monitor, 0, sizeof(*monitor)); }
}

constexpr int W = 1024, H = 600;
static uint32_t rng_state;
static uint32_t rnd() {
  rng_state ^= rng_state << 13;
  rng_state ^= rng_state >> 17;
  rng_state ^= rng_state << 5;
  return rng_state;
}
static int rnd(int n) { return int(rnd() % uint32_t(n)); }

static uint32_t tick;
static const uint8_t *presented;
static void flush(lv_display_t *display, const lv_area_t *, uint8_t *) {
  if (lv_display_flush_is_last(display)) presented = lv_display_get_buf_active(display)->data;
  lv_display_flush_ready(display);
}
static uint32_t fnv(const uint8_t *data, size_t size) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < size; ++i) h = (h ^ data[i]) * 16777619u;
  return h;
}

static lv_image_dsc_t make_rgb565(int w, int h, uint32_t seed, std::vector<uint8_t> &store) {
  store.resize(size_t(w) * h * 2);
  auto *px = reinterpret_cast<uint16_t *>(store.data());
  for (int y = 0; y < h; ++y)
    for (int x = 0; x < w; ++x) px[y * w + x] = uint16_t(((x * 7 + y * 3 + seed) ^ (x * y + seed * 31)) & 0xffff);
  lv_image_dsc_t dsc{};
  dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
  dsc.header.cf = LV_COLOR_FORMAT_RGB565;
  dsc.header.w = uint32_t(w);
  dsc.header.h = uint32_t(h);
  dsc.header.stride = uint32_t(w * 2);
  dsc.data_size = uint32_t(store.size());
  dsc.data = store.data();
  return dsc;
}
static lv_image_dsc_t make_icon(std::vector<uint8_t> &store) {
  constexpr int s = 32;
  store.resize(s * s * 4);
  for (int y = 0; y < s; ++y)
    for (int x = 0; x < s; ++x) {
      const int d = (x - 16) * (x - 16) + (y - 16) * (y - 16);
      uint8_t *p = &store[size_t(y * s + x) * 4];
      p[0] = uint8_t(x * 8); p[1] = uint8_t(y * 8); p[2] = 200; p[3] = uint8_t(d > 255 ? 0 : 255 - d);
    }
  lv_image_dsc_t dsc{};
  dsc.header.magic = LV_IMAGE_HEADER_MAGIC;
  dsc.header.cf = LV_COLOR_FORMAT_ARGB8888;
  dsc.header.w = s;
  dsc.header.h = s;
  dsc.header.stride = s * 4;
  dsc.data_size = uint32_t(store.size());
  dsc.data = store.data();
  return dsc;
}

static std::vector<uint8_t> bg_store, other_store, icon_store;
static lv_image_dsc_t bg, other_bg, icon;
static std::vector<lv_obj_t *> mutable_objs[3];
static std::vector<lv_obj_t *> labels[3];

static lv_obj_t *plain(lv_obj_t *parent) {
  lv_obj_t *o = lv_obj_create(parent);
  lv_obj_remove_style_all(o);
  lv_obj_remove_flag(o, LV_OBJ_FLAG_SCROLLABLE);
  return o;
}
static lv_obj_t *label(lv_obj_t *parent, int page, int x, int y, const char *text) {
  lv_obj_t *l = lv_label_create(parent);
  lv_label_set_text(l, text);
  lv_obj_set_pos(l, x, y);
  lv_obj_set_style_text_color(l, lv_color_hex(0xFFFFFF), 0);
  labels[page].push_back(l);
  mutable_objs[page].push_back(l);
  return l;
}

// Representative of the glass pages: text, translucent pills, icons with alpha, a flex row,
// shadows, outlines, transforms, overflow, a layer with opacity and a scrollable list.
static lv_obj_t *page(int index, const lv_image_dsc_t *backdrop) {
  lv_obj_t *s = lv_obj_create(nullptr);
  lv_obj_remove_flag(s, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_set_style_bg_color(s, lv_color_hex(0x0B1020), 0);
  lv_obj_t *img = lv_image_create(s);
  lv_image_set_src(img, backdrop);
  lv_obj_set_pos(img, 0, 0);
  const int o = index * 37;
  label(s, index, 40 + o, 40, index ? "Hemmet" : "onsdag 21 oktober");
  label(s, index, 40, 90 + o, "07:42");
  for (int i = 0; i < 6; ++i) label(s, index, 540 + (i % 2) * 230, 300 + i * 30, i % 2 ? "Akalla 3 min" : "Hjulsta 12 min");

  lv_obj_t *row = plain(s);
  lv_obj_set_pos(row, 40 + o, 200);
  lv_obj_set_size(row, LV_SIZE_CONTENT, 30);
  lv_obj_set_layout(row, LV_LAYOUT_FLEX);
  lv_obj_set_flex_flow(row, LV_FLEX_FLOW_ROW);
  lv_obj_set_style_pad_column(row, 8, 0);
  for (int i = 0; i < 2; ++i) {
    lv_obj_t *pill = plain(row);
    lv_obj_set_size(pill, LV_SIZE_CONTENT, 30);
    lv_obj_set_style_radius(pill, 15, 0);
    lv_obj_set_style_bg_color(pill, lv_color_hex(0xFFFFFF), 0);
    lv_obj_set_style_bg_opa(pill, LV_OPA_20, 0);
    lv_obj_set_style_border_width(pill, 1, 0);
    lv_obj_set_style_border_color(pill, lv_color_hex(0xFFFFFF), 0);
    lv_obj_set_style_border_opa(pill, LV_OPA_30, 0);
    lv_obj_set_style_pad_hor(pill, 12, 0);
    lv_obj_set_style_pad_ver(pill, 6, 0);
    label(pill, index, 0, 0, i ? "Nätet är nere" : "Timer 4:12");
    mutable_objs[index].push_back(pill);
  }

  lv_obj_t *tile = plain(s);  // transparent: draws nothing itself
  lv_obj_set_pos(tile, 520 + o, 24);
  lv_obj_set_size(tile, 230, 230);
  for (int i = 0; i < 3; ++i) {
    lv_obj_t *ic = lv_image_create(tile);
    lv_image_set_src(ic, &icon);
    lv_obj_set_pos(ic, 10 + i * 60, 20 + i * 50);
    mutable_objs[index].push_back(ic);
    label(tile, index, 50 + i * 20, 30 + i * 50, "10°");
  }

  lv_obj_t *shadow = plain(s);
  lv_obj_set_pos(shadow, 300 + o, 420);
  lv_obj_set_size(shadow, 120, 50);
  lv_obj_set_style_bg_opa(shadow, LV_OPA_40, 0);
  lv_obj_set_style_bg_color(shadow, lv_color_hex(0x203040), 0);
  lv_obj_set_style_shadow_width(shadow, 16, 0);
  lv_obj_set_style_shadow_opa(shadow, LV_OPA_60, 0);
  lv_obj_set_style_shadow_offset_y(shadow, 6, 0);
  mutable_objs[index].push_back(shadow);

  lv_obj_t *outline = plain(s);
  lv_obj_set_pos(outline, 40, 470 - o);
  lv_obj_set_size(outline, 80, 40);
  lv_obj_set_style_outline_width(outline, 4, 0);
  lv_obj_set_style_outline_pad(outline, 3, 0);
  lv_obj_set_style_outline_opa(outline, LV_OPA_COVER, 0);
  lv_obj_set_style_outline_color(outline, lv_color_hex(0xFF9F0A), 0);
  mutable_objs[index].push_back(outline);

  lv_obj_t *button = lv_button_create(s);
  lv_obj_set_pos(button, 800, 470 - o);
  lv_obj_set_size(button, 120, 44);
  label(button, index, 10, 10, "Klar");
  mutable_objs[index].push_back(button);

  lv_obj_t *scaled = plain(s);
  lv_obj_set_pos(scaled, 200 + o, 330);
  lv_obj_set_size(scaled, 60, 30);
  lv_obj_set_style_bg_opa(scaled, LV_OPA_70, 0);
  lv_obj_set_style_bg_color(scaled, lv_color_hex(0x30D158), 0);
  lv_obj_set_style_transform_scale(scaled, 330, 0);
  lv_obj_set_style_transform_rotation(scaled, 150, 0);
  if (index < 2) lv_obj_add_flag(scaled, LV_OBJ_FLAG_HIDDEN);  // shown now and then: the full-redraw fallback
  mutable_objs[index].push_back(scaled);

  lv_obj_t *overflow = plain(s);  // a child drawn outside its transparent parent
  lv_obj_set_pos(overflow, 650, 520);
  lv_obj_set_size(overflow, 40, 20);
  lv_obj_add_flag(overflow, LV_OBJ_FLAG_OVERFLOW_VISIBLE);
  label(overflow, index, 30 + o, -40, "utanför");

  lv_obj_t *layer = plain(s);  // a whole subtree at 50% opacity
  lv_obj_set_pos(layer, 760 + o / 2, 120);
  lv_obj_set_size(layer, 200, 80);
  lv_obj_set_style_opa(layer, LV_OPA_50, 0);
  lv_obj_set_style_bg_opa(layer, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(layer, lv_color_hex(0x1C1C1E), 0);
  label(layer, index, 8, 8, "lager");
  mutable_objs[index].push_back(layer);

  lv_obj_t *list = lv_obj_create(s);  // scrollable, default theme style and scrollbar
  lv_obj_set_pos(list, 300, 60 + o);
  lv_obj_set_size(list, 180, 100);
  for (int i = 0; i < 6; ++i) label(list, index, 0, i * 24, "rad i en lista");
  mutable_objs[index].push_back(list);

  lv_obj_t *hidden = plain(s);
  lv_obj_set_pos(hidden, 100, 300);
  lv_obj_set_size(hidden, 90, 60);
  lv_obj_set_style_bg_opa(hidden, LV_OPA_COVER, 0);
  lv_obj_set_style_bg_color(hidden, lv_color_hex(0xFF0000), 0);
  lv_obj_add_flag(hidden, LV_OBJ_FLAG_HIDDEN);
  mutable_objs[index].push_back(hidden);
  return s;
}

static lv_obj_t *tabs[4];
static int selected;
static void select_tab(int index) {
  selected = index;
  for (int i = 0; i < 4; ++i) lv_obj_set_style_bg_opa(tabs[i], i == index ? LV_OPA_20 : LV_OPA_TRANSP, 0);
}

static const char *const words[] = {"Allt lugnt hemma.", "Dörren öppen", "22,3°", "", "Kungsträdgården 4 min",
                                    "sätt igång", "Inga påminnelser", "09:08"};
static void mutate(lv_obj_t *o) {
  switch (rnd(6)) {
    case 0:
      if (lv_obj_check_type(o, &lv_label_class)) lv_label_set_text(o, words[rnd(8)]);
      else lv_obj_set_style_bg_opa(o, lv_opa_t(rnd(256)), 0);
      break;
    case 1: lv_obj_set_pos(o, lv_obj_get_x(o) + rnd(41) - 20, lv_obj_get_y(o) + rnd(41) - 20); break;
    case 2:
      if (lv_obj_has_flag(o, LV_OBJ_FLAG_HIDDEN)) lv_obj_remove_flag(o, LV_OBJ_FLAG_HIDDEN);
      else lv_obj_add_flag(o, LV_OBJ_FLAG_HIDDEN);
      break;
    case 3: lv_obj_set_style_text_color(o, lv_color_hex(rnd()), 0); break;
    case 4: lv_obj_set_width(o, 20 + rnd(200)); break;
    default: lv_obj_set_style_bg_color(o, lv_color_hex(rnd()), 0); break;
  }
}

int main(int argc, char **argv) {
  assert(argc == 4);
  const int mode = std::atoi(argv[1]);
  rng_state = uint32_t(std::strtoul(argv[2], nullptr, 10)) | 1u;
  const int steps = std::atoi(argv[3]);
  lv_init();
  lv_tick_set_cb([]() { return tick; });
  auto *display = lv_display_create(W, H);
  lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
  const size_t bytes = size_t(W) * H * 2;
  void *a = std::aligned_alloc(64, bytes), *b = std::aligned_alloc(64, bytes);
  lv_display_set_buffers(display, a, b, uint32_t(bytes), LV_DISPLAY_RENDER_MODE_DIRECT);
  lv_display_set_flush_cb(display, flush);
  lv_timer_pause(lv_display_get_refr_timer(display));
  if (mode & 2) screen_swap::install(display);

  bg = make_rgb565(W, H, 1, bg_store);
  other_bg = make_rgb565(W, H, 2, other_store);
  icon = make_icon(icon_store);
  lv_obj_t *screens[3] = {page(0, &bg), page(1, &bg), page(2, &other_bg)};
  static long screen_fill_px;
  for (auto *s : screens) {
    if (mode & 1) screen_swap::cover_backdrop(s);
    // A draw-task callback on a screen rightly disables the page-change path, so count only without it.
    if (mode & 2) continue;
    lv_obj_add_flag(s, LV_OBJ_FLAG_SEND_DRAW_TASK_EVENTS);
    lv_obj_add_event_cb(s, [](lv_event_t *e) {  // the screen's own bg fill, under its backdrop
      lv_draw_task_t *t = lv_event_get_draw_task(e);
      lv_area_t a;
      lv_draw_task_get_area(t, &a);
      if (lv_draw_task_get_type(t) == LV_DRAW_TASK_TYPE_FILL) screen_fill_px += long(a.x2 - a.x1 + 1) * (a.y2 - a.y1 + 1);
    }, LV_EVENT_DRAW_TASK_ADDED, nullptr);
  }

  lv_obj_t *bar = plain(lv_layer_top());
  lv_obj_set_size(bar, W, H);
  lv_obj_remove_flag(bar, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_t *strip = plain(bar);
  lv_obj_set_pos(strip, 246, 516);
  lv_obj_set_size(strip, 460, 60);
  for (int i = 0; i < 4; ++i) {
    tabs[i] = plain(strip);
    lv_obj_set_pos(tabs[i], 6 + i * 100, 6);
    lv_obj_set_size(tabs[i], 100, 48);
    lv_obj_set_style_radius(tabs[i], 24, 0);
    lv_obj_set_style_bg_color(tabs[i], lv_color_hex(0xFFFFFF), 0);
    lv_obj_t *t = lv_label_create(tabs[i]);
    lv_label_set_text(t, "flik");
    lv_obj_center(t);
  }
  // Like gen.py's BAR_ON_LOAD: the tab highlight changes inside the screen-load events.
  lv_display_add_event_cb(display, [](lv_event_t *e) {
    auto *s = static_cast<lv_obj_t *>(lv_event_get_param(e));
    for (int i = 0; i < 3; ++i)
      if (s == static_cast<lv_obj_t **>(lv_event_get_user_data(e))[i]) select_tab(i);
  }, LV_EVENT_SCREEN_LOAD_START, screens);

  lv_screen_load(screens[0]);
  int current = 0, swaps = 0;
  double swap_ms = 0;
  bool after_swap = false;
  uint32_t applied_seen = 0;
  double applied_pixels = 0;
  for (int step = 0; step < steps; ++step) {
    ++tick;
    const int op = rnd(12);
    if (op < 3) {  // the common case: the other tab page
      current = current == 0 ? 1 : current == 1 ? 0 : rnd(2);
      lv_screen_load(screens[current]);
      after_swap = true;
    } else if (op == 3) {  // two page changes before one refresh
      lv_screen_load(screens[current == 0 ? 1 : 0]);
      current = rnd(3);
      lv_screen_load(screens[current]);
      after_swap = true;
    } else if (op == 4) {  // a page with another backdrop (Tavla, the voice page)
      current = current == 2 ? rnd(2) : 2;
      lv_screen_load(screens[current]);
      after_swap = true;
    } else if (op == 5) {  // a new background picture, re-pointed on both pages (jarvis_orb.py repaint)
      bg_store[size_t(rnd(W * H)) * 2] ^= 0xff;
      lv_image_cache_drop(&bg);
      for (int i = 0; i < 2; ++i) lv_image_set_src(lv_obj_get_child(screens[i], 0), &bg);
    } else if (op == 6) {
      select_tab(rnd(4));
    } else if (op < 10) {  // a value arrives for any page, shown or not
      const int p = rnd(3);
      for (int k = rnd(4); k >= 0; --k) mutate(mutable_objs[p][size_t(rnd(int(mutable_objs[p].size())))]);
    }
    if (op >= 9 || after_swap) {
      const auto started = std::chrono::steady_clock::now();
      lv_refr_now(display);
      const double ms = std::chrono::duration<double, std::milli>(std::chrono::steady_clock::now() - started).count();
      if (after_swap) { ++swaps; swap_ms += ms; }
      if (screen_swap::state.stats.applied != applied_seen) {
        applied_seen = screen_swap::state.stats.applied;
        applied_pixels += screen_swap::state.stats.last_pixels;
      }
      after_swap = false;
      if (presented) std::printf("frame %d %08x\n", step, fnv(presented, bytes));
    }
  }
  const auto &st = screen_swap::state.stats;
  std::printf("{\"mode\":%d,\"swaps\":%d,\"swap_refresh_ms_mean\":%.3f,\"applied\":%u,\"fallbacks\":%u,"
              "\"last_areas\":%u,\"last_pixels\":%u,\"pixels_per_applied\":%.0f,\"screen_fill_px\":%ld}\n",
              mode, swaps, swaps ? swap_ms / swaps : 0.0, st.applied, st.fallbacks, st.last_areas, st.last_pixels,
              st.applied ? double(applied_pixels) / st.applied : 0.0, screen_fill_px);
}
