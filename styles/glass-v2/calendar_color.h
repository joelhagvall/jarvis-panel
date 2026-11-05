#pragma once
// Cold, zero-initialised state goes to PSRAM's .bss (CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY): internal RAM
// was down to ~100 kB free on 2026-11-03 (docs/memory.md). Empty on host builds.
#if __has_include("esp_attr.h")
#include "esp_attr.h"
#endif
#ifndef EXT_RAM_BSS_ATTR
#define EXT_RAM_BSS_ATTR
#endif
#include <cstdint>
#include <string>

namespace calendar_color {
inline uint32_t parse(const std::string &value) {
  constexpr uint32_t unknown = 0x8E8E93;
  if (value.size() != 7 || value[0] != '#') return unknown;
  uint32_t result = 0;
  for (size_t i = 1; i < 7; ++i) {
    const char c = value[i];
    const int digit = c >= '0' && c <= '9' ? c - '0' : c >= 'a' && c <= 'f' ? c - 'a' + 10 :
                      c >= 'A' && c <= 'F' ? c - 'A' + 10 : -1;
    if (digit < 0) return unknown;
    result = (result << 4) | uint32_t(digit);
  }
  return result;
}
}
