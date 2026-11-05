#pragma once
#include <resampler.h>
#include <algorithm>
#include <array>
#include <cstddef>
#include <cstdint>

namespace panel_audio_dsp {
// The physical bus is 48 kHz. Only wake-word/STT consumers receive 16 kHz.
// Reuse the same filtered ART resampler shipped in ESPHome's audio library.
class MicResampler {
 public:
  static constexpr size_t INPUT_FRAMES = 768;  // One 16 ms I2S read.
  static constexpr size_t OUTPUT_FRAMES = 320;
  bool begin() {
    if (ready_) return true;
    esp_audio_libs::resampler::ResamplerConfiguration config = {
        48000.0f, 16000.0f, 16, 16, 1, false, false, 128, 16};
    ready_ = resampler_.initialize(config);
    return ready_;
  }
  template<typename Emit> bool process(const uint8_t *data, size_t bytes, Emit emit) {
    if (!ready_ || bytes % 2) return false;
    size_t frames = bytes / 2;
    unsigned no_input_progress = 0;
    while (frames) {
      const size_t available = std::min(frames, INPUT_FRAMES);
      auto result = resampler_.resample(data, output_.data(), available, OUTPUT_FRAMES, 0.0f);
      if (result.frames_used > available || result.frames_generated > OUTPUT_FRAMES ||
          (!result.frames_used && !result.frames_generated)) return false;
      if (!result.frames_used && ++no_input_progress > 4) return false;
      if (result.frames_used) no_input_progress = 0;
      input_frames_ += result.frames_used;
      output_frames_ += result.frames_generated;
      clipped_ += result.clipped_samples;
      if (result.frames_generated) emit(output_.data(), result.frames_generated * 2);
      data += result.frames_used * 2;
      frames -= result.frames_used;
    }
    return true;
  }
  uint64_t input_frames() const { return input_frames_; }
  uint64_t output_frames() const { return output_frames_; }
  uint64_t clipped() const { return clipped_; }

 private:
  esp_audio_libs::resampler::Resampler resampler_{INPUT_FRAMES, OUTPUT_FRAMES};
  std::array<uint8_t, OUTPUT_FRAMES * 2> output_{};
  bool ready_{false};
  uint64_t input_frames_{0}, output_frames_{0}, clipped_{0};
};
}  // namespace panel_audio_dsp
