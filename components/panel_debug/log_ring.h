#pragma once
// The panel's own log lines, kept where a software reset or a panic does not erase them.
//
// The ring lives in RTC no-init memory (LP RAM on the P4: 32 kB, almost unused), so the previous boot's last
// lines can be read after a restart without USB or Home Assistant. A power cut erases it; the magic words tell
// that garbage apart from a real ring. Pure C++: tests/test_panel_diagnostics.py compiles it under ASan/UBSan.
#include <cstddef>
#include <cstdint>
#include <cstring>
#include <string_view>

namespace panel_debug_logs {
constexpr uint32_t RING_MAGIC = 0x48504C47;  // "HPLG"
constexpr uint32_t RING_VERSION = 1;

// A plain aggregate on purpose: no constructor may clear it before the previous boot's text has been copied out.
template<size_t N> struct Ring {
  static_assert(N >= 256 && (N & (N - 1)) == 0, "the byte counter wraps; N must be a power of two");
  uint32_t magic, inverse, version, boot_id, reset_reason;
  uint32_t written;  // bytes appended since reset(); the newest byte is data[(written - 1) % N]
  char data[N];

  void reset(uint32_t boot, uint32_t reason) {
    magic = 0;
    written = 0;
    boot_id = boot;
    reset_reason = reason;
    version = RING_VERSION;
    inverse = ~RING_MAGIC;
    magic = RING_MAGIC;
  }
  bool valid() const { return magic == RING_MAGIC && inverse == ~RING_MAGIC && version == RING_VERSION; }
  void append(const char *text, size_t size) {
    if (size >= N) {
      text += size - (N - 1);
      size = N - 1;
    }
    const size_t at = written % N, first = size < N - at ? size : N - at;
    std::memcpy(data + at, text, first);
    std::memcpy(data, text + first, size - first);
    written += uint32_t(size);
  }
  // Oldest to newest into out (capacity N). After a wrap the first, partial line is dropped.
  size_t copy_out(char *out) const { return order(data, written, out); }
  // The same in two steps, so a lock only has to cover the plain copy: raw (capacity N) <- data.
  uint32_t copy_raw(char *raw) const {
    std::memcpy(raw, data, N);
    return written;
  }
  static size_t order(const char *raw, uint32_t count, char *out) {
    if (count <= N) {
      std::memcpy(out, raw, count);
      return count;
    }
    const size_t start = count % N;
    std::memcpy(out, raw + start, N - start);
    std::memcpy(out + (N - start), raw, start);
    const void *newline = std::memchr(out, '\n', N);
    if (!newline) return 0;
    const size_t skip = static_cast<const char *>(newline) - out + 1;
    std::memmove(out, out + skip, N - skip);
    return N - skip;
  }
};

// Appends one cleaned line to out: ANSI colour codes removed, control characters dropped, inner newlines shown
// as " | ", invalid UTF-8 replaced by '?'. Returns bytes written; out needs room for 3 * size + 1.
inline size_t clean_line(std::string_view in, char *out) {
  size_t n = 0;
  for (size_t i = 0; i < in.size();) {
    const auto c = uint8_t(in[i]);
    if (c == 0x1b) {  // ESC [ ... final byte
      ++i;
      if (i < in.size() && in[i] == '[') {
        ++i;
        while (i < in.size() && !(uint8_t(in[i]) >= 0x40 && uint8_t(in[i]) <= 0x7e)) ++i;
        if (i < in.size()) ++i;
      }
      continue;
    }
    if (c == '\n' || c == '\r') {
      while (i < in.size() && (in[i] == '\n' || in[i] == '\r')) ++i;
      if (i < in.size() && n) { out[n++] = ' '; out[n++] = '|'; out[n++] = ' '; }
      continue;
    }
    if (c < 0x20 || c == 0x7f) {
      if (c == '\t') out[n++] = ' ';
      ++i;
      continue;
    }
    if (c < 0x80) {
      out[n++] = char(c);
      ++i;
      continue;
    }
    size_t length = c >= 0xf0 && c <= 0xf4 ? 4 : c >= 0xe0 ? 3 : c >= 0xc2 && c <= 0xdf ? 2 : 0;
    if (c >= 0xf5) length = 0;
    bool ok = length && i + length <= in.size();
    for (size_t k = 1; ok && k < length; ++k) ok = (uint8_t(in[i + k]) & 0xc0) == 0x80;
    if (ok && length == 3) {
      const auto second = uint8_t(in[i + 1]);
      ok = !(c == 0xe0 && second < 0xa0) && !(c == 0xed && second >= 0xa0);
    }
    if (ok && length == 4) {
      const auto second = uint8_t(in[i + 1]);
      ok = !(c == 0xf0 && second < 0x90) && !(c == 0xf4 && second >= 0x90);
    }
    if (!ok) {
      out[n++] = '?';
      ++i;
      continue;
    }
    std::memcpy(out + n, in.data() + i, length);
    n += length;
    i += length;
  }
  while (n && out[n - 1] == ' ') --n;
  return n;
}

// What a panic_abort() said ("assert failed: ..."), copied before the panic handler runs.
struct AbortRecord {
  uint32_t magic, inverse, boot_id, uptime_ms;
  char text[232];
  bool valid() const { return magic == RING_MAGIC && inverse == ~RING_MAGIC; }
};
}  // namespace panel_debug_logs
