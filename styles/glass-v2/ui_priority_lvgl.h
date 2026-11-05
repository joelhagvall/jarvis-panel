#pragma once
#include "ui_priority.h"
#include "lvgl.h"
#include "esphome/core/hal.h"
namespace ui_priority {
inline bool pointer_pressed(){
  for(auto *p=lv_indev_get_next(nullptr);p;p=lv_indev_get_next(p))
    if(lv_indev_get_type(p)==LV_INDEV_TYPE_POINTER&&lv_indev_get_state(p)==LV_INDEV_STATE_PRESSED)return true;
  return false;
}
inline void install(){
  for(auto *p=lv_indev_get_next(nullptr);p;p=lv_indev_get_next(p)){
    if(lv_indev_get_type(p)!=LV_INDEV_TYPE_POINTER)continue;
    for(auto code:{LV_EVENT_PRESSED,LV_EVENT_RELEASED})lv_indev_add_event_cb(p,[](lv_event_t*){
      queue.input.mark(esphome::millis());
    },code,nullptr);
  }
  static bool drew=false;
  for(auto code:{LV_EVENT_REFR_START,LV_EVENT_RENDER_READY,LV_EVENT_REFR_READY})
    lv_display_add_event_cb(lv_display_get_default(),[](lv_event_t *e){
      auto code=lv_event_get_code(e);
      if(code==LV_EVENT_REFR_START)drew=false;
      else if(code==LV_EVENT_RENDER_READY)drew=true;
      else if(drew)queue.input.frame();
    },code,nullptr);
}
}
