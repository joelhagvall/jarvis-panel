#pragma once
#include "esphome/core/component.h"
#include "esphome/components/microphone/microphone.h"
#include "mic_resampler_core.h"
#include <atomic>

namespace esphome::panel_audio {
class PanelMicrophone : public Component, public microphone::Microphone {
 public:
  PanelMicrophone() { audio_stream_info_ = audio::AudioStreamInfo(16, 1, 16000); }
  void set_source(microphone::Microphone *source) { source_ = source; }
  float get_setup_priority() const override { return setup_priority::PROCESSOR; }
  void setup() override;
  void loop() override;
  void dump_config() override;
  void start() override { if (!is_failed()) source_->start(); }
  void stop() override { if (!is_failed()) source_->stop(); }
  // Loudness of what the microphone hears now (RMS of full scale, 0..1, attack 20 ms, release 120 ms).
  // Next to the speaker it follows Jarvis's own voice in step with the sound, for the orb's wave.
  float level() const { return level_.load(std::memory_order_relaxed); }

 protected:
  microphone::Microphone *source_{nullptr};
  ::panel_audio_dsp::MicResampler resampler_;
  std::vector<uint8_t> output_;
  std::atomic<uint32_t> input_{0}, output_frames_{0}, clipped_{0};
  std::atomic<uint32_t> max_processing_us_{0}, errors_{0};
  std::atomic<float> level_{0}, level_max_{0};
  uint32_t first_loop_{0}, report_ms_{0};
  uint32_t last_input_{0}, last_output_{0};
};
}  // namespace esphome::panel_audio
