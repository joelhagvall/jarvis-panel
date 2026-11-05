// Real LVGL timers with a controlled 17 ms render cost; not hardware throughput.
#include "lvgl.h"
#include "bench/frame_pacing.h"
#include <algorithm>
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
static std::vector<unsigned> intervals;
static unsigned previous;
static bool have_previous;
static int last_value = -1;
int main(int argc, char **argv) {
  if (argc != 2) return 2;
  const unsigned period = unsigned(std::atoi(argv[1]));
  lv_init();
  auto *display = lv_display_create(32, 32);
  static unsigned char pixels[32 * 32 * 4];
  lv_display_set_buffers(display, pixels, nullptr, sizeof(pixels), LV_DISPLAY_RENDER_MODE_FULL);
  lv_display_set_flush_cb(display, [](lv_display_t *disp, const lv_area_t *, uint8_t *) {
    lv_tick_inc(17);  // Controlled render/submission cost from the observed workload.
    lv_display_flush_ready(disp);
    const auto now = lv_tick_get();
    if (have_previous && now > 500) intervals.push_back(now - previous);
    previous = now;
    have_previous = true;
  });
  lv_timer_set_period(lv_display_get_refr_timer(display), 16);
  lv_timer_set_period(lv_anim_get_timer(), 16);
  if (period != 16) frame_pacing_install(period);
  auto *object = lv_screen_active();
  lv_anim_t animation;
  lv_anim_init(&animation);
  lv_anim_set_var(&animation, object);
  lv_anim_set_values(&animation, 0, 49);
  lv_anim_set_duration(&animation, 2000);
  lv_anim_set_repeat_count(&animation, LV_ANIM_REPEAT_INFINITE);
  lv_anim_set_exec_cb(&animation, [](void *obj, int32_t value) {
    if (value != last_value) { lv_obj_invalidate(static_cast<lv_obj_t *>(obj)); last_value = value; }
  });
  lv_anim_start(&animation);
  while (lv_tick_get() < 10500) {
    lv_timer_handler();
    lv_tick_inc(2);  // Controlled non-LVGL component/scheduler work, separately declared.
    lv_tick_inc(esphome::App.interval);
  }
  std::sort(intervals.begin(), intervals.end());
  unsigned late = 0;
  for (auto value : intervals) if (value > 50) ++late;
  std::printf("{\"loop_ms\":%u,\"n\":%zu,\"p95_ms\":%u,\"max_ms\":%u,\"jank_percent\":%.3f}\n",
              esphome::App.interval, intervals.size(), intervals[(intervals.size()*95+99)/100-1], intervals.back(),
              100.0*late/intervals.size());
}
