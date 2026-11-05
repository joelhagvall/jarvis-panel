// Hemmet's reminder page (reminders_page.py, 2026-11-03): what its rows hold, and Home Assistant actions whose data
// the panel computes (a reminder's or a timer's id).
//
// The generated config is JSON, so a homeassistant.action cannot carry a lambda's value (gen.py's choice_scripts
// work around that with one script per entry and a Home Assistant template). Picking a reminder by its position in
// Home Assistant's current state could remove another one when the list changed between the tap and the call, so
// call() sends the exact id itself, the way ESPHome's own homeassistant.action does (api/homeassistant_service.h).
#pragma once
#include <initializer_list>
#include <string>
#include <utility>
#include "esphome/core/defines.h"
#include "esphome/core/log.h"
#ifdef USE_API_HOMEASSISTANT_SERVICES
#include "esphome/components/api/api_server.h"
#endif

namespace reminders_view {

constexpr int ROWS = 9;      // reminders_page.ROWS, server/reminders.py PANEL_ROWS
constexpr int TIMERS = 4;    // reminders_page.TIMERS

inline std::string rem_id[ROWS], rem_when[ROWS], rem_text[ROWS];
inline std::string open_id;            // the reminder whose card is open, for Ta bort
inline std::string timer_id[TIMERS];   // the timer in each row, for +1 min and Avbryt

// Home Assistant's action with string data; false when Home Assistant is not connected (nothing is sent).
inline bool call(const char *action, std::initializer_list<std::pair<const char *, std::string>> data) {
#ifdef USE_API_HOMEASSISTANT_SERVICES
  auto *api = esphome::api::global_api_server;
  if (api == nullptr || !api->is_connected()) {
    ESP_LOGW("reminders", "%s not sent: no Home Assistant", action);
    return false;
  }
  esphome::api::HomeassistantActionRequest req;
  req.service = esphome::StringRef(action);
  req.data.init(data.size());
  for (const auto &kv : data) {
    esphome::api::HomeassistantServiceMap m;
    m.key = esphome::StringRef(kv.first);
    m.value = esphome::StringRef(kv.second);   // kv.second lives until send_homeassistant_action returns
    req.data.push_back(m);
  }
  api->send_homeassistant_action(req);
  ESP_LOGI("reminders", "%s sent", action);
  return true;
#else
  ESP_LOGI("reminders", "%s (no Home Assistant in this build)", action);
  return false;
#endif
}

}  // namespace reminders_view
