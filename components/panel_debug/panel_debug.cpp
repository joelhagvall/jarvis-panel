#include "panel_debug.h"
#include <algorithm>
#include <array>
#include <cstdlib>
#include <cstring>
#include <map>
#include "esp_app_desc.h"
#include "esp_random.h"
#include "esp_system.h"
#include "mbedtls/md.h"
#include <sys/time.h>
#include "lwip/sockets.h"
#include "lwip/tcp.h"
#include "pixel_copy.h"
#include "diagnostics.h"
#include "settings_parse.h"
#include <cerrno>
#include "wire_reply.h"
#ifdef USE_WIFI
#include "esphome/components/wifi/wifi_component.h"
#include "esp_wifi.h"
#endif
#include "driver/jpeg_encode.h"
#include "esp_heap_caps.h"
#include "esphome/core/hal.h"
#include "esphome/core/application.h"
#include "esphome/core/log.h"
#include <cmath>
#include "freertos/task.h"

namespace esphome::panel_debug {
static const char *const TAG = "panel_debug";

PanelDebug::Job::~Job() {
  std::free(pixels);
  heap_caps_free(samples);
  if (done) vSemaphoreDelete(done);
}

void PanelDebug::start_radio_task_() {
  // Stack in PSRAM (docs/memory.md): it only waits on the hosted RPC once a second.
  if (xTaskCreatePinnedToCoreWithCaps(radio_task_, "radio-rssi", 3072, this, 1, nullptr, 0,
                                      MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT) != pdPASS)
    ESP_LOGW(TAG, "Signal-strength sampling task unavailable; RSSI stays unknown");
}

void PanelDebug::radio_task_(void *arg) {
  auto *self = static_cast<PanelDebug *>(arg);
  RadioCache cache;
  for (;;) {
    vTaskDelay(pdMS_TO_TICKS(1000));
#ifdef USE_WIFI
    const uint32_t now = millis();
    if (!wifi::global_wifi_component || !wifi::global_wifi_component->is_connected()) {
      cache.clear();
      self->radio_.valid.store(false, std::memory_order_release);
      continue;
    }
    const bool watched = self->radio_.polled.load(std::memory_order_acquire) &&
                         uint32_t(now - self->radio_.polled_at_ms.load(std::memory_order_acquire)) < 15000;
    if (!cache.due(now, watched ? 1000 : 60000)) continue;
    wifi_ap_record_t info{};
    const uint32_t start = micros();
    const esp_err_t result = esp_wifi_sta_get_ap_info(&info);
    const uint32_t took = micros() - start;
    cache.record(now, result == ESP_OK, info.rssi);
    self->radio_.took_us.store(took, std::memory_order_relaxed);
    self->radio_.rssi.store(info.rssi, std::memory_order_relaxed);
    self->radio_.sampled_at_ms.store(now, std::memory_order_relaxed);
    self->radio_.valid.store(result == ESP_OK, std::memory_order_release);
#endif
  }
}

float PanelDebug::radio_rssi() const {
  if (!radio_.valid.load(std::memory_order_acquire) ||
      uint32_t(millis() - radio_.sampled_at_ms.load(std::memory_order_acquire)) > 120000) return NAN;
  return float(radio_.rssi.load(std::memory_order_relaxed));
}

void PanelDebug::start_log_capture() {
  if (!boot_id_) boot_id_ = esp_random();
  diag::start_logs(boot_id_, build_id_);
}

void PanelDebug::setup() {
  start_radio_task_();
  diag::capture_crash_record();
  diag::start_boot_heap_watch();
  if (token_.empty()) { ESP_LOGI(TAG, "Remote UI disabled: no token configured"); return; }
  if (!boot_id_) boot_id_ = esp_random();
  mutex_ = xSemaphoreCreateMutex();
  if (!mutex_ || !lv_display_get_default()) { mark_failed(); return; }
  pointer_device_ = lv_indev_create();
  if (!pointer_device_) { mark_failed(); return; }
  lv_indev_set_type(pointer_device_, LV_INDEV_TYPE_POINTER);
  lv_indev_set_disp(pointer_device_, lv_display_get_default());
  lv_indev_set_mode(pointer_device_, LV_INDEV_MODE_EVENT);
  lv_indev_set_user_data(pointer_device_, this);
  lv_indev_set_read_cb(pointer_device_, [](lv_indev_t *device, lv_indev_data_t *data) {
    auto *self = static_cast<PanelDebug *>(lv_indev_get_user_data(device));
    data->point = self->point_;
    data->state = self->pressed_ ? LV_INDEV_STATE_PRESSED : LV_INDEV_STATE_RELEASED;
  });
  diag::install_touch_recorder(pointer_device_);
  if (!diag::install_redraw_map(lv_display_get_default())) ESP_LOGW(TAG, "Redraw map unavailable");
  // A clip copies the presented frame before LVGL starts drawing the next one (see clip.cpp).
  lv_display_add_event_cb(lv_display_get_default(), [](lv_event_t *event) {
    static_cast<PanelDebug *>(lv_event_get_user_data(event))->clip_grab_();
  }, LV_EVENT_REFR_START, this);
  httpd_config_t config = HTTPD_DEFAULT_CONFIG();
  config.server_port = port_;
  config.ctrl_port = 32770;
  config.core_id = 0;
  config.global_user_ctx = this;
  config.global_user_ctx_free_fn = [](void *) {};  // Component lifetime owns this pointer.
  config.open_fn = [](httpd_handle_t server, int socket) -> esp_err_t {
    return httpd_sess_set_send_override(server, socket, send_);
  };
  panel_pixel_copy_install();
  config.stack_size = 8192;
  config.max_open_sockets = 3;
  config.max_uri_handlers = 20;
  config.lru_purge_enable = true;
  config.recv_wait_timeout = config.send_wait_timeout = 3;
  if (httpd_start(&server_, &config) != ESP_OK) { mark_failed(); return; }
  static const std::pair<const char *, httpd_method_t> ROUTES[] = {
      {"/status", HTTP_GET}, {"/ui", HTTP_GET}, {"/screenshot", HTTP_GET}, {"/frame.rgb565", HTTP_GET},
      {"/tap", HTTP_POST}, {"/swipe", HTTP_POST}, {"/lease", HTTP_POST}, {"/test", HTTP_POST},
      {"/logs", HTTP_GET}, {"/crash", HTTP_GET}, {"/tasks", HTTP_POST}, {"/touches", HTTP_GET},
      {"/replay", HTTP_POST}, {"/redraw", HTTP_POST}, {"/clip", HTTP_POST}, {"/settings", HTTP_GET},
      {"/settings", HTTP_POST}, {"/restart", HTTP_POST}};
  for (const auto &[path, method] : ROUTES) {
    httpd_uri_t uri{};
    uri.uri = path;
    uri.method = method;
    uri.handler = handle_;
    uri.user_ctx = this;
    if (httpd_register_uri_handler(server_, &uri) != ESP_OK) {
      httpd_stop(server_);
      server_ = nullptr;
      mark_failed();
      return;
    }
  }
  ESP_LOGI(TAG, "Authenticated physical-screen capture and touch available on port %u", unsigned(port_));
}

void PanelDebug::dump_config() {
  ESP_LOGCONFIG(TAG, "Physical panel remote UI: %s", server_ ? "enabled (signed requests required)" : "disabled");
}

static void hmac_sha256(const uint8_t *key, size_t key_size, const uint8_t *message, size_t message_size,
                        uint8_t out[32]) {
  if (mbedtls_md_hmac(mbedtls_md_info_from_type(MBEDTLS_MD_SHA256), key, key_size, message, message_size, out) != 0)
    std::memset(out, 0, 32);  // never matches a real signature
}

// HPv1 only: the bearer token itself is no longer accepted, so it never has to cross the network.
PanelDebug::Auth PanelDebug::authenticate_(httpd_req_t *request, const std::string &path, std::string_view body) {
  const size_t length = httpd_req_get_hdr_value_len(request, "Authorization");
  char header[168]{};
  if (length == 0 || length >= sizeof(header) ||
      httpd_req_get_hdr_value_str(request, "Authorization", header, sizeof(header)) != ESP_OK)
    return Auth::DENIED;
  struct timeval now {};
  gettimeofday(&now, nullptr);
  if (now.tv_sec < 1767225600) return Auth::CLOCK;  // before 2026: SNTP has not set the clock yet
  const uint64_t now_ms = uint64_t(now.tv_sec) * 1000 + uint64_t(now.tv_usec) / 1000;
  const char *method = request->method == HTTP_POST ? "POST" : "GET";
  return authorized_signed(std::string_view(header, length), token_, method, path, lease_header_(request), body,
                           now_ms, replay_, hmac_sha256)
             ? Auth::OK
             : Auth::DENIED;
}

int PanelDebug::send_(httpd_handle_t server, int socket, const char *data, size_t size, int flags) {
  auto *self = static_cast<PanelDebug *>(httpd_get_global_user_ctx(server));
  if (self->reply_buffer_) {
    self->reply_buffer_->append(data, size);
    return int(size);
  }
  return ::send(socket, data, size, flags);
}

esp_err_t PanelDebug::reply_(httpd_req_t *request, const char *status, std::string_view body) {
  const int enabled = 1;
  if (setsockopt(httpd_req_to_sockfd(request), IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled)) != 0) return ESP_FAIL;
  httpd_resp_set_status(request, status);
  httpd_resp_set_type(request, "application/json");
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  // IDF writes each extra header as four tiny fragments. Preserve IDF's response
  // formatting, then send the resulting small JSON response as one contiguous write.
  // Captures retain streaming; no pixels or large bodies are buffered here.
  auto *self = static_cast<PanelDebug *>(httpd_get_global_user_ctx(request->handle));
  panel_debug_memory::String wire;      // as large as the body: PSRAM, not internal RAM (psram_string.h)
  wire.reserve(body.size() + 256);
  self->reply_buffer_ = &wire;
  const auto result = httpd_resp_send(request, body.data(), body.size());
  self->reply_buffer_ = nullptr;
  if (result != ESP_OK) return result;
  return panel_debug_wire::send_all(wire, [request](const char *data, size_t size) {
    return ::send(httpd_req_to_sockfd(request), data, size, 0);
  }) ? ESP_OK : ESP_FAIL;
}

