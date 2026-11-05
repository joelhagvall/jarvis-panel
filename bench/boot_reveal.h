#pragma once
#include "lvgl.h"
#include <cstdint>

// A single refresh replaces the opaque splash with the prepared dashboard.
// Never expose dashboard pixels while splash logo/text remain on top.
namespace boot_reveal {
using Clock = uint32_t (*)();
inline void show(lv_obj_t *cover, uint32_t *ended, Clock clock) {
  lv_obj_add_flag(cover, LV_OBJ_FLAG_HIDDEN);
  *ended = clock();
}
}  // namespace boot_reveal
