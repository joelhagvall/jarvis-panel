// Real LVGL pointer processing: a drag on a tab must route once and suppress clicks.
#include "lvgl.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>

extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t size) { return std::malloc(size); }
void lv_free_core(void *ptr) { std::free(ptr); }
void *lv_realloc_core(void *ptr, size_t size) { return std::realloc(ptr, size); }
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *monitor) { std::memset(monitor, 0, sizeof(*monitor)); }
}

class Probe {
 public:
  lv_point_t point_{};
  bool pressed_{};
  lv_indev_t *pointer_device_{};
  void pointer_(int x, int y, bool pressed);
};
// ACTUAL_PANEL_POINTER_IMPL
static int gestures, clicks;
static lv_obj_t *next_page;

int main(int argc, char **argv) {
  assert(argc == 3);
  const bool bubble = std::strcmp(argv[1], "1") == 0;
  const bool driven = std::strcmp(argv[2], "1") == 0;
  lv_init();
  auto *display = lv_display_create(1024, 600);
  lv_timer_pause(lv_display_get_refr_timer(display));
  auto *page = lv_screen_active();
  next_page = lv_obj_create(nullptr);
  lv_obj_remove_flag(page, LV_OBJ_FLAG_SCROLLABLE);
  lv_obj_remove_flag(next_page, LV_OBJ_FLAG_SCROLLABLE);
  auto *bar = lv_obj_create(lv_layer_top());
  lv_obj_remove_style_all(bar);
  lv_obj_set_size(bar, 1024, 600);
  lv_obj_remove_flag(bar, LV_OBJ_FLAG_CLICKABLE);
  lv_obj_remove_flag(bar, LV_OBJ_FLAG_SCROLLABLE);
  if (!bubble) lv_obj_remove_flag(bar, LV_OBJ_FLAG_GESTURE_BUBBLE);
  lv_obj_add_event_cb(bar, [](lv_event_t *) {
    if (lv_indev_get_gesture_dir(lv_indev_active()) == LV_DIR_LEFT) {
      ++gestures;
      lv_indev_wait_release(lv_indev_active());
      lv_screen_load(next_page);  // Include the screen change during the drag.
    }
  }, LV_EVENT_GESTURE, nullptr);
  auto *tabs = lv_obj_create(bar);
  lv_obj_remove_style_all(tabs);
  lv_obj_set_pos(tabs, TABS_X, TABS_Y);
  lv_obj_set_size(tabs, 460, 60);
  lv_obj_remove_flag(tabs, LV_OBJ_FLAG_SCROLLABLE);
  for (int i = 0; i < 4; ++i) {
    auto *button = lv_button_create(tabs);
    lv_obj_remove_style_all(button);
    lv_obj_set_pos(button, 6 + i * TAB_WIDTH, 6);
    lv_obj_set_size(button, TAB_WIDTH, 48);
    lv_obj_remove_flag(button, LV_OBJ_FLAG_SCROLLABLE);
    lv_obj_add_event_cb(button, [](lv_event_t *) { ++clicks; }, LV_EVENT_CLICKED, nullptr);
  }
  lv_obj_update_layout(lv_layer_top());
  lv_obj_update_layout(page);
  auto *input = lv_indev_create();
  lv_indev_set_type(input, LV_INDEV_TYPE_POINTER);
  lv_indev_set_mode(input, LV_INDEV_MODE_EVENT);
  lv_indev_set_display(input, display);
  Probe probe;
  probe.pointer_device_ = input;
  lv_indev_set_user_data(input, &probe);
  lv_indev_set_read_cb(input, [](lv_indev_t *device, lv_indev_data_t *data) {
    auto *self = static_cast<Probe *>(lv_indev_get_user_data(device));
    data->point = self->point_;
    data->state = self->pressed_ ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
  });
  const int start = TABS_X + 6 + 3 * TAB_WIDTH + TAB_WIDTH / 2;
  const int finish = TABS_X + 6 + TAB_WIDTH / 2;
  for (int i = 0; i <= 20; ++i) {
    const int x = start + (finish - start) * i / 20;
    lv_tick_inc(15);
    if (driven) probe.pointer_(x, TABS_Y + 30, true);
    else { probe.point_ = {x, TABS_Y + 30}; probe.pressed_ = true; lv_indev_read(input); }
    lv_timer_handler();  // Include the stale periodic read from event mode.
  }
  lv_tick_inc(15);
  if (driven) probe.pointer_(finish, TABS_Y + 30, false);
  else { probe.pressed_ = false; lv_indev_read(input); }
  if (bubble || !driven) {
    assert(gestures == 0 && clicks == 1);  // Reproduce the original false tab click.
  } else {
    assert(gestures == 1 && clicks == 0 && lv_screen_active() == next_page);
  }
  std::printf("{\"gesture_bubble\":%s,\"explicit_pointer\":%s,\"gestures\":%d,\"clicks\":%d}\n",
              bubble ? "true" : "false", driven ? "true" : "false", gestures, clicks);
}