esp_err_t PanelDebug::diagnostic_reply_(httpd_req_t *request, const panel_debug_memory::String &body) {
  const bool failed = body.compare(0, 9, "{\"error\":") == 0;
  return reply_(request, failed ? "503 Service Unavailable" : "200 OK", std::string_view(body.data(), body.size()));
}

std::shared_ptr<PanelDebug::Job> PanelDebug::submit_(Kind kind, const Gesture &gesture, const std::string &mode, uint32_t duration_ms,
                                                    uint32_t wait_ms, std::shared_ptr<Job> prepared) {
  auto job = prepared ? std::move(prepared) : std::make_shared<Job>(kind);
  if (!job->done) return {};
  job->gesture = gesture;
  job->mode = mode;
  job->duration_ms = duration_ms;
  if (kind == Kind::CAPTURE) {
    jpeg_encode_memory_alloc_cfg_t memory{};
    memory.buffer_direction = JPEG_ENC_ALLOC_INPUT_BUFFER;
    job->pixels = static_cast<uint8_t *>(jpeg_alloc_encoder_mem(FRAME_BYTES, &memory, &job->allocated));
    if (!job->pixels) return {};
  }
  job->queued_at = micros();
  if (xSemaphoreTake(mutex_, pdMS_TO_TICKS(30)) != pdTRUE) return {};
  const bool busy = bool(pending_);
  if (!busy) pending_ = job;
  xSemaphoreGive(mutex_);
  if (busy) return {};
  // Both tasks hold a shared reference. A timeout cannot free data used by the main task.
  if (xSemaphoreTake(job->done, pdMS_TO_TICKS(wait_ms)) != pdTRUE) {
    job->cancelled.store(true, std::memory_order_release);
    return {};
  }
  return job;
}

