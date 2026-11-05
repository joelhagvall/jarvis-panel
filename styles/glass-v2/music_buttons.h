#pragma once
#include "lvgl.h"
#include "src/core/lv_obj_private.h"
namespace panel_music_ui {
// LVGL collects transitions from every attached style; a local empty descriptor does not cancel a theme's.
// Detach only shared styles whose sole property is a transition. Keep geometry, colors, pressed feedback,
// disabled/focus states and local styles. Never change the theme object other widgets still reference.
inline unsigned instant_button(lv_obj_t *button) {
  unsigned removed=0;
  for(unsigned i=0;i<button->style_cnt;) {
    const auto binding=button->styles[i];lv_style_value_t value{};
    if(!binding.is_local&&!binding.is_trans&&binding.style->prop_cnt==1&&
       lv_style_get_prop(binding.style,LV_STYLE_TRANSITION,&value)==LV_STYLE_RES_FOUND) {
      const auto before=button->style_cnt;
      lv_obj_remove_style(button,binding.style,binding.selector);
      removed+=before-button->style_cnt;
      if(before==button->style_cnt)++i;
    } else ++i;
  }
  return removed;
}
}
