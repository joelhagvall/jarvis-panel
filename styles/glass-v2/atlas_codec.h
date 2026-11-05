#pragma once
#include <algorithm>
#include <cstddef>
#include <cstdint>

namespace orb_atlas_ns {
// Incremental RGB565 PackBits decoder. Output is bounded per call, including long repeated runs;
// a tiny compressed chunk can never monopolise the main loop by expanding the entire atlas.
class RleDecoder {
 public:
  RleDecoder(uint16_t *out, size_t words) : out_(out), capacity_(words) {}
  int feed(const uint8_t *input, size_t length, size_t budget = 32768) {
    if (failed_) return -1;
    size_t at = 0, produced = 0;
    while (produced < budget) {
      if (remaining_ && repeat_ && have_value_) {
        const size_t count = std::min(remaining_, budget - produced);
        std::fill_n(out_ + written_, count, value_);
        written_ += count;
        remaining_ -= count;
        produced += count;
        if (!remaining_) have_value_ = false;
        continue;
      }
      if (at == length) break;
      if (done()) { failed_ = true; return -1; }
      if (!half_) {
        low_ = input[at++];
        half_ = true;
        continue;
      }
      const uint16_t word = low_ | (uint16_t(input[at++]) << 8);
      half_ = false;
      if (!remaining_) {
        remaining_ = word & 0x7fff;
        repeat_ = (word & 0x8000) != 0;
        if (!remaining_ || remaining_ > capacity_ - written_) { failed_ = true; return -1; }
      } else if (repeat_) {
        value_ = word;
        have_value_ = true;
      } else {
        out_[written_++] = word;
        remaining_--;
        produced++;
      }
    }
    return int(at);
  }
  bool done() const { return !failed_ && written_ == capacity_ && !remaining_ && !half_; }
 private:
  uint16_t *out_;
  size_t capacity_, written_{0}, remaining_{0};
  uint16_t value_{0};
  uint8_t low_{0};
  bool half_{false}, repeat_{false}, have_value_{false}, failed_{false};
};
}  // namespace orb_atlas_ns
