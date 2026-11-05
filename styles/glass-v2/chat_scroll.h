#pragma once
#include "lvgl.h"
#include <algorithm>
#include <cstring>
#include <string>
#include <vector>

namespace panel_chat {
inline void update(lv_obj_t *viewport, lv_obj_t *row, lv_obj_t *label, const std::string &text) {
  const bool hidden = lv_obj_has_flag(row, LV_OBJ_FLAG_HIDDEN);
  if (std::strcmp(lv_label_get_text(label), text.c_str()) == 0 && hidden == text.empty()) return;
  // Follow new text only while the reader is already at the end of the chat.
  const bool follow = lv_obj_get_scroll_bottom(viewport) <= 12;
  lv_label_set_text(label, text.c_str());
  if (text.empty()) lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
  else lv_obj_remove_flag(row, LV_OBJ_FLAG_HIDDEN);
  lv_obj_update_layout(viewport);
  if (follow) {
    lv_obj_scroll_to_y(viewport, lv_obj_get_scroll_y(viewport) + lv_obj_get_scroll_bottom(viewport), LV_ANIM_OFF);
  }
}
// The bubbles before the newest question (ha/.../chat_view.py): messages separated by 0x1f, each 'u' (Joel, blue,
// right) or 'j' (Jarvis, dark, left) and its text. labels[i] sits in a bubble in a row (gen.py bubble()); unused
// rows are hidden. Like update(), the chat follows new text only while the reader is at its end.
inline void earlier(lv_obj_t *viewport, lv_obj_t *const *labels, int n, const std::string &text) {
  std::vector<std::string> items;
  for (size_t start = 0; !text.empty() && start <= text.size();) {
    const size_t end = std::min(text.find('\x1f', start), text.size());
    if (end > start + 1) items.push_back(text.substr(start, end - start));
    start = end + 1;
  }
  const int skip = std::max(0, int(items.size()) - n);
  const bool follow = lv_obj_get_scroll_bottom(viewport) <= 12;
  bool changed = false;
  for (int i = 0; i < n; ++i) {
    lv_obj_t *label = labels[i], *bubble = lv_obj_get_parent(label), *row = lv_obj_get_parent(bubble);
    const int k = skip + i;
    const bool shown = k < int(items.size());
    const char *body = shown ? items[k].c_str() + 1 : "";
    const bool you = shown && items[k][0] == 'u';
    if (std::strcmp(lv_label_get_text(label), body) != 0) { lv_label_set_text(label, body); changed = true; }
    if (shown == lv_obj_has_flag(row, LV_OBJ_FLAG_HIDDEN)) {
      if (shown) lv_obj_remove_flag(row, LV_OBJ_FLAG_HIDDEN); else lv_obj_add_flag(row, LV_OBJ_FLAG_HIDDEN);
      changed = true;
    }
    if (!shown) continue;
    lv_obj_set_style_bg_color(bubble, lv_color_hex(you ? 0x0A84FF : 0x1C1C1E), LV_PART_MAIN);
    lv_obj_set_style_bg_opa(bubble, you ? LV_OPA_COVER : 235, LV_PART_MAIN);
    lv_obj_set_style_flex_main_place(row, you ? LV_FLEX_ALIGN_END : LV_FLEX_ALIGN_START, LV_PART_MAIN);
  }
  if (!changed) return;
  lv_obj_update_layout(viewport);
  if (follow) {
    lv_obj_scroll_to_y(viewport, lv_obj_get_scroll_y(viewport) + lv_obj_get_scroll_bottom(viewport), LV_ANIM_OFF);
  }
}
// No bubble shows: every row in the viewport's column is hidden (the Jarvis card's Härnäst may show then).
inline bool empty(lv_obj_t *viewport) {
  lv_obj_t *column = lv_obj_get_child(viewport, 0);
  if (column == nullptr) return true;
  for (uint32_t i = 0; i < lv_obj_get_child_count(column); ++i)
    if (!lv_obj_has_flag(lv_obj_get_child(column, i), LV_OBJ_FLAG_HIDDEN)) return false;
  return true;
}
}  // namespace panel_chat
