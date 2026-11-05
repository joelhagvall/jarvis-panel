#pragma once
#ifdef USE_ESP32
#include "src/draw/lv_draw_private.h"
#include "src/core/lv_obj_draw_private.h"
#include "src/core/lv_refr_private.h"
#include "src/display/lv_display_private.h"
namespace panel_vinyl {
// LVGL 9.4's snapshot layer lifecycle, into caller-owned storage and a fixed world-coordinate clip.
// UI owner only, once at setup. No display submission, network query or off-thread LVGL call.
inline bool draw_static(lv_obj_t *object,lv_draw_buf_t &buffer,lv_area_t area) {
  if(!((buffer.header.cf==LV_COLOR_FORMAT_RGB565&&LV_DRAW_SW_SUPPORT_RGB565)||
       (buffer.header.cf==LV_COLOR_FORMAT_ARGB8888&&LV_DRAW_SW_SUPPORT_ARGB8888)))return false;
  if(lv_obj_get_ext_draw_size(object))return false;
  lv_layer_t layer;lv_layer_init(&layer);layer.draw_buf=&buffer;layer.buf_area=area;
  layer.color_format=static_cast<lv_color_format_t>(buffer.header.cf);
  layer._clip_area=area;layer.phy_clip_area=area;
  lv_draw_unit_send_event(nullptr,LV_EVENT_CHILD_CREATED,&layer);
  auto *old_display=lv_refr_get_disp_refreshing(),*display=lv_obj_get_display(object);
  auto *old_layer=display->layer_head;display->layer_head=&layer;lv_refr_set_disp_refreshing(display);
  const bool hidden=lv_obj_has_flag(object,LV_OBJ_FLAG_HIDDEN);
  lv_obj_remove_flag(object,LV_OBJ_FLAG_HIDDEN);lv_obj_redraw(&layer,object);layer.all_tasks_added=true;
  while(layer.draw_task_head){lv_draw_dispatch_wait_for_request();lv_draw_dispatch();}
  if(hidden)lv_obj_add_flag(object,LV_OBJ_FLAG_HIDDEN);
  display->layer_head=old_layer;lv_refr_set_disp_refreshing(old_display);
  lv_draw_unit_send_event(nullptr,LV_EVENT_SCREEN_LOAD_START,&layer);
  lv_draw_unit_send_event(nullptr,LV_EVENT_CHILD_DELETED,&layer);
  return true;
}
}
#endif
