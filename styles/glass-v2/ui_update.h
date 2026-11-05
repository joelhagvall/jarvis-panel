#pragma once
#include <cstring>
#include <cstdlib>
#include <initializer_list>
#include <new>
#include "screen_swap.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#endif
#include "lvgl.h"
namespace ui_update {
class Batch;
inline Batch *current_batch=nullptr;
struct Damage {
  screen_swap::Areas areas;
  lv_obj_t *objects[128]{};
  unsigned count{};
};
inline Damage *damage_storage() {
  static Damage *value=[](){
#ifdef USE_ESP32
    void *p=heap_caps_malloc(sizeof(Damage),MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT);
#else
    void *p=std::malloc(sizeof(Damage));
#endif
    return p ? new(p) Damage{} : nullptr;
  }();
  return value;
}
// Only for a known, bounded subtree without blur/overflow effects. Reserve every final/old drawing
// region first, retain existing invalidations, then settle its synchronous layout without invalidating
// intermediate size/position combinations. No refresh or event-loop yield occurs inside this scope.
class Batch {
  lv_obj_t *root_;lv_display_t *display_;bool active_;
  Damage *damage_{};bool overflow_{};
  lv_area_t fallback_[8]{};unsigned fallback_count_{};
  void area(lv_obj_t *o){
    lv_area_t a;lv_obj_get_coords(o,&a);const auto ext=lv_obj_get_ext_draw_size(o);
    lv_area_increase(&a,ext,ext);
    if(lv_obj_area_is_visible(o,&a))damage_->areas.add(a);
  }
public:
  Batch(lv_obj_t *root,std::initializer_list<lv_area_t> areas):root_(root),display_(lv_obj_get_display(root)),
    active_(display_&&lv_display_is_invalidation_enabled(display_)&&lv_obj_is_visible(root)) {
    if(active_){
      for(const auto &a:areas){
        if(fallback_count_==8){lv_obj_get_coords(root_,&fallback_[0]);fallback_count_=1;break;}
        fallback_[fallback_count_++]=a;
      }
      lv_obj_update_layout(root_); // Preserve pending layout damage from unrelated updates before this scope.
      damage_=damage_storage();
      if(damage_){damage_->count=0;damage_->areas.clear();}
      current_batch=this;
      lv_display_enable_invalidation(display_,false);
    }
  }
  void track(lv_obj_t *o){
    if(!active_||!damage_)return;
    // A content-sized flex row moves unchanged siblings too (high/low forecast temperatures).
    auto *parent=lv_obj_get_parent(o);
    if(parent&&lv_obj_get_style_layout(parent,LV_PART_MAIN)!=LV_LAYOUT_NONE)o=parent;
    for(unsigned i=0;i<damage_->count;++i)if(damage_->objects[i]==o)return;
    if(damage_->count==128){overflow_=true;return;}
    damage_->objects[damage_->count++]=o;area(o);
  }
  ~Batch(){if(active_){
    lv_obj_update_layout(root_);
    if(damage_)for(unsigned i=0;i<damage_->count;++i)area(damage_->objects[i]);
    current_batch=nullptr;lv_display_enable_invalidation(display_,true);
    if(!damage_||overflow_){for(unsigned i=0;i<fallback_count_;++i)lv_obj_invalidate_area(root_,&fallback_[i]);}
    else {
      damage_->areas.premerge();damage_->areas.reduce(screen_swap::kBudget);
      for(int i=0;i<damage_->areas.n;++i)lv_obj_invalidate_area(root_,&damage_->areas.a[i]);
    }
  }}
  Batch(const Batch&)=delete;Batch& operator=(const Batch&)=delete;
};
inline void changed(lv_obj_t *o){if(current_batch)current_batch->track(o);}
// Atomic data updates on static pages. Skip unchanged LVGL writes; do not defer related fields across frames.
inline void text(lv_obj_t *o,const char *s){if(std::strcmp(lv_label_get_text(o),s)){changed(o);lv_label_set_text(o,s);}}
inline void visible(lv_obj_t *o,bool on){
  if(on&&lv_obj_has_flag(o,LV_OBJ_FLAG_HIDDEN)){changed(o);lv_obj_remove_flag(o,LV_OBJ_FLAG_HIDDEN);}
  else if(!on&&!lv_obj_has_flag(o,LV_OBJ_FLAG_HIDDEN)){changed(o);lv_obj_add_flag(o,LV_OBJ_FLAG_HIDDEN);}
}
inline void x(lv_obj_t *o,int v){if(lv_obj_get_style_x(o,LV_PART_MAIN)!=v){changed(o);lv_obj_set_x(o,v);}}
inline void y(lv_obj_t *o,int v){if(lv_obj_get_style_y(o,LV_PART_MAIN)!=v){changed(o);lv_obj_set_y(o,v);}}
inline void width(lv_obj_t *o,int v){if(lv_obj_get_style_width(o,LV_PART_MAIN)!=v){changed(o);lv_obj_set_width(o,v);}}
inline void height(lv_obj_t *o,int v){if(lv_obj_get_style_height(o,LV_PART_MAIN)!=v){changed(o);lv_obj_set_height(o,v);}}
inline void text_opa(lv_obj_t *o,lv_opa_t v,lv_style_selector_t p){if(lv_obj_get_style_text_opa(o,static_cast<lv_part_t>(p))!=v){changed(o);lv_obj_set_style_text_opa(o,v,p);}}
inline void opacity(lv_obj_t *o,lv_opa_t v){if(lv_obj_get_style_opa(o,LV_PART_MAIN)!=v){changed(o);lv_obj_set_style_opa(o,v,LV_PART_MAIN);}}
inline void bg_color(lv_obj_t *o,lv_color_t v,lv_style_selector_t p){if(!lv_color_eq(lv_obj_get_style_bg_color(o,static_cast<lv_part_t>(p)),v)){changed(o);lv_obj_set_style_bg_color(o,v,p);}}
inline void text_color(lv_obj_t *o,lv_color_t v,lv_style_selector_t p){if(!lv_color_eq(lv_obj_get_style_text_color(o,static_cast<lv_part_t>(p)),v)){changed(o);lv_obj_set_style_text_color(o,v,p);}}
}
