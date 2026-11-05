#pragma once
// Cold, zero-initialised state goes to PSRAM's .bss (CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY): internal RAM
// was down to ~100 kB free on 2026-11-03 (docs/memory.md). Empty on host builds.
#if __has_include("esp_attr.h")
#include "esp_attr.h"
#endif
#ifndef EXT_RAM_BSS_ATTR
#define EXT_RAM_BSS_ATTR
#endif
#define HALLPANEL_DETAIL_LOAD 1
#include <cstdint>
namespace panel_detail_load {
enum class Mode { NONE, WEATHER, CALENDAR, AI };
struct Fixture {
  Mode mode{Mode::NONE};
  bool active{false};
  uint32_t started{0}, steps{0};
  void (*apply)(unsigned){nullptr};
  void (*revert)(){nullptr};
  bool (*unchanged)(){nullptr};
  void begin(Mode which,uint32_t now,void (*update)(unsigned),void (*restore)(),bool (*owned)()) {
    mode=which;started=now;steps=0;apply=update;revert=restore;unchanged=owned;active=true;apply(0);
  }
  void restore() {
    if(!active)return;
    active=false;mode=Mode::NONE;revert();  // renderer reads the latest real HA values, not an old snapshot
  }
  void tick(uint32_t now) {
    if(!active)return;
    if(!unchanged()){restore();return;}
    const uint32_t next=(now-started)/600;
    if(next!=steps){steps=next;apply(steps);}
  }
};
inline Fixture fixture EXT_RAM_BSS_ATTR;
}
