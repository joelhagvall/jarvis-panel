#pragma once
// Main UI task only. A bounded latest-value queue for noncritical detail-page data, never alarms/voice/input.
#include <array>
#include <cstdint>
#include <string>
#include <utility>
#ifdef USE_ESP32
#include "esphome/components/panel_debug/psram_string.h"
#endif
namespace ui_priority {
#ifdef USE_ESP32
using Value=panel_debug_memory::String;
#else
using Value=std::string;
#endif
struct Stats {uint32_t enqueued{},applied{},replaced{},deferred{},pending{},max_wait_ms{};};
struct Input {
  bool seen{},awaiting_frame{};uint32_t at{};
  void mark(uint32_t now){seen=awaiting_frame=true;at=now;}
  void frame(){awaiting_frame=false;}
  bool blocked(uint32_t now,bool pressed)const{
    if(pressed)return true;
    if(!seen)return false;
    const uint32_t age=now-at;
    return age<12 || (awaiting_frame&&age<120); // A blank/cancelled tap cannot starve updates forever.
  }
};
template<unsigned N> struct Queue {
  struct Slot {Value value;bool pending{};uint32_t at{};void(*render)(const std::string&){};};
  std::array<Slot,N> slots;Input input;Stats stats;bool draining{};
  void submit(unsigned index,const std::string &value,uint32_t now){
    auto &s=slots.at(index);++stats.enqueued;
    if(s.pending)++stats.replaced;
    else {s.pending=true;s.at=now;++stats.pending;}
    s.value.assign(value.data(),value.size());
  }
  bool tick(uint32_t now,bool pressed){
    if(draining||!stats.pending)return false;
    // One main-loop/input pass before starting data work, even when no touch has been seen yet.
    bool settling=false;for(const auto &s:slots)if(s.pending&&(uint32_t(now-s.at)<8||!s.render))settling=true;
    if(settling||input.blocked(now,pressed)){++stats.deferred;return false;}
    draining=true;
    std::array<Value,N> values;std::array<bool,N> ready{};
    for(unsigned i=0;i<N;++i)if(slots[i].pending){
      auto &s=slots[i];values[i]=std::move(s.value);ready[i]=true;s.pending=false;--stats.pending;
      const uint32_t age=now-s.at;if(age>stats.max_wait_ms)stats.max_wait_ms=age;
    }
    // All fields already pending are applied together before the next refresh, preserving related values.
    for(unsigned i=0;i<N;++i)if(ready[i]){
      if(slots[i].render)slots[i].render(std::string(values[i].data(),values[i].size()));
      ++stats.applied;
    }
    draining=false;return true;
  }
};
inline Queue<8> queue;
}