esp_err_t PanelDebug::handle_(httpd_req_t *request) {
  return static_cast<PanelDebug *>(request->user_ctx)->request_(request);
}

template<class Text> static bool form_body(httpd_req_t *request, Text &body, size_t limit = 192) {
  if (request->content_len <= 0 || size_t(request->content_len) > limit) return false;
  body.resize(request->content_len);
  size_t received = 0;
  while (received < body.size()) {
    const int n = httpd_req_recv(request, body.data() + received, body.size() - received);
    if (n <= 0) return false;
    received += n;
  }
  return true;
}

static bool form_fields(const std::string &body, std::map<std::string, std::string> &fields) {
  size_t start = 0;
  while (start < body.size()) {
    const size_t end = body.find('&', start);
    const auto pair = body.substr(start, end == std::string::npos ? end : end - start);
    const auto equal = pair.find('=');
    if (equal == std::string::npos || !fields.emplace(pair.substr(0, equal), pair.substr(equal + 1)).second) return false;
    if (end == std::string::npos) return true;
    start = end + 1;
  }
  return false;
}

static bool decimal(const std::string &text, uint32_t &number) {
  if (text.empty() || text.size() > 6) return false;
  number = 0;
  for (char c : text) {
    if (c < '0' || c > '9') return false;
    number = number * 10 + uint32_t(c - '0');
  }
  return true;
}

std::string PanelDebug::lease_header_(httpd_req_t *request) {
  char value[65]{};
  if (httpd_req_get_hdr_value_len(request, "X-Panel-Lease") != 64 ||
      httpd_req_get_hdr_value_str(request, "X-Panel-Lease", value, sizeof(value)) != ESP_OK) return {};
  return lease_id_valid(value) ? std::string(value) : std::string();
}

esp_err_t PanelDebug::lease_request_(httpd_req_t *request, const std::string &body) {
  std::map<std::string, std::string> fields;
  uint32_t ttl = 0;
  if (body.empty() || !form_fields(body, fields) || fields.size() != 3 ||
      !fields.count("id") || !fields.count("action") || !fields.count("ttl_ms") ||
      !lease_id_valid(fields["id"]) || !decimal(fields["ttl_ms"], ttl) || ttl < 30000 || ttl > 300000)
    return reply_(request, "400 Bad Request", "{\"error\":\"invalid lease\"}");
  const auto &action = fields["action"];
  const uint32_t now = millis();
  bool ok = false;
  if (action == "acquire") ok = lease_.acquire(fields["id"], ttl, now);
  else if (action == "renew" && lease_.owns(fields["id"], now)) ok = lease_.acquire(fields["id"], ttl, now);
  else if (action == "release") {
    ok = lease_.release(fields["id"], now);
    if (ok) restore_requested_.store(true, std::memory_order_release);
  } else if (action != "renew") return reply_(request, "400 Bad Request", "{\"error\":\"invalid lease action\"}");
  return reply_(request, ok ? "200 OK" : "409 Conflict", ok ? "{\"ok\":true}" : "{\"error\":\"device lease unavailable\"}");
}

