#pragma once
// Cold, zero-initialised state goes to PSRAM's .bss (CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY): internal RAM
// was down to ~100 kB free on 2026-11-03 (docs/memory.md). Empty on host builds.
#if __has_include("esp_attr.h")
#include "esp_attr.h"
#endif
#ifndef EXT_RAM_BSS_ATTR
#define EXT_RAM_BSS_ATTR
#endif
#define HALLPANEL_MUSIC_LOAD 1
#include <array>
#include <string>
#include "music_ui.h"
#include "now_playing.h"

namespace panel_music_load {
// Bounded, silent diagnostic. It never writes live music state, calls HA or changes audio/settings.
// Restoring a label requires that it still contains our last injected value.
struct Fixture {
  std::array<lv_obj_t *, 4> labels{};
  std::array<std::string, 4> saved{}, injected{}, prior{};
  std::array<bool, 4> hidden{};
  lv_obj_t *group{}, *empty{};
  bool group_hidden{}, empty_hidden{}, active{false};
  uint32_t started{}, step{0}, covers{0};
  std::array<uint16_t, 256> pixels{};
  lv_image_dsc_t image{};
  void (*source)(const lv_image_dsc_t *, uint64_t){nullptr};
  panel_music::Pending feedback;
  panel_music::State live;
  std::string live_album, live_art;
  bool unchanged() const {
    const auto &now=panel_music::state;
    return now.state==live.state&&now.source==live.source&&now.title==live.title&&now.artist==live.artist&&
      now.place==live.place&&now.remote==live.remote&&panel_music::album==live_album&&
      panel_music::artwork.wanted==live_art;
  }
  void text(unsigned i, const std::string &value, bool urgent=false) {
    prior[i]=injected[i];
    injected[i]=value;
    if(urgent) panel_music_ui::urgent_text(labels[i],value.c_str());
    else panel_music_ui::text(labels[i],value.c_str());
  }
  void begin(std::array<lv_obj_t *, 4> items, lv_obj_t *playing, lv_obj_t *idle, uint32_t now,
             void (*publish)(const lv_image_dsc_t *, uint64_t)) {
    labels=items; group=playing; empty=idle; started=now; step=covers=0;source=publish;
    feedback=panel_music::Pending{};
    live=panel_music::state;live_album=panel_music::album;live_art=panel_music::artwork.wanted;
    group_hidden=lv_obj_has_flag(group,LV_OBJ_FLAG_HIDDEN);
    empty_hidden=lv_obj_has_flag(empty,LV_OBJ_FLAG_HIDDEN);
    for(unsigned i=0;i<labels.size();++i) {
      saved[i]=injected[i]=prior[i]=lv_label_get_text(labels[i]);
      hidden[i]=lv_obj_has_flag(labels[i],LV_OBJ_FLAG_HIDDEN);
      lv_obj_remove_flag(labels[i],LV_OBJ_FLAG_HIDDEN);
    }
    lv_obj_remove_flag(group,LV_OBJ_FLAG_HIDDEN); lv_obj_add_flag(empty,LV_OBJ_FLAG_HIDDEN);
    image.header.magic=LV_IMAGE_HEADER_MAGIC; image.header.cf=LV_COLOR_FORMAT_RGB565;
    image.header.w=image.header.h=16; image.header.stride=32;
    image.data_size=sizeof(pixels); image.data=reinterpret_cast<const uint8_t *>(pixels.data());
    active=true;
  }
  bool owns(unsigned i) const {
    if(injected[i]==lv_label_get_text(labels[i]))return true;
    if(prior[i]!=lv_label_get_text(labels[i]))return false;
    for(const auto &item:panel_music_ui::spread().queue)
      if(item.label==labels[i]&&item.value==injected[i])return true;
    return false;
  }
  bool owns_labels() const {
    for(unsigned i=0;i<labels.size();++i)if(!owns(i))return false;
    return true;
  }
  void restore() {
    if(!active)return;
    const bool owned=owns_labels();
    for(unsigned i=0;i<labels.size();++i) {
      const bool owned_label=owns(i);
      // Also cancel our queued metadata so it cannot reappear after restoration.
      auto &q=panel_music_ui::spread().queue;
      q.erase(std::remove_if(q.begin(),q.end(),[&](const panel_music_ui::Spread::Item &item){
        return item.label==labels[i]&&item.value==injected[i];
      }),q.end());
      if(owned_label) {
        panel_music_ui::urgent_text(labels[i],saved[i].c_str());
        if(hidden[i])lv_obj_add_flag(labels[i],LV_OBJ_FLAG_HIDDEN);
      }
    }
    if(owned) {
      if(group_hidden)lv_obj_add_flag(group,LV_OBJ_FLAG_HIDDEN);
      if(!empty_hidden)lv_obj_remove_flag(empty,LV_OBJ_FLAG_HIDDEN);
    }
    active=false;
  }
  void tick(uint32_t now) {
    if(!active)return;
    if(!unchanged()) { restore(); return; }  // actual metadata, not an unchanged UI resync, preempts diagnostics
    const uint32_t elapsed=now-started, next=elapsed/600;
    if(next==step)return;
    step=next;
    // Three real metadata labels arrive together; the normal frame budget spreads their drawing.
    text(1, step%2 ? "En längre låttitel som upptar två rader på panelen" : "Kort låttitel");
    text(2,"Artist "+std::to_string(step)); text(3,"Album "+std::to_string(step));
    panel_music::State state; state.state="playing";
    if(step%5==1) feedback.begin(panel_music::Pending::Action::TOGGLE,state,0,now);
    else if(step%5==2) feedback.acknowledge(feedback.ticket,now);
    else if(step%5==3) { state.state="paused";feedback.observe(state,0,now); }
    else if(step%5==4) {
      feedback.begin(panel_music::Pending::Action::NEXT,state,0,now); feedback.fail(feedback.ticket,now);
    } else {
      feedback.begin(panel_music::Pending::Action::NEXT,state,0,now); feedback.tick(now+8000);
    }
    text(0,feedback.message(),true);
    if(step%5==0) {
      for(unsigned i=0;i<pixels.size();++i) pixels[i]=uint16_t((i*137+step*503)&0xffff);
      source(&image,0xF0000000ull+step);
      ++covers;
    }
  }
};
inline Fixture fixture EXT_RAM_BSS_ATTR;
}
