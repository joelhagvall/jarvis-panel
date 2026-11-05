#pragma once
// Only non-safety weather/calendar presentation. Never publish restored sensor states.
#include <array>
#include <cstdint>
#include <cstring>
#include <string>
#include <string_view>

namespace boot_cache {
constexpr size_t FIELD_COUNT = 9, RECORD_BYTES = 2688;   // 9: the weather card's days (2026-10-29)
constexpr uint32_t SCHEMA = 0x42434302, MAX_AGE_S = 86400, WRITE_GAP_S = 3600;
constexpr std::array<size_t, FIELD_COUNT> LIMITS{64, 256, 512, 16, 96, 512, 512, 512, 96};
using Bytes = std::array<uint8_t, RECORD_BYTES>;
using Fields = std::array<std::string_view, FIELD_COUNT>;

inline uint32_t read32(const uint8_t *p) {
  return uint32_t(p[0]) | uint32_t(p[1]) << 8 | uint32_t(p[2]) << 16 | uint32_t(p[3]) << 24;
}
inline void write32(uint8_t *p, uint32_t n) {
  for (unsigned i = 0; i < 4; ++i) p[i] = uint8_t(n >> (8 * i));
}
inline uint32_t checksum(const Bytes &data) {
  uint32_t h = 2166136261u;
  for (size_t i = 4; i < data.size(); ++i) { h ^= data[i]; h *= 16777619u; }
  return h;
}

class Record {
 public:
  Bytes bytes{};
  bool validate() const {
    if (read32(bytes.data()) != checksum(bytes) || read32(bytes.data() + 4) != SCHEMA || epoch() < 1704067200u)
      return false;
    // Saved local timestamp, not a source of current time.
    for (size_t i = 12; i < 28; ++i) {
      const size_t j = i - 12;
      const char expected = j == 4 || j == 7 ? '-' : j == 10 ? ' ' : j == 13 ? ':' : 0;
      if (expected ? bytes[i] != uint8_t(expected) : bytes[i] < '0' || bytes[i] > '9') return false;
    }
    size_t at = 28;
    for (size_t i = 0; i < FIELD_COUNT; ++i) {
      if (at + 2 > bytes.size()) return false;
      size_t n = size_t(bytes[at]) | size_t(bytes[at + 1]) << 8; at += 2;
      if (n > LIMITS[i] || at + n > bytes.size()) return false;
      for (size_t j = 0; j < n; ++j) if (!bytes[at + j]) return false;
      at += n;
    }
    for (; at < bytes.size(); ++at) if (bytes[at]) return false;
    return true;
  }
  uint32_t epoch() const { return read32(bytes.data() + 8); }
  bool usable(uint32_t now) const {
    // Unknown current time is always visibly marked; never seed the RTC from NVS.
    return validate() && (!now || (now >= epoch() && now - epoch() <= MAX_AGE_S));
  }
  std::string stamp() const { return std::string(reinterpret_cast<const char *>(bytes.data() + 12), 16); }
  std::string field(size_t index) const {
    if (index >= FIELD_COUNT || !validate()) return {};
    size_t at = 28;
    for (size_t i = 0; i < FIELD_COUNT; ++i) {
      size_t n = size_t(bytes[at]) | size_t(bytes[at + 1]) << 8; at += 2;
      if (i == index) return std::string(reinterpret_cast<const char *>(bytes.data() + at), n);
      at += n;
    }
    return {};
  }
  bool encode(uint32_t now, std::string_view stamp, const Fields &fields) {
    bytes.fill(0);
    if (stamp.size() != 16 || now < 1704067200u) return false;
    write32(bytes.data() + 4, SCHEMA); write32(bytes.data() + 8, now);
    std::memcpy(bytes.data() + 12, stamp.data(), 16);
    size_t at = 28;
    for (size_t i = 0; i < FIELD_COUNT; ++i) {
      const auto value = fields[i];
      if (value.size() > LIMITS[i] || value.find('\0') != std::string_view::npos || at + 2 + value.size() > bytes.size()) return false;
      bytes[at++] = uint8_t(value.size()); bytes[at++] = uint8_t(value.size() >> 8);
      if (!value.empty()) std::memcpy(bytes.data() + at, value.data(), value.size());
      at += value.size();
    }
    write32(bytes.data(), checksum(bytes));
    return validate();
  }
};

// Storage has load/save(Record*) matching ESPHome preferences. Buffers live in
// the global component, never as multi-KiB locals on the LVGL/HTTP task stack.
template<class Storage> class Cache {
 public:
  bool loaded = false;
  uint32_t saves = 0, failures = 0;
  void load(Storage storage) {
    storage_ = storage;
    loaded = storage_.load(&saved_) && saved_.validate();
  }
  bool usable(uint32_t now) const { return loaded && saved_.usable(now); }
  uint32_t epoch() const { return loaded ? saved_.epoch() : 0; }
  std::string field(size_t i) const { return loaded ? saved_.field(i) : std::string{}; }
  std::string stamp() const { return loaded ? saved_.stamp() : std::string{}; }
  bool save(uint32_t now, uint32_t uptime, std::string_view stamp, const Fields &fields) {
    if (uptime < 65000 || !now) return false; // No crash-loop flash writes.
    if (loaded && (now < saved_.epoch() || now - saved_.epoch() < WRITE_GAP_S)) return false;
    if (!candidate_.encode(now, stamp, fields)) { ++failures; return false; }
    if (loaded && now - saved_.epoch() < 6 * WRITE_GAP_S &&
        std::memcmp(saved_.bytes.data() + 28, candidate_.bytes.data() + 28, RECORD_BYTES - 28) == 0) return false;
    if (!storage_.save(&candidate_)) { ++failures; return false; }
    saved_ = candidate_; loaded = true; ++saves;
    return true; // Preferences synchronizes its queued write through its usual task.
  }
 private:
  Storage storage_{};
  Record saved_{}, candidate_{};
};
}  // namespace boot_cache