esp_err_t PanelDebug::request_(httpd_req_t *request) {
  const std::string path = request->uri;
  // The signature covers the body, so it is read before authentication: at most 192 bytes, or a replay
  // script of up to 48 kB, which goes to PSRAM.
  const bool replay = path == "/replay";
  std::string body;
  panel_debug_memory::String script;
  if (request->content_len > 0 &&
      !(replay ? form_body(request, script, panel_debug_touch::MAX_REPLAY_BODY) : form_body(request, body)))
    return reply_(request, "400 Bad Request", "{\"error\":\"invalid body\"}");
  const Auth auth = authenticate_(request, path, replay ? std::string_view(script.data(), script.size()) : std::string_view(body));
  if (auth == Auth::CLOCK)
    return reply_(request, "503 Service Unavailable", "{\"error\":\"clock not synchronized\",\"auth\":\"hpv1\"}");
  if (auth != Auth::OK) return reply_(request, "401 Unauthorized", "{\"error\":\"unauthorized\",\"auth\":\"hpv1\"}");
  if (path.find('?') != std::string::npos)
    return reply_(request, "400 Bad Request", "{\"error\":\"query parameters are not supported\"}");
  if (path == "/lease") return lease_request_(request, body);
  // Reads that neither show nor touch the UI need no lease: a health check may read them during a test.
  const bool unleased = path == "/status" || path == "/logs" || path == "/crash" || path == "/tasks";
  if (!unleased && !lease_.owns(lease_header_(request), millis()))
    return reply_(request, "423 Locked", "{\"error\":\"an active device lease is required\"}");
  if (path == "/logs") return diagnostic_reply_(request, diag::logs_json());
  if (path == "/restart") {
    // A clean software restart (the same as after an OTA) for repeatable boot measurements.
    ESP_LOGW(TAG, "Restart requested over HTTP");
    restart_at_ms_.store(millis() + 500);
    return reply_(request, "200 OK", "{\"ok\":true}");
  }
  if (path == "/crash") return diagnostic_reply_(request, diag::crash_json());
  if (path == "/touches") return diagnostic_reply_(request, diag::touches_json());
  if (path == "/tasks" || path == "/redraw") {
    std::map<std::string, std::string> fields;
    const char *key = path == "/tasks" ? "window_ms" : "duration_ms";
    uint32_t window = 0;
    const uint32_t low = path == "/tasks" ? 500 : 200, high = path == "/tasks" ? 5000 : 10000;
    if (body.empty() || !form_fields(body, fields) || fields.size() != 1 || !fields.count(key) ||
        !decimal(fields[key], window) || window < low || window > high)
      return reply_(request, "400 Bad Request", "{\"error\":\"invalid measurement window\"}");
    return diagnostic_reply_(request, path == "/tasks" ? diag::tasks_json(window) : diag::redraw_json(window));
  }
  if (path == "/clip") {
    std::map<std::string, std::string> fields;
    uint32_t value[7];
    static const char *const KEYS[] = {"duration_ms", "x", "y", "w", "h", "scale", "quality"};
    bool ok = !body.empty() && form_fields(body, fields) && fields.size() == 7;
    for (int i = 0; ok && i < 7; ++i) ok = fields.count(KEYS[i]) && decimal(fields[KEYS[i]], value[i]);
    panel_debug_clip::Plan plan;
    if (!ok || !panel_debug_clip::plan(value[0], int(value[1]), int(value[2]), int(value[3]), int(value[4]), int(value[5]),
                                       int(value[6]), plan))
      return reply_(request, "400 Bad Request", "{\"error\":\"invalid clip\"}");
    return clip_request_(request, plan);
  }
  if (replay) {
    auto job = std::make_shared<Job>(Kind::REPLAY);
    job->samples = static_cast<panel_debug_touch::Sample *>(heap_caps_calloc(
        panel_debug_touch::MAX_REPLAY_SAMPLES, sizeof(panel_debug_touch::Sample), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    if (!job->done || !job->samples)
      return reply_(request, "503 Service Unavailable", "{\"error\":\"no memory for the replay\"}");
    job->sample_count = panel_debug_touch::parse_replay(std::string_view(script.data(), script.size()), job->samples,
                                                        panel_debug_touch::MAX_REPLAY_SAMPLES);
    if (!job->sample_count) return reply_(request, "400 Bad Request", "{\"error\":\"invalid replay script\"}");
    const uint32_t wait = job->samples[job->sample_count - 1].t_ms + 3000;
    job = submit_(Kind::REPLAY, {}, "", 0, wait, job);
    if (!job) return reply_(request, "503 Service Unavailable", "{\"error\":\"panel busy or replay timed out\"}");
    return reply_(request, job->status == 200 ? "200 OK" : "409 Conflict", job->result);
  }
  if (path == "/settings") {
    auto job = std::make_shared<Job>(Kind::SETTINGS);
    if (request->method == HTTP_POST) {
      std::map<std::string, std::string> fields;
      if (body.empty() || !form_fields(body, fields) || fields.size() != 2 || !fields.count("key") || !fields.count("value"))
        return reply_(request, "400 Bad Request", "{\"error\":\"invalid setting\"}");
      const auto parsed = panel_debug_settings::parse(fields["key"], fields["value"]);
      if (parsed.key == panel_debug_settings::Key::NONE)
        return reply_(request, "400 Bad Request", "{\"error\":\"unknown setting or value out of range\"}");
      job->settings_key = int(parsed.key);
      job->settings_a = parsed.a;
      job->settings_b = parsed.b;
    }
    job = submit_(Kind::SETTINGS, {}, "", 0, 3500, job);
    if (!job) return reply_(request, "503 Service Unavailable", "{\"error\":\"panel busy\"}");
    return reply_(request, job->status == 200 ? "200 OK" : job->status == 409 ? "409 Conflict" : "503 Service Unavailable",
                  job->result);
  }
  const bool swipe = path == "/swipe", tap = path == "/tap", test = path == "/test";
  const bool raw = path == "/frame.rgb565", image = raw || path == "/screenshot";
  Gesture gesture{};
  std::string mode;
  uint32_t test_duration = 0;
  if (tap || swipe || test) {
    if (body.empty())
      return reply_(request, "400 Bad Request", "{\"error\":\"incomplete command\"}");
    if (test) {
      std::map<std::string, std::string> fields;
      if (!form_fields(body, fields) || fields.size() != 2 || !fields.count("mode") ||
          !fields.count("duration_ms") || !decimal(fields["duration_ms"], test_duration) ||
          test_duration < 100 || test_duration > 30000)
        return reply_(request, "400 Bad Request", "{\"error\":\"invalid test mode\"}");
      mode = fields["mode"];
      const std::string allowed = "|restore|animation|animation_live|animation_60|animation_60_linear|animation_60_staged|listening|thinking|speaking|speaking_live|idle|night|ota|wifi_disconnected|ha_disconnected|api_timeout|missing_image|chat|chat_scroll|turntable|music_load|detail_weather|detail_calendar|detail_ai|";
      if (mode.empty() || mode.find('|') != std::string::npos || allowed.find("|" + mode + "|") == std::string::npos)
        return reply_(request, "400 Bad Request", "{\"error\":\"unsupported test mode\"}");
    } else if (!parse_gesture(body, swipe, gesture))
      return reply_(request, "400 Bad Request", "{\"error\":\"invalid gesture\"}");
  }
  if (image && last_capture_ms_ && millis() - last_capture_ms_ < 500)
    return reply_(request, "429 Too Many Requests", "{\"error\":\"maximum two captures per second\"}");
  if (image) last_capture_ms_ = millis();
  auto job = submit_(image ? Kind::CAPTURE : test ? Kind::TEST : tap || swipe ? Kind::TOUCH : path == "/ui" ? Kind::UI : Kind::STATUS,
                     gesture, mode, test_duration);
  if (!job) return reply_(request, "503 Service Unavailable", "{\"error\":\"panel busy or capture timed out\"}");
  if (job->status != 200)
    return reply_(request, job->status == 409 ? "409 Conflict" : "503 Service Unavailable", job->result);
  if (!image) {
    if (job->deferred_status) {
      const uint32_t start = micros();
      job->result = job->deferred_status();
      // ESP-IDF returns bytes here. This scans only the HTTP worker's stack,
      // after its deepest formatter calls; it never runs on the LVGL task.
      const uint32_t stack_free = uxTaskGetStackHighWaterMark(nullptr);
      const uint32_t encode_us = micros() - start;
      std::string observer = ",\"status_main_us\":";
      observer += std::to_string(job->copy_us);
      observer += ",\"status_queue_us\":";
      observer += std::to_string(job->queue_us);
      observer += ",\"status_worker_us\":";
      observer += std::to_string(encode_us);
      observer += ",\"status_worker_stack_min_free_bytes\":";
      observer += std::to_string(stack_free);
      job->result.insert(job->result.size() - 1, observer.data(), observer.size());
    }
    const uint32_t send_start = micros();
    const auto result = reply_(request, "200 OK", job->result);
    if (path == "/status") {
      status_send_us_ = micros() - send_start;
      status_send_bytes_ = job->result.size();
      status_send_at_ms_ = millis();
      ++status_send_count_;
      if (result != ESP_OK) ++status_send_errors_;
      // Polling is active: the sampling task refreshes signal strength every second.
      radio_.polled_at_ms.store(millis(), std::memory_order_relaxed);
      radio_.polled.store(true, std::memory_order_release);
    }
    return result;
  }

  const std::string sequence = std::to_string(job->sequence), copy = std::to_string(job->copy_us);
  // Bulk transfers can coalesce header fragments before the large body. Keep small
  // control/status responses immediate without forcing every capture fragment onto Wi-Fi.
  const int enabled = 0;
  if (setsockopt(httpd_req_to_sockfd(request), IPPROTO_TCP, TCP_NODELAY, &enabled, sizeof(enabled)) != 0) return ESP_FAIL;
  httpd_resp_set_hdr(request, "Cache-Control", "no-store");
  httpd_resp_set_hdr(request, "X-Frame-Sequence", sequence.c_str());
  httpd_resp_set_hdr(request, "X-Capture-Copy-Us", copy.c_str());
  httpd_resp_set_hdr(request, "X-Width", "1024");
  httpd_resp_set_hdr(request, "X-Height", "600");
  if (raw) {
    httpd_resp_set_type(request, "application/octet-stream");
    captures_.fetch_add(1, std::memory_order_relaxed);
    return httpd_resp_send(request, reinterpret_cast<char *>(job->pixels), FRAME_BYTES);
  }
  jpeg_encode_memory_alloc_cfg_t memory{};
  memory.buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER;
  size_t capacity = 0;
  auto *output = static_cast<uint8_t *>(jpeg_alloc_encoder_mem(FRAME_BYTES + 1024, &memory, &capacity));
  jpeg_encoder_handle_t encoder{};
  jpeg_encode_engine_cfg_t engine{};
  engine.timeout_ms = 500;
  esp_err_t error = output ? jpeg_new_encoder_engine(&engine, &encoder) : ESP_ERR_NO_MEM;
  uint32_t length = 0;
  if (error == ESP_OK) {
    jpeg_encode_cfg_t config{};
    config.width = WIDTH;
    config.height = HEIGHT;
    config.src_type = JPEG_ENCODE_IN_FORMAT_RGB565;
    config.sub_sample = JPEG_DOWN_SAMPLING_YUV444;  // 600 rows are an exact multiple of its 8-row block.
    config.image_quality = 90;
    error = jpeg_encoder_process(encoder, &config, job->pixels, job->allocated, output, capacity, &length);
  }
  if (encoder) jpeg_del_encoder_engine(encoder);
  esp_err_t response;
  if (error != ESP_OK) {
    ESP_LOGW(TAG, "JPEG capture failed: %s", esp_err_to_name(error));
    response = reply_(request, "503 Service Unavailable", "{\"error\":\"JPEG encoder unavailable\"}");
  } else {
    httpd_resp_set_type(request, "image/jpeg");
    captures_.fetch_add(1, std::memory_order_relaxed);
    response = httpd_resp_send(request, reinterpret_cast<char *>(output), length);
    ESP_LOGI(TAG, "Capture frame %u: JPEG %u bytes, main-task copy %u us", unsigned(job->sequence),
             unsigned(length), unsigned(job->copy_us));
  }
  std::free(output);
  return response;
}

bool PanelDebug::physical_touch_() const {
  for (auto *device = lv_indev_get_next(nullptr); device; device = lv_indev_get_next(device))
    if (device != pointer_device_ && lv_indev_get_type(device) == LV_INDEV_TYPE_POINTER &&
        lv_indev_get_state(device) == LV_INDEV_STATE_PRESSED) return true;
  return false;
}

void PanelDebug::pointer_(int x, int y, bool pressed) {
  point_ = {x, y};
  pressed_ = pressed;
  lv_indev_read(pointer_device_);
  // Event-mode reads resume LVGL's timer while pressed. Our main-task gesture
  // job already samples every 15 ms; extra timer reads repeat the same point
  // and reset gesture velocity/distance. Keep this pointer explicitly driven.
  // The real touchscreen's timer is unaffected. Long presses are still checked
  // by every explicit read, including stationary presses.
  lv_timer_pause(lv_indev_get_read_timer(pointer_device_));
}

void PanelDebug::finish_(const std::shared_ptr<Job> &job, int status) {
  job->status = status;
  xSemaphoreGive(job->done);
  active_.reset();
}

void PanelDebug::loop() {
  const uint32_t restart_at = restart_at_ms_.load();
  if (restart_at && int32_t(millis() - restart_at) >= 0) App.safe_reboot();
  if (int32_t(millis() - next_heap_log_ms_) >= 0) {
    // A cheap health sample of what ran out on 2026-10-29: the boot's internal RAM. In `tools/panel logs`.
    // Every 5 s for the first minute (the boot's image downloads are where it ran out), then every 10 min.
    next_heap_log_ms_ = millis() + (millis() < 60000 ? 5000 : 600000);
    ESP_LOGI(TAG, "Internal RAM: %u free, %u lowest since boot, largest block %u; PSRAM lowest %u",
             unsigned(heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
             unsigned(heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
             unsigned(heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT)),
             unsigned(heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM)));
  }
  if (!server_) return;
  clip_grab_();  // a frame presented after LVGL's last refresh start
  if (physical_touch_()) last_physical_touch_ms_ = millis();
  if (restore_requested_.exchange(false, std::memory_order_acq_rel) ||
      (test_mode_ != "idle" && int32_t(millis() - test_until_) >= 0)) {
    if (test_ && test_mode_ != "idle") test_("restore");
    test_until_ = 0;
    test_mode_ = "idle";
  }
  if (!active_ && xSemaphoreTake(mutex_, 0) == pdTRUE) {
    active_ = std::move(pending_);
    xSemaphoreGive(mutex_);
  }
  auto job = active_;
  if (!job) return;
  if (job->cancelled.load(std::memory_order_acquire)) {
    if (pressed_) {
      lv_indev_reset(pointer_device_, nullptr);
      pointer_(point_.x, point_.y, false);
    }
    finish_(job, 503);
    return;
  }
  if (job->kind == Kind::TEST) {
    if (!test_ || physical_touch_() || !test_(job->mode)) {
      job->result = "{\"error\":\"test mode unsafe or unsupported\"}";
      finish_(job, 409);
    } else {
      test_until_ = (job->mode == "restore" || job->mode == "idle") ? 0 : millis() + job->duration_ms;
      test_mode_ = (job->mode == "restore" || job->mode == "idle") ? "idle" : job->mode;
      job->result = "{\"ok\":true}";
      finish_(job);
    }
  } else if (job->kind == Kind::REPLAY) {
    replay_step_(job);
  } else if (job->kind == Kind::SETTINGS) {
    if (!settings_get_ || !settings_set_) {
      job->result = "{\"error\":\"this build has no settings hooks\"}";
      finish_(job, 503);
      return;
    }
    if (job->settings_key) {
      ESP_LOGI(TAG, "Setting %d changed over HTTP (%d, %d)", job->settings_key, job->settings_a, job->settings_b);
      if (!settings_set_(job->settings_key, job->settings_a, job->settings_b)) {
        job->result = "{\"error\":\"setting refused\"}";
        finish_(job, 409);
        return;
      }
    }
    const std::string state = settings_get_();
    job->result.assign(state.data(), state.size());
    finish_(job);
  } else if (job->kind == Kind::STATUS || job->kind == Kind::UI) {
    if (job->kind == Kind::UI) job->result = ui_();
    else {
      const uint32_t start = micros();
      job->queue_us = start - job->queued_at;
      job->deferred_status = status_();
      job->copy_us = micros() - start;
    }
    finish_(job);
  } else if (job->kind == Kind::CAPTURE) {
    const uint32_t start = micros();
    if (!capture_ || !capture_(job->pixels, FRAME_BYTES, job->sequence)) return;
    job->copy_us = micros() - start;
    finish_(job);
  } else {
    if (physical_touch_()) {
      if (pressed_) { lv_indev_reset(pointer_device_, nullptr); pointer_(point_.x, point_.y, false); }
      job->result = "{\"error\":\"physical touch has priority\"}";
      finish_(job, 409);
      return;
    }
    const uint32_t now = millis();
    const auto &g = job->gesture;
    if (!job->started) {
      job->started = now;
      job->next_step = now + 15;
      if (touch_) touch_();
      pointer_(g.x1, g.y1, true);
    } else if (now - job->started >= uint32_t(g.duration)) {
      pointer_(g.x2, g.y2, true);
      if (release_) release_();
      pointer_(g.x2, g.y2, false);
      job->result = "{\"ok\":true}";
      finish_(job);
    } else if (int32_t(now - job->next_step) >= 0) {
      pointer_(interpolate(g.x1, g.x2, now - job->started, g.duration),
               interpolate(g.y1, g.y2, now - job->started, g.duration), true);
      job->next_step = now + 15;
    }
  }
}

void PanelDebug::replay_step_(const std::shared_ptr<Job> &job) {
  // Replays recorded finger samples through the virtual pointer at their own pace. A real finger wins.
  const auto *samples = job->samples;
  const size_t count = job->sample_count;
  if (physical_touch_()) {
    if (pressed_) { lv_indev_reset(pointer_device_, nullptr); pointer_(point_.x, point_.y, false); }
    job->result = "{\"error\":\"physical touch has priority\"}";
    finish_(job, 409);
    return;
  }
  const uint32_t now = millis();
  bool apply = false;
  if (!job->started) {
    job->started = now ? now : 1;
    job->sample_index = 0;
    apply = true;
  }
  const uint32_t elapsed = now - job->started;
  size_t index = job->sample_index;
  // Within one press state jump to the newest due sample; cross a press/release change one sample at a time,
  // so no release is skipped when the loop runs slower than the samples.
  while (index + 1 < count && samples[index + 1].t_ms <= elapsed && samples[index + 1].pressed == samples[index].pressed) ++index;
  if (index == job->sample_index && index + 1 < count && samples[index + 1].t_ms <= elapsed) ++index;
  if (index != job->sample_index) apply = true;
  if (!apply && pressed_ && now - job->last_apply >= 15) apply = true;  // keep LVGL reading a held press
  if (apply) {
    const auto &s = samples[index];
    const bool was = pressed_;
    if (s.pressed && !was && touch_) touch_();
    pointer_(s.x, s.y, s.pressed);
    if (!s.pressed && was && release_) release_();
    job->last_apply = now;
    job->sample_index = index;
  }
  if (index + 1 == count && elapsed >= samples[count - 1].t_ms) {
    if (pressed_) pointer_(point_.x, point_.y, false);
    job->result = "{\"ok\":true,\"samples\":" + std::to_string(count) + ",\"took_ms\":" + std::to_string(elapsed) + "}";
    finish_(job);
  }
}

PanelDebug::DeferredReply PanelDebug::status_() const {
  // All mutable state is copied on the ESPHome task. The returned formatter only
  // reads immutable captures and runs after submit_() returns on the HTTP task.
  const auto *app = esp_app_get_description();
  char sha[65]{};
  esp_app_get_elf_sha256(sha, sizeof(sha));
  const std::string page = page_ ? page_() : "unknown", mode = test_mode_;
  const bool physical = physical_touch_();
  const uint32_t heap_start = micros();
  const std::array<uint32_t, 10> values = {
      millis(), millis() - last_physical_touch_ms_, boot_id_, uint32_t(esp_reset_reason()),
      heap_caps_get_free_size(MALLOC_CAP_SPIRAM), heap_caps_get_minimum_free_size(MALLOC_CAP_SPIRAM),
      heap_caps_get_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
      heap_caps_get_minimum_free_size(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
      heap_caps_get_largest_free_block(MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT),
      captures_.load(std::memory_order_relaxed)};
  const uint32_t heap_us = micros() - heap_start;
  const auto pixel_copy = panel_pixel_copy_snapshot();
  bool wifi_known = false, wifi_connected = false;
#ifdef USE_WIFI
  if (wifi::global_wifi_component) {
    wifi_known = true;
    wifi_connected = wifi::global_wifi_component->is_connected();
  }
#endif
  const auto extra = status_extra_ ? status_extra_() : DeferredStatus{};
  return [this, page, mode, physical, values, heap_us, pixel_copy, wifi_known, wifi_connected, extra, app, sha = std::string(sha)]() {
    // Signal strength comes from the sampling task; its age and query time are reported.
    // Neither the UI task nor this response waits for the C6.
    std::string rssi_json = "null";
    std::string rssi_age_json = "null";
    const uint32_t radio_us = radio_.took_us.load(std::memory_order_relaxed);
    if (wifi_known && wifi_connected && radio_.valid.load(std::memory_order_acquire)) {
      rssi_json = std::to_string(radio_.rssi.load(std::memory_order_relaxed));
      rssi_age_json = std::to_string(uint32_t(millis() - radio_.sampled_at_ms.load(std::memory_order_relaxed)));
    }
    const std::string wifi_json = wifi_known ? std::string(",\"wifi_connected\":") +
        (wifi_connected ? "true" : "false") + ",\"wifi_rssi\":" + rssi_json + ",\"wifi_rssi_age_ms\":" + rssi_age_json :
        ",\"wifi_connected\":null,\"wifi_rssi\":null,\"wifi_rssi_age_ms\":null";
    // Keep conversions in separate full expressions. extra() nests another
    // formatter, so a giant operator+ chain otherwise retains both frames.
    // PSRAM: a reply of 6-13 kB grew a std::string to an 8-16 kB internal block, polled by the gates during
    // boot, when internal RAM is tightest (psram_string.h; a boot aborted in operator new on 2026-10-29).
    panel_debug_memory::String result;
    result.reserve(16384);
    result += "{\"protocol\":2,\"lease_required\":true,\"width\":1024,\"height\":600,\"page\":";
    result += (quoted(page)).c_str();
    result += ",\"uptime_ms\":";
    result += (std::to_string(values[0])).c_str();
    result += ",\"physical_touch_active\":";
    result += (physical ? "true" : "false");
    result += ",\"physical_touch_idle_ms\":";
    result += (std::to_string(values[1])).c_str();
    result += ",\"boot_id\":";
    result += (std::to_string(values[2])).c_str();
    result += ",\"reset_reason\":";
    result += (std::to_string(values[3])).c_str();
    result += ",\"build_id\":";
    result += (quoted(build_id_)).c_str();
    result += ",\"firmware_elf_sha256\":";
    result += (quoted(sha)).c_str();
    result += ",\"firmware_version\":";
    result += (quoted(app->version)).c_str();
    result += ",\"build_date\":";
    result += (quoted(app->date)).c_str();
    result += ",\"build_time\":";
    result += (quoted(app->time)).c_str();
    result += ",\"idf_version\":";
    result += (quoted(app->idf_ver)).c_str();
    result += ",\"psram_free\":";
    result += (std::to_string(values[4])).c_str();
    result += ",\"psram_min_free\":";
    result += (std::to_string(values[5])).c_str();
    result += ",\"internal_free\":";
    result += (std::to_string(values[6])).c_str();
    result += ",\"internal_min_free\":";
    result += (std::to_string(values[7])).c_str();
    result += ",\"internal_largest_block\":";
    result += (std::to_string(values[8])).c_str();
    result += ",\"test_mode\":";
    result += (quoted(mode)).c_str();
    result += wifi_json.c_str();
    result += ",\"captures\":";
    result += (std::to_string(values[9])).c_str();
    result += ",\"status_main_heap_us\":";
    result += (std::to_string(heap_us)).c_str();
    result += ",\"pixel_dma_ready\":";
    result += (std::to_string(pixel_copy.ready)).c_str();
    result += ",\"pixel_dma_errors\":";
    result += (std::to_string(pixel_copy.dma_errors)).c_str();
    result += ",\"pixel_dma_copies\":";
    result += (std::to_string(pixel_copy.dma_copies)).c_str();
    result += ",\"pixel_dma_bytes\":";
    result += (std::to_string(pixel_copy.dma_bytes)).c_str();
    result += ",\"pixel_dma_us\":";
    result += (std::to_string(pixel_copy.dma_us)).c_str();
    result += ",\"pixel_staged_copies\":";
    result += (std::to_string(pixel_copy.staged_copies)).c_str();
    result += ",\"pixel_dma_burst\":";
    result += (std::to_string(pixel_copy.dma_burst)).c_str();
    result += ",\"pixel_ppa_burst\":";
    result += (std::to_string(pixel_copy.ppa_burst)).c_str();
    result += ",\"pixel_ppa_ready\":";
    result += (std::to_string(pixel_copy.ppa_ready)).c_str();
    result += ",\"pixel_ppa_errors\":";
    result += (std::to_string(pixel_copy.ppa_errors)).c_str();
    result += ",\"pixel_ppa_copies\":";
    result += (std::to_string(pixel_copy.ppa_copies)).c_str();
    result += ",\"pixel_ppa_bytes\":";
    result += (std::to_string(pixel_copy.ppa_bytes)).c_str();
    result += ",\"pixel_ppa_us\":";
    result += (std::to_string(pixel_copy.ppa_us)).c_str();
    result += ",\"pixel_mask_images\":";
    result += (std::to_string(pixel_copy.masked_images)).c_str();
    result += ",\"pixel_mask_pixels\":";
    result += (std::to_string(pixel_copy.masked_pixels)).c_str();
    result += ",\"pixel_mask_skipped\":";
    result += (std::to_string(pixel_copy.masked_skipped)).c_str();
    result += ",\"pixel_mask_opaque\":";
    result += (std::to_string(pixel_copy.masked_opaque)).c_str();
    result += ",\"pixel_mask_us\":";
    result += (std::to_string(pixel_copy.masked_us)).c_str();
    result += ",\"status_radio_us\":";
    result += (std::to_string(radio_us)).c_str();
    result += ",\"status_previous_send_us\":";
    result += (std::to_string(status_send_us_)).c_str();
    result += ",\"status_previous_send_bytes\":";
    result += (std::to_string(status_send_bytes_)).c_str();
    result += ",\"status_previous_send_age_ms\":";
    result += (std::to_string(status_send_count_ ? millis() - status_send_at_ms_ : 0)).c_str();
    result += ",\"status_send_count\":";
    result += (std::to_string(status_send_count_)).c_str();
    result += ",\"status_send_errors\":";
    result += (std::to_string(status_send_errors_)).c_str();
    if (extra) {
      const std::string more = extra();
      result.append(more.data(), more.size());
    }
    result += "}";
    return result;
  };
}

panel_debug_memory::String PanelDebug::ui_() const {
  // The whole tree in PSRAM, reserved once: it reached ~55 kB and internal RAM could not hold it during boot.
  panel_debug_memory::String out;
  out.reserve(64 * 1024);
  out += "{\"page\":";
  out += quoted(page_ ? page_() : "unknown");
  out += ",\"widgets\":[";
  size_t count = 0;
  bool truncated = false;
  auto visit = [&](auto &&self, lv_obj_t *object, const std::string &path, unsigned depth) -> void {
    if (!object || !lv_obj_is_visible(object)) return;
    if (count >= 256 || depth > 16 || out.size() > 55000) { truncated = true; return; }
    lv_area_t area;
    lv_obj_get_coords(object, &area);
    if (count++) out += ',';
    out += "{\"id\":" + quoted(path) + ",\"path\":" + quoted(path) + ",\"x\":" + std::to_string(area.x1) + ",\"y\":" +
           std::to_string(area.y1) + ",\"width\":" + std::to_string(lv_area_get_width(&area)) +
           ",\"height\":" + std::to_string(lv_area_get_height(&area)) + ",\"clickable\":" +
           (lv_obj_has_flag(object, LV_OBJ_FLAG_CLICKABLE) ? "true" : "false") +
           ",\"disabled\":" + (lv_obj_has_state(object, LV_STATE_DISABLED) ? "true" : "false") +
           ",\"checked\":" + (lv_obj_has_state(object, LV_STATE_CHECKED) ? "true" : "false") + ",\"type\":";
#if LV_USE_LABEL
    if (lv_obj_check_type(object, &lv_label_class)) {
      out += "\"label\",\"text\":";
      std::string text = lv_label_get_text(object);
      if (text.size() > 240) {
        size_t end = 240;
        while (end && (uint8_t(text[end]) & 0xc0) == 0x80) --end;
        text.resize(end);
      }
      out += quoted(text);
    } else
#endif
#if LV_USE_SLIDER
    if (lv_obj_check_type(object, &lv_slider_class)) {
      out += "\"slider\",\"value\":" + std::to_string(lv_slider_get_value(object)) +
             ",\"min\":" + std::to_string(lv_slider_get_min_value(object)) +
             ",\"max\":" + std::to_string(lv_slider_get_max_value(object));
    } else
#endif
#if LV_USE_BUTTON
    if (lv_obj_check_type(object, &lv_button_class)) out += "\"button\"";
    else
#endif
      out += "\"object\"";
    out += '}';
    const uint32_t children = lv_obj_get_child_count(object);
    for (uint32_t i = 0; i < children; ++i) self(self, lv_obj_get_child(object, i), path + '/' + std::to_string(i), depth + 1);
  };
  visit(visit, lv_screen_active(), "screen", 0);
  visit(visit, lv_layer_top(), "top", 0);
  visit(visit, lv_layer_sys(), "system", 0);
  out += "],\"truncated\":";
  out += truncated ? "true}" : "false}";
  return out;      // moved, not copied: the copy needed a second block as large as the tree
}
}  // namespace esphome::panel_debug
