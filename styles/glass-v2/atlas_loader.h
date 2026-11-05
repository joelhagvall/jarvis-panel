#pragma once
#include <array>
#include <cstdlib>
#include <cstring>
#include <memory>
#include "atlas_codec.h"
#include "esphome/components/http_request/http_request.h"
#include "esphome/components/image/image.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include <atomic>
#endif

namespace orb_atlas_ns {
#ifdef USE_ESP32
// All HTTP and decoding belong to this worker. Only step() touches the image used by LVGL.
class Loader {
 public:
  void begin(esphome::http_request::HttpRequestComponent *, const std::string &url, size_t count) {
    if (image_ || state_.load(std::memory_order_acquire) == LOADING) return;
    url_ = url;
    expected_count_ = count;
    started_ = esphome::millis();
    state_.store(LOADING, std::memory_order_release);
    if (xTaskCreate([](void *arg) {
          auto *self = static_cast<Loader *>(arg);
          const bool ok = self->download_();
          self->state_.store(ok ? LOADED : FAILED, std::memory_order_release);
          vTaskDelete(nullptr);
        }, "orb-download", 8192, this, 1, nullptr) != pdPASS) {
      state_.store(FAILED, std::memory_order_release);
    }
  }
  void step() {
    const int state = state_.load(std::memory_order_acquire);
    if (state == LOADED) {
      image_ = std::make_unique<esphome::image::Image>(reinterpret_cast<uint8_t *>(pixels_), 220,
          expected_count_ * 220, esphome::image::IMAGE_TYPE_RGB565,
          alpha_ ? esphome::image::TRANSPARENCY_ALPHA_CHANNEL : esphome::image::TRANSPARENCY_OPAQUE);
      state_.store(READY, std::memory_order_release);
      ESP_LOGI("orb_atlas", "Background RGB565 atlas ready in %u ms", esphome::millis() - started_);
    } else if (state == FAILED) {
      std::free(pixels_);
      pixels_ = nullptr;
      state_.store(READY, std::memory_order_release);
      ESP_LOGW("orb_atlas", "Atlas load failed; keeping the static orb");
    }
  }
  bool done() const { return state_.load(std::memory_order_acquire) == READY; }
  bool ok() const { return done() && image_ != nullptr; }
  esphome::image::Image *image() { return image_.get(); }
 private:
  bool download_() {
    esp_http_client_config_t config{};
    config.url = url_.c_str();
    config.timeout_ms = 2000;
    config.buffer_size = 4096;
    auto client = esp_http_client_init(&config);
    if (!client) return false;
    const bool ok = read_(client);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return ok;
  }
  bool read_(esp_http_client_handle_t client) {
    if (esp_http_client_open(client, 0) != ESP_OK) return false;
    const int64_t length = esp_http_client_fetch_headers(client);
    if (esp_http_client_get_status_code(client) != 200 || length < 12 || length > 20 * 1024 * 1024) return false;
    std::array<uint8_t, 12> header{};
    size_t at = 0;
    while (at < header.size()) {
      const int got = esp_http_client_read(client, reinterpret_cast<char *>(header.data() + at), header.size() - at);
      if (got <= 0 || esphome::millis() - started_ > 30000) return false;
      at += got;
    }
    const size_t width = header[4] | (size_t(header[5]) << 8);
    const size_t count = header[6] | (size_t(header[7]) << 8);
    const uint32_t bytes = uint32_t(header[8]) | (uint32_t(header[9]) << 8) |
                           (uint32_t(header[10]) << 16) | (uint32_t(header[11]) << 24);
    alpha_ = std::memcmp(header.data(), "HPA2", 4) == 0;
    if ((!alpha_ && std::memcmp(header.data(), "HPA1", 4)) || width != 220 || count != expected_count_ ||
        bytes != count * 220 * 220 * (alpha_ ? 3 : 2)) return false;
    pixels_ = static_cast<uint16_t *>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM));
    if (!pixels_) return false;
    RleDecoder decoder(pixels_, bytes / 2);
    std::array<uint8_t, 4096> input{};
    size_t size = 0, used = 0, received = header.size();
    while (!decoder.done()) {
      if (esphome::millis() - started_ > 30000) return false;
      if (used == size) {
        if (decoder.feed(nullptr, 0) < 0) return false;
        if (decoder.done()) break;
        const int got = esp_http_client_read(client, reinterpret_cast<char *>(input.data()), input.size());
        if (got <= 0) return false;
        received += got;
        used = 0;
        size = got;
      }
      const int consumed = decoder.feed(input.data() + used, size - used);
      if (consumed < 0) return false;
      used += consumed;
      vTaskDelay(1);  // let audio, touch and the main loop run between decoder budgets
    }
    return used == size && received == size_t(length);
  }
  enum State { READY, LOADING, LOADED, FAILED };
  std::atomic<int> state_{READY};
  std::string url_;
  size_t expected_count_{0};
  uint32_t started_{0};
  uint16_t *pixels_{nullptr};
  bool alpha_{false};
  std::unique_ptr<esphome::image::Image> image_;
};
#else
class Loader {
 public:
  void begin(esphome::http_request::HttpRequestComponent *http, const std::string &url, size_t count) {
    if (image_) { finished_ = true; return; }  // a decoded atlas is immutable until reboot
    finished_ = false;
    at_ = size_ = header_bytes_ = 0;
    started_ = esphome::millis();
    expected_count_ = count;
    response_ = http->get(url);
    if (!response_ || response_->status_code != 200 || response_->content_length < 12 ||
        response_->content_length > 20 * 1024 * 1024) fail_();
  }
  void step() {
    if (finished_) return;
    if (esphome::millis() - started_ > 30000) { fail_(); return; }
    if (at_ == size_) {
      // First drain an outstanding repeated run without reading another network chunk.
      if (decoder_ && decoder_->feed(nullptr, 0) < 0) { fail_(); return; }
      if (decoder_ && decoder_->done()) { finish_(); return; }
      const int got = response_->read(input_.data(), input_.size());
      if (got <= 0) { fail_(); return; }
      at_ = 0;
      size_ = size_t(got);
    }
    while (header_bytes_ < header_.size() && at_ < size_) header_[header_bytes_++] = input_[at_++];
    if (header_bytes_ < header_.size()) return;
    if (!decoder_ && !prepare_()) { fail_(); return; }
    const int used = decoder_->feed(input_.data() + at_, size_ - at_);
    if (used < 0) { fail_(); return; }
    at_ += used;
    if (decoder_->done()) finish_();
  }
  bool done() const { return finished_; }
  bool ok() const { return finished_ && image_ != nullptr; }
  esphome::image::Image *image() { return image_.get(); }
 private:
  bool prepare_() {
    const size_t width = header_[4] | (size_t(header_[5]) << 8);
    const size_t count = header_[6] | (size_t(header_[7]) << 8);
    const uint32_t bytes = uint32_t(header_[8]) | (uint32_t(header_[9]) << 8) |
                           (uint32_t(header_[10]) << 16) | (uint32_t(header_[11]) << 24);
    alpha_ = std::memcmp(header_.data(), "HPA2", 4) == 0;
    if ((!alpha_ && std::memcmp(header_.data(), "HPA1", 4)) || width != 220 || count != expected_count_ ||
        bytes != count * 220 * 220 * (alpha_ ? 3 : 2)) return false;
#ifdef USE_ESP32
    pixels_ = static_cast<uint16_t *>(heap_caps_malloc(bytes, MALLOC_CAP_SPIRAM));
#else
    pixels_ = static_cast<uint16_t *>(std::malloc(bytes));
#endif
    if (!pixels_) return false;
    decoder_ = std::make_unique<RleDecoder>(pixels_, bytes / 2);
    return true;
  }
  void finish_() {
    if (at_ != size_ || response_->get_bytes_read() != response_->content_length) { fail_(); return; }
    response_->end();
    response_.reset();
    image_ = std::make_unique<esphome::image::Image>(reinterpret_cast<uint8_t *>(pixels_), 220,
        expected_count_ * 220, esphome::image::IMAGE_TYPE_RGB565,
        alpha_ ? esphome::image::TRANSPARENCY_ALPHA_CHANNEL : esphome::image::TRANSPARENCY_OPAQUE);
    decoder_.reset();
    finished_ = true;
    ESP_LOGI("orb_atlas", "RGB565 atlas unpacked in %u ms", esphome::millis() - started_);
  }
  void fail_() {
    if (response_) response_->end();
    response_.reset();
    decoder_.reset();
    std::free(pixels_);
    pixels_ = nullptr;
    image_.reset();
    finished_ = true;
    ESP_LOGW("orb_atlas", "Atlas load failed; keeping the static orb");
  }
  std::shared_ptr<esphome::http_request::HttpContainer> response_;
  std::unique_ptr<RleDecoder> decoder_;
  std::unique_ptr<esphome::image::Image> image_;
  std::array<uint8_t, 4096> input_{};
  std::array<uint8_t, 12> header_{};
  size_t at_{0}, size_{0}, header_bytes_{0}, expected_count_{0};
  uint16_t *pixels_{nullptr};
  uint32_t started_{0};
  bool finished_{true};
  bool alpha_{false};
};
#endif
inline Loader loader;
}  // namespace orb_atlas_ns
