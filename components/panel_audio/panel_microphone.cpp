#include "panel_microphone.h"
#include "esphome/core/log.h"
#include "esphome/core/hal.h"
#include <esp_timer.h>
#include <cinttypes>
#include <cmath>

namespace esphome::panel_audio {
static const char *const TAG = "panel_audio";

void PanelMicrophone::setup() {
  if (source_ == nullptr || !resampler_.begin()) {
    ESP_LOGE(TAG, "Cannot initialize filtered 48 kHz to 16 kHz microphone");
    mark_failed();
    return;
  }
  output_.reserve(::panel_audio_dsp::MicResampler::OUTPUT_FRAMES * 2);
  source_->add_data_callback([this](const std::vector<uint8_t> &data) {
    if (errors_.load()) return;
    const auto format = source_->get_audio_stream_info();
    if (format.get_sample_rate() != 48000 || format.get_bits_per_sample() != 16 || format.get_channels() != 1) {
      errors_.fetch_add(1);
      return;
    }
    const uint32_t started = esp_timer_get_time();
    const size_t samples = data.size() / 2;
    if (samples) {
      const auto *pcm = reinterpret_cast<const int16_t *>(data.data());
      int64_t sum = 0;
      for (size_t i = 0; i < samples; ++i) sum += int32_t(pcm[i]) * pcm[i];
      const float rms = std::sqrt(float(sum) / float(samples)) / 32768.0f;
      const float dt = float(samples) / 48000.0f, previous = level_.load(std::memory_order_relaxed);
      const float k = 1.0f - std::exp(-dt / (rms > previous ? 0.020f : 0.120f));
      const float next = previous + (rms - previous) * k;
      level_.store(next, std::memory_order_relaxed);
      if (next > level_max_.load(std::memory_order_relaxed)) level_max_.store(next, std::memory_order_relaxed);
    }
    const bool ok = resampler_.process(data.data(), data.size(), [this](const uint8_t *bytes, size_t size) {
      output_.assign(bytes, bytes + size);  // Capacity is reserved once; no per-frame allocation.
      data_callbacks_.call(output_);
    });
    if (!ok) errors_.fetch_add(1);
    input_.store(uint32_t(resampler_.input_frames()));
    output_frames_.store(uint32_t(resampler_.output_frames()));
    clipped_.store(uint32_t(resampler_.clipped()));
    const uint32_t duration = uint32_t(esp_timer_get_time()) - started;
    auto previous = max_processing_us_.load();
    while (previous < duration && !max_processing_us_.compare_exchange_weak(previous, duration)) {}
  });
}

void PanelMicrophone::loop() {
  state_ = source_->is_running() ? microphone::STATE_RUNNING : microphone::STATE_STOPPED;
  const uint32_t now = millis();
  if (!first_loop_) {
    first_loop_ = report_ms_ = now;
    last_input_ = input_.load(); last_output_ = output_frames_.load();
  }
  if (errors_.load() || (now - first_loop_ > 5000 && !input_.load())) {
    ESP_LOGE(TAG, "Microphone conversion stopped or received an invalid audio format");
    mark_failed();
    return;
  }
  if (now - report_ms_ >= 10000) {
    const auto input = input_.load(), output = output_frames_.load();
    ESP_LOGI(TAG, "Audio 48000->16000: elapsed_ms=%" PRIu32 " input=%" PRIu32 " output=%" PRIu32
             " max_us=%" PRIu32 " clipped=%" PRIu32 " errors=%" PRIu32 " level_max=%.4f",
             now - report_ms_, input - last_input_, output - last_output_, max_processing_us_.exchange(0),
             clipped_.load(), errors_.load(), double(level_max_.exchange(0)));
    report_ms_ = now; last_input_ = input; last_output_ = output;
  }
}

void PanelMicrophone::dump_config() {
  ESP_LOGCONFIG(TAG, "Shared audio clock: 48000 Hz; filtered microphone: 16000 Hz, 16-bit mono");
}
}  // namespace esphome::panel_audio
