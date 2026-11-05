// Run the production chat updater against real LVGL layout and pointer processing.
#include "lvgl.h"
#include "chat_scroll.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>
extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t n) { return std::malloc(n); }
void lv_free_core(void *p) { std::free(p); }
void *lv_realloc_core(void *p, size_t n) { return std::realloc(p, n); }
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *m) { std::memset(m, 0, sizeof(*m)); }
}
class Probe {
 public:
  lv_point_t point_{};
  bool pressed_{};
  lv_indev_t *pointer_device_{};
  void pointer_(int x, int y, bool pressed);
};
// ACTUAL_PANEL_POINTER_IMPL
static int gestures;
static lv_obj_t *plain(lv_obj_t *parent) {
  auto *obj = lv_obj_create(parent);
  lv_obj_remove_style_all(obj);
  lv_obj_remove_flag(obj, LV_OBJ_FLAG_SCROLLABLE);
  return obj;
}
int main() {
  lv_init();
  auto *display = lv_display_create(1024, 600);
  lv_timer_pause(lv_display_get_refr_timer(display));
  auto *page = lv_screen_active();
  lv_obj_remove_flag(page, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_add_event_cb(page, [](lv_event_t *) { ++gestures; }, LV_EVENT_GESTURE, nullptr);
  auto *viewport = plain(page);
  lv_obj_set_pos(viewport, 536, 310);
  lv_obj_set_size(viewport, 444, 126);
  lv_obj_add_flag(viewport, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_remove_flag(viewport, LV_OBJ_FLAG_GESTURE_BUBBLE);
  lv_obj_remove_flag(viewport, LV_OBJ_FLAG_SCROLL_CHAIN_VER);
  lv_obj_remove_flag(viewport, LV_OBJ_FLAG_SCROLL_ELASTIC);
  lv_obj_set_scroll_dir(viewport, LV_DIR_VER);
  auto *content = plain(viewport);
  lv_obj_set_size(content, lv_pct(100), LV_SIZE_CONTENT);
  lv_obj_set_style_min_height(content, lv_pct(100), LV_PART_MAIN);
  lv_obj_set_flex_flow(content, LV_FLEX_FLOW_COLUMN);
  lv_obj_set_flex_align(content, LV_FLEX_ALIGN_END, LV_FLEX_ALIGN_START, LV_FLEX_ALIGN_START);
  auto *row = plain(content);
  lv_obj_set_size(row, lv_pct(100), LV_SIZE_CONTENT);
  auto *label = lv_label_create(row);
  lv_obj_set_width(label, 270);
  lv_label_set_text(label, "");
  lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
  lv_obj_update_layout(viewport);
  std::string full;
  for (int n = 0; n < 24; ++n) full += "En hel mening som ska kunna lasas genom att skrolla. ";
  full += "SLUTET";
  panel_chat::update(viewport, row, label, full);
  assert(std::strcmp(lv_label_get_text(label), full.c_str()) == 0);
  const int end = lv_obj_get_scroll_y(viewport);
  assert(end > 126 && lv_obj_get_scroll_bottom(viewport) <= 1);
  auto *input = lv_indev_create();
  lv_indev_set_type(input, LV_INDEV_TYPE_POINTER);
  lv_indev_set_mode(input, LV_INDEV_MODE_EVENT);
  lv_indev_set_display(input, display);
  Probe probe;
  probe.pointer_device_ = input;
  lv_indev_set_user_data(input, &probe);
  lv_indev_set_read_cb(input, [](lv_indev_t *dev, lv_indev_data_t *data) {
    auto *p = static_cast<Probe *>(lv_indev_get_user_data(dev));
    data->point = p->point_;
    data->state = p->pressed_ ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
  });
  for (int i = 0; i <= 16; ++i) {
    lv_tick_inc(15);
    probe.pointer_(650, 325 + i*5, true);
    lv_timer_handler();
  }
  probe.pointer_(650, 405, false);
  const int reading = lv_obj_get_scroll_y(viewport);
  assert(reading < end);
  assert(gestures == 0 && lv_screen_active() == page);
  // Let momentum settle, then verify incoming text does not pull a reader down.
  lv_obj_scroll_to_y(viewport, end/2, LV_ANIM_OFF);
  const int middle = lv_obj_get_scroll_y(viewport);
  panel_chat::update(viewport, row, label, full + " Ny information.");
  assert(lv_obj_get_scroll_y(viewport) == middle);
  panel_chat::update(viewport, row, label, full + " Ny information.");
  assert(lv_obj_get_scroll_y(viewport) == middle);
  lv_obj_scroll_to_y(viewport, 100000, LV_ANIM_OFF);
  panel_chat::update(viewport, row, label, full + full);
  assert(lv_obj_get_scroll_bottom(viewport) <= 1);
  panel_chat::update(viewport, row, label, "");
  assert(lv_obj_has_flag(row, LV_OBJ_FLAG_HIDDEN));
  panel_chat::update(viewport, row, label, "Kort svar");
  assert(!lv_obj_has_flag(row, LV_OBJ_FLAG_HIDDEN));
  assert(lv_obj_get_scroll_bottom(viewport) <= 0);
  assert(lv_obj_get_y(row) >= 100);  // short chat stays aligned to the bottom
  std::printf("{\"ok\":true,\"native_lvgl\":true,\"full_text_bytes\":%zu,\"end_y\":%d,\"drag_y\":%d,\"page_gestures\":%d}\n", full.size(), end, reading, gestures);
}
