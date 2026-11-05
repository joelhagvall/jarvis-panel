#pragma once
#include <atomic>
#include <algorithm>
#include <cstdlib>
#include <memory>
#include <string>
#include "packed_atlas.h"
#include "esphome/components/http_request/http_request.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#include "esp_http_client.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif

namespace packed_orb {
class Loader {
 public:
  void begin(esphome::http_request::HttpRequestComponent *http, const std::string &url, size_t count) {
    if (bytes_ || state_.load(std::memory_order_acquire) == LOADING) return;
    expected_ = count;
    received_ = size_ = 0;
    valid_ = false;
    url_ = url;
    started_ = esphome::millis();
    state_.store(LOADING, std::memory_order_release);
#ifdef USE_ESP32
    if (xTaskCreate([](void *arg) {
          auto *self = static_cast<Loader *>(arg);
          bool ok = self->download_();
          self->state_.store(ok ? LOADED : FAILED, std::memory_order_release);
          vTaskDelete(nullptr);
        }, "orb-download", 6144, this, 1, nullptr) != pdPASS) state_.store(FAILED, std::memory_order_release);
#else
    response_ = http->get(url);
    if (!response_ || response_->status_code != 200 || !allocate_(response_->content_length))
      state_.store(FAILED, std::memory_order_release);
#endif
  }
  void step() {
#ifndef USE_ESP32
    if (state_.load() == LOADING) {
      const int got = response_->read(bytes_ + received_, std::min(size_ - received_, size_t(4096)));
      if (got <= 0 || esphome::millis() - started_ > 30000) state_.store(FAILED);
      else {
        received_ += got;
        if (received_ == size_) state_.store(LOADED);
      }
    }
#endif
    const int state = state_.load(std::memory_order_acquire);
    if (state != LOADED && state != FAILED) return;
#ifndef USE_ESP32
    if (response_) response_->end();
    response_.reset();
#endif
    valid_ = state == LOADED && atlas_.open(bytes_, size_, expected_);
    if (!valid_) {
      std::free(bytes_);
      bytes_ = nullptr;
      ESP_LOGW("packed_orb", "Atlas load failed; keeping the static orb");
    } else {
      ESP_LOGI("packed_orb", "Loaded %u compressed frames in %u ms; retained %u bytes", unsigned(atlas_.count()),
               unsigned(esphome::millis() - started_), unsigned(size_));
    }
    state_.store(READY, std::memory_order_release);
  }
  bool done() const { return state_.load(std::memory_order_acquire) == READY; }
  bool ok() const { return done() && valid_; }
  const Atlas &atlas() const { return atlas_; }
 private:
  bool allocate_(size_t size) {
    if (size < 16 || size > MAX_BYTES) return false;
    size_ = size;
#ifdef USE_ESP32
    bytes_ = static_cast<uint8_t *>(heap_caps_malloc(size, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
#else
    bytes_ = static_cast<uint8_t *>(std::malloc(size));
#endif
    return bytes_ != nullptr;
  }
#ifdef USE_ESP32
  bool download_() {
    esp_http_client_config_t config{};
    config.url = url_.c_str();
    config.timeout_ms = 2000;
    config.buffer_size = 4096;
    auto client = esp_http_client_init(&config);
    if (!client) return false;
    bool ok = false;
    if (esp_http_client_open(client, 0) == ESP_OK) {
      const auto size = esp_http_client_fetch_headers(client);
      if (esp_http_client_get_status_code(client) == 200 && size >= 16 && size <= MAX_BYTES && allocate_(size)) {
        while (received_ < size_ && esphome::millis() - started_ <= 30000) {
          const int got = esp_http_client_read(client, reinterpret_cast<char *>(bytes_ + received_),
                                               std::min(size_ - received_, size_t(4096)));
          if (got <= 0) break;
          received_ += got;
          vTaskDelay(1);
        }
        ok = received_ == size_;
      }
    }
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return ok;
  }
#else
  std::shared_ptr<esphome::http_request::HttpContainer> response_;
#endif
  enum State { READY, LOADING, LOADED, FAILED };
  std::atomic<int> state_{READY};
  uint8_t *bytes_{nullptr};
  size_t size_{0}, expected_{0}, received_{0};
  uint32_t started_{0};
  bool valid_{false};
  std::string url_;
  Atlas atlas_;
};
inline Loader loader;
}  // namespace packed_orb
