#pragma once
#include <algorithm>
#include <cstring>
#include <cstdint>
#include <string>
#include <vector>
#include "lvgl.h"
namespace panel_music_ui {
// At most one visible label changes per refreshed frame (2026-10-31): at a song change Spelas nu redrew title,
// artist and album (512x90 px) in the frame that also drew the record and the arm, 14-21 ms of render work for a
// 16.6 ms frame, so the record repeated a frame (`responsiveness.slowest`, docs/now-playing.md). A second visible
// change waits for the next REFR_READY; a later value for a waiting label replaces it. Hidden or off-screen labels
// (the Hemmet rows while Spelas nu shows) change at once.
struct Spread {
  struct Item { lv_obj_t *label; std::string value; };
  std::vector<Item> queue;
  uint32_t frame{0}, used{0xFFFFFFFFu};
  lv_obj_t *admitted{nullptr};
  std::string previous, committed;
  void remember(lv_obj_t *label,const char *value) {
    admitted=label; previous=lv_label_get_text(label);committed=value;
  }
  void yield_to(lv_obj_t *urgent) {
    if(!admitted || admitted==urgent || used!=frame)return;
    // No refresh has drawn this admission yet. Restore the old text and queue the latest value, so urgent
    // feedback does not add a second visible label to the record's current frame. Keep any newer queued value.
    if(committed==lv_label_get_text(admitted)) {
      bool queued=false;for(const auto &item:queue)if(item.label==admitted){queued=true;break;}
      if(!queued)queue.push_back({admitted,committed});
      lv_label_set_text(admitted,previous.c_str());
    }
    admitted=nullptr;
  }
  bool replace(lv_obj_t *label,const char *value) {
    for(auto &item:queue)if(item.label==label){item.value=value;return true;}
    return false;
  }
  bool admit(bool visible) {
    if(!visible)return true;
    if(used==frame)return false;
    used=frame;return true;
  }
  // REFR_READY: the next waiting change, skipping any that no longer change anything (`write` false).
  template<class Write> void frame_done(Write write) {
    ++frame;admitted=nullptr;
    while(!queue.empty()) {
      Item item=std::move(queue.front());queue.erase(queue.begin());
      remember(item.label,item.value.c_str());
      if(write(item.label,item.value.c_str())){used=frame;return;}
      admitted=nullptr;
    }
  }
};
inline Spread &spread(){static Spread s;return s;}
inline bool write(lv_obj_t *label,const char *value) {
  if(std::strcmp(lv_label_get_text(label),value)==0)return false;
  lv_label_set_text(label,value);return true;
}
// LVGL invalidates/layouts labels even when copied text is identical. Pending control acknowledgements must
// not redraw the whole metadata column while the record is animating.
inline void text(lv_obj_t *label,const char *value) {
#ifdef USE_ESP32
  static bool hooked=false;
  if(!hooked) {
    hooked=true;
    lv_display_add_event_cb(lv_display_get_default(),[](lv_event_t *){spread().frame_done(write);},
                            LV_EVENT_REFR_READY,nullptr);
  }
  auto &s=spread();
  if(s.replace(label,value))return;
  if(std::strcmp(lv_label_get_text(label),value)==0)return;
  if(!s.admit(lv_obj_is_visible(label))){s.queue.push_back({label,value});return;}
  if(lv_obj_is_visible(label))s.remember(label,value);
#endif
  write(label,value);
}
// A command acknowledgement must not wait behind metadata, or be overwritten by its queued old caption.
// Only the fixed, small status line uses this path; track metadata keeps its one-label-per-frame budget.
inline void urgent_text(lv_obj_t *label,const char *value) {
  auto &s=spread();auto &q=s.queue;
  q.erase(std::remove_if(q.begin(),q.end(),[label](const Spread::Item &i){return i.label==label;}),q.end());
#ifdef USE_ESP32
  if(std::strcmp(lv_label_get_text(label),value)==0)return;
  if(lv_obj_is_visible(label)){s.yield_to(label);s.used=s.frame;}
#endif
  write(label,value);
}
// Compare with the set style, not lv_obj_get_x/width: those are the last layout's coordinates. Two renders in one
// frame (cover off, then on, at a song change) skipped the second move and left the row's text under its cover
// (Joel, 2026-10-29).
inline void x(lv_obj_t *obj,int value){
  if(lv_obj_get_style_x(obj,LV_PART_MAIN)!=value)lv_obj_set_x(obj,value);
}
inline void width(lv_obj_t *obj,int value){
  if(lv_obj_get_style_width(obj,LV_PART_MAIN)!=value)lv_obj_set_width(obj,value);
}
inline void opacity(lv_obj_t *obj,uint8_t value){
  if(lv_obj_get_style_opa(obj,LV_PART_MAIN)!=value)lv_obj_set_style_opa(obj,value,LV_PART_MAIN);
}
}
