#pragma once
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>

namespace packed_orb {
// One producer and one LVGL consumer. State ownership protects pixel memory;
// the index is atomic too, because a READY slot may change between probe and pin.
template<size_t Count>
class PrefetchSlots {
 public:
  static constexpr uint32_t NONE = UINT32_MAX;
  enum State : uint32_t { EMPTY, WRITING, READY, READING };
  struct Slot {
    std::atomic<uint32_t> state{EMPTY}, index{NONE};
    uint8_t *data{};
  };
  Slot *take(uint32_t index) {
    for (auto &slot : slots) {
      if (slot.index.load(std::memory_order_relaxed) != index) continue;
      uint32_t expected = READY;
      if (!slot.state.compare_exchange_strong(expected, READING, std::memory_order_acquire)) continue;
      // Close the READY->WRITING->READY ABA window before reading any pixels.
      if (slot.index.load(std::memory_order_relaxed) == index) return &slot;
      slot.state.store(READY, std::memory_order_release);
    }
    return nullptr;
  }
  Slot *reserve(uint32_t index, uint32_t keep_first = NONE, uint32_t keep_second = NONE) {
    for (auto &slot : slots) {
      const auto state = slot.state.load(std::memory_order_acquire);
      if ((state == READY || state == READING) && slot.index.load(std::memory_order_relaxed) == index) return nullptr;
    }
    for (const auto state : {EMPTY, READY}) {
      for (size_t i = 0; i < Count; ++i) {
        auto &slot = slots[(cursor_ + i) % Count];
        if (state == READY && (slot.index.load(std::memory_order_relaxed) == keep_first ||
                              slot.index.load(std::memory_order_relaxed) == keep_second)) continue;
        uint32_t expected = state;
        if (slot.state.compare_exchange_strong(expected, WRITING, std::memory_order_acquire)) {
          slot.index.store(index, std::memory_order_relaxed);
          cursor_ = (cursor_ + i + 1) % Count;
          return &slot;
        }
      }
    }
    return nullptr;
  }
  // A cached READY image whose recipe is about to be rewritten must not be shown again.
  void forget(uint32_t index) {
    for (auto &slot : slots) {
      if (slot.index.load(std::memory_order_relaxed) != index) continue;
      uint32_t expected = READY;
      slot.state.compare_exchange_strong(expected, EMPTY, std::memory_order_acq_rel);
    }
  }
  void publish(Slot &slot) { slot.state.store(READY, std::memory_order_release); }
  void abort(Slot &slot) { slot.state.store(EMPTY, std::memory_order_release); }
  void release(Slot &slot) { slot.state.store(READY, std::memory_order_release); }
  std::array<Slot, Count> slots{};
 private:
  size_t cursor_{};  // producer only
};
}  // namespace packed_orb
