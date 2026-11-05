#pragma once
// The reply under the orb: all of it, in the biggest font where it fits the box. A reply too long even for
// the smallest font is never cut (Joel, 2026-11-01): it shows in the scroll font and rolls up line by line in
// step with Jarvis's voice, keeping the line he is saying second from the top. The box is the label's parent
// (jarvis_orb.py's reply_box), clipped and not touchable, so a tap still reaches the voice page.
#include "lvgl.h"
#include <algorithm>
#include <cstdio>
#include <string>
#if __has_include("esphome/core/log.h")
#include "esphome/core/log.h"
#define PANEL_REPLY_LOG(...) ESP_LOGI("panel_reply", __VA_ARGS__)
#else
#define PANEL_REPLY_LOG(fmt, ...) std::printf(fmt "\n", ##__VA_ARGS__)
#endif

namespace panel_reply {

inline bool fits(const std::string &t, const lv_font_t *font, int32_t width, int32_t height) {
  lv_point_t size;
  lv_text_get_size(&size, t.c_str(), font, 0, 0, width, LV_TEXT_FLAG_NONE);
  return size.y <= height;
}

// One paragraph: newlines become spaces and runs of spaces become one.
inline std::string paragraph(const std::string &text) {
  std::string t;
  for (char ch : text) {
    if (ch == '\n') ch = ' ';
    if (ch == ' ' && (t.empty() || t.back() == ' ')) continue;
    t += ch;
  }
  while (!t.empty() && t.back() == ' ') t.pop_back();
  return t;
}

// The top line to show `elapsed_ms` into a reply of `lines` lines spoken over `speech_ms`: the line being said
// second from the top, never past the last screenful. lead_ms is the time before the voice starts.
inline int32_t follow_top(uint32_t elapsed_ms, uint32_t speech_ms, uint32_t lead_ms, int32_t lines, int32_t visible) {
  int32_t last = std::max<int32_t>(0, lines - visible);
  if (elapsed_ms <= lead_ms || speech_ms == 0) return 0;
  int64_t said = (int64_t) (elapsed_ms - lead_ms) * lines / speech_ms;   // the line being said now
  return (int32_t) std::min<int64_t>(last, std::max<int64_t>(0, said - 1));
}

// Jarvis's speaking pace, measured 2026-11-01 from 23 cached Mattias replies: median 11.3 characters a second
// (p10 9.6, p90 13.1); rerun with tools/speech_rate.py. HA's TTS cache on Jarvis names each file
// sha1(text)_..._tts.jarvis_mattias.wav, so hashing history.db's replies finds their audio for ffprobe.
// The voice starts about half a second after the text arrives. Each roll logs a "panel_reply" line
// (tools/panel logs).
constexpr uint32_t CHARS_PER_S = 11, LEAD_MS = 600, STEP_MS = 250;

struct Follow {
  lv_obj_t *box = nullptr;
  lv_timer_t *timer = nullptr;
  uint32_t start = 0, speech_ms = 0;
  int32_t line_h = 0, lines = 0, visible = 0, top = 0, max_y = 0;
};
inline Follow &follow() {
  static Follow f;
  return f;
}

inline void stop() {
  Follow &f = follow();
  if (f.timer) lv_timer_delete(f.timer);
  f.timer = nullptr;
}

inline void step(lv_timer_t *) {
  Follow &f = follow();
  int32_t top = follow_top(lv_tick_elaps(f.start), f.speech_ms, LEAD_MS, f.lines, f.visible);
  if (top != f.top) {
    f.top = top;
    lv_obj_scroll_to_y(f.box, std::min(top * f.line_h, f.max_y), LV_ANIM_ON);
  }
  if (top >= f.lines - f.visible) stop();
}

// The speech is over: show the end, in case the voice ran ahead of the estimate.
inline void finish() {
  Follow &f = follow();
  if (!f.timer || !f.box) return;
  stop();
  f.top = f.lines - f.visible;
  lv_obj_scroll_to_y(f.box, f.max_y, LV_ANIM_ON);
}

// Starts rolling the label's box when the label is taller than it; else leaves it at the top.
inline void roll(lv_obj_t *label, size_t chars) {
  stop();
  Follow &f = follow();
  f.box = lv_obj_get_parent(label);
  lv_obj_scroll_to_y(f.box, 0, LV_ANIM_OFF);
  lv_obj_update_layout(f.box);
  const lv_font_t *font = lv_obj_get_style_text_font(label, LV_PART_MAIN);
  f.line_h = lv_font_get_line_height(font) + lv_obj_get_style_text_line_space(label, LV_PART_MAIN);
  int32_t label_h = lv_obj_get_height(label), box_h = lv_obj_get_content_height(f.box);
  if (f.line_h <= 0 || label_h <= box_h) return;
  f.lines = (label_h + f.line_h - 1) / f.line_h;
  f.visible = std::max<int32_t>(1, box_h / f.line_h);
  f.max_y = label_h - box_h;
  f.top = 0;
  f.speech_ms = (uint32_t) (chars * 1000 / CHARS_PER_S);
  f.start = lv_tick_get();
  f.timer = lv_timer_create(step, STEP_MS, nullptr);
  PANEL_REPLY_LOG("%u chars, %d lines, %d visible: rolling over %u ms", (unsigned) chars, (int) f.lines,
                  (int) f.visible, (unsigned) f.speech_ms);
}

// Sets the label's font and text: the first of fonts (biggest first) that holds all of text in width x height.
// When none does, fonts[scroll_font] shows all of it and the box rolls with the voice.
inline void show(lv_obj_t *label, const std::string &text, const lv_font_t *const *fonts, size_t count,
                 int32_t width, int32_t height, size_t scroll_font) {
  std::string t = paragraph(text);
  size_t i = 0;
  while (i + 1 < count && !fits(t, fonts[i], width, height)) i++;
  if (!fits(t, fonts[i], width, height)) i = std::min(scroll_font, count - 1);
  lv_obj_set_style_text_font(label, fonts[i], LV_PART_MAIN);
  lv_label_set_text(label, t.c_str());
  roll(label, t.size());
}

// Your sentence over the reply: all of it, in big (lines of it, max_h tall at most) when it fits, else in
// small with as many lines as max_h holds (Joel, 2026-11-01: a long question ended in "…"). Only a sentence
// too long even for that still ends in "…" (the label's DOT).
inline void show_you(lv_obj_t *label, const lv_font_t *big, const lv_font_t *small, int32_t width, int32_t max_h,
                     int32_t big_lines) {
  std::string t = lv_label_get_text(label);
  int32_t big_h = std::min(max_h, big_lines * lv_font_get_line_height(big));
  const lv_font_t *font = fits(t, big, width, big_h) ? big : small;
  int32_t line = std::max<int32_t>(1, lv_font_get_line_height(font));
  int32_t h = font == big ? big_h : std::max(line, max_h / line * line);
  lv_obj_set_style_text_font(label, font, LV_PART_MAIN);
  lv_obj_set_height(label, h);
}

}  // namespace panel_reply
