// Actual LVGL pointer events executing the generated production slider lambdas.
#include "lvgl.h"
#include "slider_detents.h"
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <string>

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
static lv_obj_t *slider, *value_label;
static int shown, saved = -1, changes, commits;
static bool checking_drag;

static void changed(lv_event_t *) {
  float x = lv_slider_get_value(slider);
  // ACTUAL_ON_VALUE
  ++changes;
  if (checking_drag && lv_slider_is_dragged(slider)) {
    assert(shown == SLIDER_LOW || shown % 5 == 0);
    assert(shown == lv_slider_get_value(slider));
    assert(std::stoi(lv_label_get_text(value_label)) == shown);
  }
}
static void released(lv_event_t *) {
  float x = lv_slider_get_value(slider);
  // ACTUAL_ON_RELEASE
  saved = shown;  // board.py's *_set scripts consume exactly this global.
  ++commits;
}

int main() {
  lv_init();
  auto *display = lv_display_create(1024, 600);
  lv_timer_pause(lv_display_get_refr_timer(display));
  auto *page = lv_screen_active();
  lv_obj_remove_flag(page, LV_OBJ_FLAG_SCROLLABLE);
  slider = lv_slider_create(page);
  lv_obj_remove_style_all(slider);
  lv_obj_set_pos(slider, 235, 174);
  lv_obj_set_size(slider, SLIDER_WIDTH, 8);
  lv_obj_set_style_pad_all(slider, 0, LV_PART_MAIN);
  lv_obj_set_style_pad_all(slider, 10, LV_PART_KNOB);
  lv_slider_set_range(slider, SLIDER_LOW, 100);
  value_label = lv_label_create(page);
  lv_obj_set_pos(value_label, 800, 100);
  lv_obj_add_event_cb(slider, changed, LV_EVENT_VALUE_CHANGED, nullptr);
  lv_obj_add_event_cb(slider, released, LV_EVENT_RELEASED, nullptr);
  lv_obj_update_layout(page);
  // Syncing settings/HA must preserve previously saved non-step values.
  lv_slider_set_value(slider, 12, LV_ANIM_OFF);
  lv_obj_send_event(slider, LV_EVENT_VALUE_CHANGED, nullptr);
  assert(shown == 12 && lv_slider_get_value(slider) == 12);
  assert(commits == 0);
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
  auto coordinate = [](int value) {
    return 235 + ((value - SLIDER_LOW) * (SLIDER_WIDTH - 1) + (100-SLIDER_LOW)/2) / (100-SLIDER_LOW);
  };
  const int targets[] = {48, 49, 50, 51, 52, 47, 53, SLIDER_LOW, 100};
  const int expected[] = {50, 50, 50, 50, 50, 45, 55, SLIDER_LOW, 100};
  for (unsigned test = 0; test < sizeof(targets)/sizeof(targets[0]); ++test) {
    lv_slider_set_value(slider, 20, LV_ANIM_OFF);
    lv_obj_send_event(slider, LV_EVENT_VALUE_CHANGED, nullptr);
    const int start = coordinate(20), finish = coordinate(targets[test]);
    const int before = commits;
    checking_drag = true;
    for (int i = 0; i <= 20; ++i) {
      lv_tick_inc(15);
      probe.pointer_(start + (finish-start)*i/20, 178, true);
      lv_timer_handler();
    }
    lv_tick_inc(15);
    probe.pointer_(finish, 178, false);
    checking_drag = false;
    assert(saved == expected[test]);
    assert(lv_slider_get_value(slider) == saved);
    assert(std::stoi(lv_label_get_text(value_label)) == saved);
    assert(commits == before + 1);
  }
  assert(changes > 9);
  std::printf("{\"ok\":true,\"minimum\":%d,\"pointer_drags\":9,\"restored_12_preserved\":true,\"commits\":%d}\n",
              SLIDER_LOW, commits);
}
