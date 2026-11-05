#pragma once
// Display frame clock. The DPI frame interrupt writes it; the UI task reads it.
// A "presentation" is a frame boundary at which a newly submitted buffer began
// scanning out. Counting boundaries and presentations measures what the panel
// shows: a boundary without a presentation repeats the previous image.
// Software timestamps only; this is not an optical measurement.
#include <array>
#include <atomic>
#include <cstddef>
#include <cstdint>
#include <string>

namespace vsync {
constexpr size_t RING = 128;
constexpr uint32_t STALE_US = 100000;  // no boundary for 100 ms: the display clock is not live

struct Snapshot {
  uint32_t count{}, presented{}, last_us{}, total{}, valid{};
  std::array<uint32_t, RING> us{};
  // Only the trailing `valid` ring entries are reported; the rest could have been
  // overwritten by the interrupt while the UI task copied the array.
  std::string json(uint32_t now_us,const char *key="presentation") const {
    std::string out = ",\""+std::string(key)+"\":{\"schema\":1,\"vsync_total\":" + std::to_string(count) +
                      ",\"presented_total\":" + std::to_string(presented) +
                      ",\"vsync_age_us\":" + std::to_string(uint32_t(now_us - last_us)) +
                      ",\"presented_interval\":{\"total\":" + std::to_string(total) + ",\"us\":[";
    for (uint32_t i = total - valid; i < total; ++i) {
      if (i != total - valid) out += ',';
      out += std::to_string(us[i % RING]);
    }
    return out + "]}}";
  }
};

struct Clock {
  std::atomic<uint32_t> count{0};           // frame boundaries since boot
  std::atomic<uint32_t> last_us{0};         // microseconds of the latest boundary
  std::atomic<uint32_t> presented{0};       // boundaries that presented a new buffer
  std::atomic<uint32_t> interval_total{0};  // presentation intervals recorded
  std::atomic<bool> pending{false};         // a submitted buffer has not been presented yet
  std::array<uint32_t, RING> interval_us{};
  uint32_t previous_us{};  // interrupt only
  bool have_previous{};    // interrupt only

  // UI task: a new buffer was submitted; it is presented at the next boundary.
  void submitted() { pending.store(true, std::memory_order_release); }

  // Interrupt context: one call per frame boundary. The ring entry is written
  // before its total becomes visible.
  void frame(uint32_t now_us, bool presented_now) {
    last_us.store(now_us, std::memory_order_relaxed);
    if (presented_now) pending.store(false, std::memory_order_relaxed);
    count.fetch_add(1, std::memory_order_release);
    if (!presented_now) return;
    if (have_previous) {
      const uint32_t total = interval_total.load(std::memory_order_relaxed);
      interval_us[total % RING] = now_us - previous_us;
      interval_total.store(total + 1, std::memory_order_release);
    }
    previous_us = now_us;
    have_previous = true;
    presented.fetch_add(1, std::memory_order_release);
  }
  // Signed: a boundary stamped a moment after the caller read its clock still counts as live.
  bool live(uint32_t now_us) const {
    return count.load(std::memory_order_acquire) != 0 &&
           int32_t(now_us - last_us.load(std::memory_order_acquire)) < int32_t(STALE_US);
  }
  // UI task. Entries the interrupt may have changed during the copy are dropped,
  // including the one it can be writing before publishing its total.
  Snapshot snapshot() const {
    Snapshot s;
    const uint32_t before = interval_total.load(std::memory_order_acquire);
    std::atomic_thread_fence(std::memory_order_seq_cst);
    s.us = interval_us;
    // Full fences keep the array reads between the two total reads on weakly ordered cores.
    std::atomic_thread_fence(std::memory_order_seq_cst);
    const uint32_t after = interval_total.load(std::memory_order_acquire);
    s.count = count.load(std::memory_order_acquire);
    s.presented = presented.load(std::memory_order_acquire);
    s.last_us = last_us.load(std::memory_order_acquire);
    s.total = before;
    const uint32_t available = before < RING ? before : uint32_t(RING);
    // Positions before..after (inclusive, the last one possibly mid-write) map onto
    // the oldest logical entries of our window once the ring has wrapped.
    uint32_t unsafe = 0;
    if (before >= RING) unsafe = after - before + 1;
    else if (after + 1 > RING) unsafe = after + 1 - uint32_t(RING);
    s.valid = available > unsafe ? available - unsafe : 0;
    return s;
  }
};
// A renderer stamps its image serial into the submitted framebuffer. Only the display interrupt advances
// this clock: other widgets submitting a buffer with the same serial still repeat the record image.
constexpr uint32_t SOURCE_ACTIVE=0x80000000u,SOURCE_ID=0x7fffffffu;
struct SourceClock {
  Clock clock;
  uint32_t scanning{},previous_id{};bool was_active{}; // Interrupt only.
  void frame(uint32_t now_us,bool presented_now,uint32_t submitted) {
    if(presented_now)scanning=submitted;
    const uint32_t id=scanning&SOURCE_ID;
    if((scanning&SOURCE_ACTIVE)&&id) {
      clock.frame(now_us,!was_active||id!=previous_id);
      previous_id=id;was_active=true;
    } else {
      // Do not turn an intentionally paused/hidden interval into a missed moving frame on resume.
      clock.have_previous=false;was_active=false;
    }
  }
  Snapshot snapshot()const{return clock.snapshot();}
};
}  // namespace vsync
