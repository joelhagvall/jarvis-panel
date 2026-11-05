#pragma once
#include <array>
#include <cmath>

// Speaker on_volume callbacks are deferred. Track automatic changes until their callbacks arrive,
// so a night-mode cap never overwrites the user's saved volume, even with several queued changes.
class PanelVolumeState {
 public:
  bool ready{false};
  void automatic(float value) {
    if (count_ == pending_.size()) {
      for (size_t i = 1; i < count_; i++) pending_[i - 1] = pending_[i];
      count_--;
    }
    pending_[count_++] = value;
  }
  void observed(float value, float &preference) {
    for (size_t i = 0; i < count_; i++) {
      if (std::fabs(value - pending_[i]) < 0.001f) {
        for (size_t j = i + 1; j < count_; j++) pending_[j - 1] = pending_[j];
        count_--;
        return;
      }
    }
    if (ready && std::isfinite(value) && value >= 0 && value <= 1) preference = value;
  }
 private:
  std::array<float, 8> pending_{};
  size_t count_{0};
};
inline PanelVolumeState panel_volume_state;
