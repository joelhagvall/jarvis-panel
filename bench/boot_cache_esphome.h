#pragma once
// Cold, zero-initialised state goes to PSRAM's .bss (CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY): internal RAM
// was down to ~100 kB free on 2026-11-03 (docs/memory.md). Empty on host builds.
#if __has_include("esp_attr.h")
#include "esp_attr.h"
#endif
#ifndef EXT_RAM_BSS_ATTR
#define EXT_RAM_BSS_ATTR
#endif
#include "boot_cache.h"
#include "esphome/core/preferences.h"
#include "esp_sntp.h"
namespace boot_cache {
using PanelCache = Cache<esphome::ESPPreferenceObject>;
inline PanelCache panel_cache EXT_RAM_BSS_ATTR;
constexpr uint32_t PREFERENCE_KEY = 0xB027C001;
}  // namespace boot_cache
