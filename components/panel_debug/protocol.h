#pragma once
#include <algorithm>
#include <array>
#include <cstdint>
#include <string>
#include <string_view>

namespace esphome::panel_debug {
constexpr int WIDTH = 1024, HEIGHT = 600;
constexpr size_t FRAME_BYTES = WIDTH * HEIGHT * 2;

// Signed requests (HPv1): the token never crosses the network. The client sends
//   Authorization: HPv1 ts=<unix ms>,nonce=<16 hex>,sig=<64 hex>
// with sig = HMAC-SHA256(token, "HPv1\n" method "\n" path "\n" ts "\n" nonce "\n" lease "\n" body).
// The panel accepts a timestamp within SIGNED_WINDOW_MS of its own clock and each nonce once.
constexpr uint64_t SIGNED_WINDOW_MS = 120000;
using Hmac = void (*)(const uint8_t *key, size_t key_size, const uint8_t *message, size_t message_size,
                      uint8_t out[32]);
struct SignedHeader {
  uint64_t ts{}, nonce{};
  std::array<uint8_t, 32> sig{};
};
inline int hex_value(char c) {
  if (c >= '0' && c <= '9') return c - '0';
  if (c >= 'a' && c <= 'f') return c - 'a' + 10;
  return -1;
}
inline bool parse_signed(std::string_view header, SignedHeader &out) {
  constexpr std::string_view PREFIX = "HPv1 ts=";
  if (header.size() > 160 || header.substr(0, PREFIX.size()) != PREFIX) return false;
  header.remove_prefix(PREFIX.size());
  const auto comma = header.find(',');
  const auto ts = header.substr(0, comma);
  if (comma == std::string_view::npos || ts.empty() || ts.size() > 15 || ts[0] == '0') return false;
  out.ts = 0;
  for (char c : ts) {
    if (c < '0' || c > '9') return false;
    out.ts = out.ts * 10 + uint64_t(c - '0');
  }
  header.remove_prefix(comma + 1);
  if (header.size() != 6 + 16 + 5 + 64 || header.substr(0, 6) != "nonce=" || header.substr(22, 5) != ",sig=")
    return false;
  out.nonce = 0;
  for (char c : header.substr(6, 16)) {
    const int v = hex_value(c);
    if (v < 0) return false;
    out.nonce = (out.nonce << 4) | uint64_t(v);
  }
  const auto sig = header.substr(27);
  for (size_t i = 0; i < 32; ++i) {
    const int high = hex_value(sig[2 * i]), low = hex_value(sig[2 * i + 1]);
    if (high < 0 || low < 0) return false;
    out.sig[i] = uint8_t(high << 4 | low);
  }
  return true;
}
inline std::string signed_message(std::string_view method, std::string_view path, const SignedHeader &h,
                                  std::string_view lease, std::string_view body) {
  constexpr char HEX[] = "0123456789abcdef";
  std::string nonce(16, '0');
  for (int i = 0; i < 16; ++i) nonce[i] = HEX[(h.nonce >> (60 - 4 * i)) & 15];
  std::string out = "HPv1\n";
  out.append(method).append("\n").append(path).append("\n").append(std::to_string(h.ts)).append("\n");
  out.append(nonce).append("\n").append(lease).append("\n").append(body);
  return out;
}
// Remembers recent nonces. When the ring is full the oldest entry leaves and no request at or before its
// time is accepted again, so an evicted nonce can never be replayed.
struct ReplayGuard {
  static constexpr size_t SIZE = 256;
  std::array<std::pair<uint64_t, uint64_t>, SIZE> seen{};
  size_t next{}, used{};
  uint64_t floor{};
  bool fresh(const SignedHeader &h, uint64_t now_ms) const {
    if (h.ts + SIGNED_WINDOW_MS < now_ms || h.ts > now_ms + SIGNED_WINDOW_MS || h.ts <= floor) return false;
    for (size_t i = 0; i < used; ++i)
      if (seen[i].second == h.nonce) return false;
    return true;
  }
  void remember(const SignedHeader &h) {
    if (used == SIZE) floor = std::max(floor, seen[next].first);
    else ++used;
    seen[next] = {h.ts, h.nonce};
    next = (next + 1) % SIZE;
  }
};
// Verify first, then remember: an unsigned request can never move the replay state.
inline bool authorized_signed(std::string_view header, std::string_view token, std::string_view method,
                              std::string_view path, std::string_view lease, std::string_view body,
                              uint64_t now_ms, ReplayGuard &guard, Hmac hmac) {
  SignedHeader h;
  if (token.size() < 32 || !parse_signed(header, h) || !guard.fresh(h, now_ms)) return false;
  const std::string message = signed_message(method, path, h, lease, body);
  uint8_t expected[32];
  hmac(reinterpret_cast<const uint8_t *>(token.data()), token.size(),
       reinterpret_cast<const uint8_t *>(message.data()), message.size(), expected);
  unsigned difference = 0;
  for (size_t i = 0; i < 32; ++i) difference |= unsigned(expected[i] ^ h.sig[i]);
  if (difference) return false;
  guard.remember(h);
  return true;
}

inline std::string quoted(std::string_view input) {
  std::string out = "\"";
  constexpr char HEX[] = "0123456789abcdef";
  for (unsigned char c : input) {
    if (c == '"' || c == '\\') { out += '\\'; out += char(c); }
    else if (c < 32) { out += "\\u00"; out += HEX[c >> 4]; out += HEX[c & 15]; }
    else out += char(c);
  }
  return out + '"';
}

inline bool lease_id_valid(std::string_view id) {
  if (id.size() != 64) return false;
  for (char c : id) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
  return true;
}

// Pure protocol state: only the HTTP task mutates it. Unsigned clock wrapping is safe.
struct Lease {
  std::string id;
  uint32_t deadline{};
  bool active(uint32_t now) const { return !id.empty() && int32_t(deadline - now) > 0; }
  bool owns(std::string_view value, uint32_t now) const { return active(now) && value == id; }
  bool acquire(std::string_view value, uint32_t ttl_ms, uint32_t now) {
    if (!lease_id_valid(value) || ttl_ms < 30000 || ttl_ms > 300000 || (active(now) && value != id)) return false;
    id = value;
    deadline = now + ttl_ms;
    return true;
  }
  bool release(std::string_view value, uint32_t now) {
    if (!owns(value, now)) return false;
    id.clear();
    return true;
  }
};

struct Gesture {
  int x1{}, y1{}, x2{}, y2{}, duration{};
};
inline bool parse_gesture(std::string_view body, bool swipe, Gesture &out) {
  if (body.empty() || body.size() > 192) return false;
  int values[5]{};
  unsigned seen = 0;
  while (!body.empty()) {
    const auto end = body.find('&');
    const auto pair = body.substr(0, end);
    const auto equal = pair.find('=');
    if (equal == std::string_view::npos) return false;
    const auto key = pair.substr(0, equal), text = pair.substr(equal + 1);
    int at = -1;
    if (key == (swipe ? "x1" : "x")) at = 0;
    else if (key == (swipe ? "y1" : "y")) at = 1;
    else if (swipe && key == "x2") at = 2;
    else if (swipe && key == "y2") at = 3;
    else if (key == "duration_ms") at = 4;
    if (at < 0 || (seen & (1u << at)) || text.empty() || text.size() > 4) return false;
    int number = 0;
    for (char c : text) {
      if (c < '0' || c > '9') return false;
      number = number * 10 + c - '0';
    }
    values[at] = number;
    seen |= 1u << at;
    if (end == std::string_view::npos) break;
    body.remove_prefix(end + 1);
    if (body.empty()) return false;
  }
  const unsigned required = swipe ? 31u : 19u;
  if (seen != required || values[0] >= WIDTH || values[1] >= HEIGHT ||
      (swipe && (values[2] >= WIDTH || values[3] >= HEIGHT)) ||
      values[4] < (swipe ? 100 : 40) || values[4] > (swipe ? 2000 : 500)) return false;
  out = {values[0], values[1], swipe ? values[2] : values[0], swipe ? values[3] : values[1], values[4]};
  return true;
}
inline int interpolate(int from, int to, uint32_t elapsed, uint32_t duration) {
  if (elapsed >= duration) return to;
  return from + (to - from) * int(elapsed) / int(duration);
}
}  // namespace esphome::panel_debug
