#pragma once
// Strings for the debug server's large replies, kept in PSRAM.
//
// This firmware has CONFIG_SPIRAM_USE_CAPS_ALLOC without CONFIG_SPIRAM_USE_MALLOC, so new and std::string use
// internal RAM only, and it has no C++ exceptions, so a failed new aborts. The /ui tree grows to about 55 kB and
// was copied twice more on its way out; during a boot's image downloads that asked internal RAM for blocks it did
// not have, and the panel aborted (2026-10-28: __wrap___cxa_allocate_exception <- operator new <- std::string
// in PanelDebug::ui_, three installs rolled back). PSRAM has about 16 MB free.
#include <cstddef>
#include <cstdlib>
#include <new>
#include <string>
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#endif

namespace panel_debug_memory {
template<class T>
struct PsramAllocator {
  using value_type = T;
  PsramAllocator() = default;
  template<class U>
  constexpr PsramAllocator(const PsramAllocator<U> &) noexcept {}
  T *allocate(std::size_t n) {
#ifdef USE_ESP32
    void *p = heap_caps_malloc(n * sizeof(T), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT);
    if (!p) p = heap_caps_malloc(n * sizeof(T), MALLOC_CAP_8BIT);   // no PSRAM left: try internal RAM
#else
    void *p = std::malloc(n * sizeof(T));
#endif
    if (!p) std::abort();      // there are no exceptions; with 16 MB of PSRAM this is not expected
    return static_cast<T *>(p);
  }
  void deallocate(T *p, std::size_t) noexcept {
#ifdef USE_ESP32
    heap_caps_free(p);
#else
    std::free(p);
#endif
  }
  template<class U>
  bool operator==(const PsramAllocator<U> &) const noexcept { return true; }
  template<class U>
  bool operator!=(const PsramAllocator<U> &) const noexcept { return false; }
};
using String = std::basic_string<char, std::char_traits<char>, PsramAllocator<char>>;
}  // namespace panel_debug_memory
