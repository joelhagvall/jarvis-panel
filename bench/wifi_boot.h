#pragma once
// Boot-time Wi-Fi milestones for GET /status: where the ~6 s between UI and "Wi-Fi connected" go.
// ESPHome's own on_connect gives one number (boot_wifi_ms). The ESP-IDF events (forwarded from the C6 by
// esp_wifi_remote) split it into setup done -> associated (STA_CONNECTED) -> IP (STA_GOT_IP), and record the
// negotiated auth mode/channel and any failed attempts. Passive: handlers only store millis() once.
#include <atomic>
#include <cstdint>
#include <string>
#include "esp_event.h"
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esphome/core/hal.h"

namespace wifi_boot {

inline std::atomic<uint32_t> setup_ms{0}, assoc_ms{0}, ip_ms{0}, authmode{0xff}, channel{0}, disconnects{0},
    last_reason{0};
inline std::atomic<int32_t> install_error{1};  // 1 = not installed yet, 0 = ok, else esp_err_t

inline void on_event(void *, esp_event_base_t base, int32_t id, void *data) {
  if (base == WIFI_EVENT && id == WIFI_EVENT_STA_CONNECTED) {
    if (!assoc_ms.load()) {
      const auto *event = static_cast<const wifi_event_sta_connected_t *>(data);
      if (event != nullptr) {
        authmode.store(event->authmode);
        channel.store(event->channel);
      }
      assoc_ms.store(esphome::millis());
    }
  } else if (base == WIFI_EVENT && id == WIFI_EVENT_STA_DISCONNECTED) {
    if (!ip_ms.load()) {
      disconnects.fetch_add(1);
      const auto *event = static_cast<const wifi_event_sta_disconnected_t *>(data);
      if (event != nullptr)
        last_reason.store(event->reason);
    }
  } else if (base == IP_EVENT && id == IP_EVENT_STA_GOT_IP) {
    if (!ip_ms.load())
      ip_ms.store(esphome::millis());
  }
}

// Called once from on_boot after ESPHome's Wi-Fi setup created the default event loop.
inline void install() {
  if (install_error.load() != 1)
    return;
  setup_ms.store(esphome::millis());
  esp_err_t err = esp_event_handler_instance_register(WIFI_EVENT, ESP_EVENT_ANY_ID, &on_event, nullptr, nullptr);
  if (err == ESP_OK)
    err = esp_event_handler_instance_register(IP_EVENT, IP_EVENT_STA_GOT_IP, &on_event, nullptr, nullptr);
  install_error.store(err);
}

inline std::string json() {
  std::string out = ",\"boot_wifi_setup_ms\":";
  out += std::to_string(setup_ms.load());
  out += ",\"boot_wifi_assoc_ms\":";
  out += std::to_string(assoc_ms.load());
  out += ",\"boot_wifi_ip_ms\":";
  out += std::to_string(ip_ms.load());
  out += ",\"boot_wifi_authmode\":";
  out += std::to_string(authmode.load());
  out += ",\"boot_wifi_channel\":";
  out += std::to_string(channel.load());
  out += ",\"boot_wifi_disconnects\":";
  out += std::to_string(disconnects.load());
  out += ",\"boot_wifi_last_reason\":";
  out += std::to_string(last_reason.load());
  out += ",\"boot_wifi_probe_error\":";
  out += std::to_string(install_error.load());
  return out;
}

}  // namespace wifi_boot
