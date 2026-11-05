#pragma once
#include "radio_cache.h"
#include "clip_format.h"
#include "touch_log.h"
#include <atomic>
#include <functional>
#include <memory>
#include <string>
#include <string_view>
#include "esphome/core/component.h"
#include "esp_http_server.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "lvgl.h"
#include "protocol.h"
#include "psram_string.h"

namespace esphome::panel_debug {
class PanelDebug : public Component {
 public:
  using Capture = std::function<bool(uint8_t *, size_t, uint32_t &)>;
  void set_token(const std::string &token) { token_ = token; }
  void set_port(uint16_t port) { port_ = port; }
  void set_build_id(const std::string &build_id) { build_id_ = build_id; }
  void set_capture_callback(Capture callback) { capture_ = std::move(callback); }
  void set_page_callback(std::function<const char *()> callback) { page_ = std::move(callback); }
  void set_touch_callback(std::function<void()> callback) { touch_ = std::move(callback); }
  void set_release_callback(std::function<void()> callback) { release_ = std::move(callback); }
  using DeferredStatus = std::function<std::string()>;
  void set_status_callback(std::function<DeferredStatus()> callback) { status_extra_ = std::move(callback); }
  // The /status reply itself is built in PSRAM (see status_()).
  using DeferredReply = std::function<panel_debug_memory::String()>;
  void set_test_callback(std::function<bool(const std::string &)> callback) { test_ = std::move(callback); }
  // The frame on screen now: its pixels (1024x600 RGB565), submission number and presentation time (us).
  using Frame = std::function<bool(const uint8_t *&, uint32_t &, uint32_t &)>;
  void set_frame_callback(Frame callback) { frame_ = std::move(callback); }
  // Settings as JSON, and a setter for one parsed value (panel_debug_settings::Key, a, b); both on the main task.
  using SettingsGet = std::function<std::string()>;
  using SettingsSet = std::function<bool(int, int, int)>;
  void set_settings_callbacks(SettingsGet get, SettingsSet set) {
    settings_get_ = std::move(get);
    settings_set_ = std::move(set);
  }
  // Called before App.setup() (see __init__.py) so the log ring holds every setup line.
  void start_log_capture();
  void setup() override;
  void loop() override;
  float get_setup_priority() const override { return -100.0f; }
  void dump_config() override;
  // Latest sampled signal strength for the Home Assistant sensor; NAN when unknown or stale.
  float radio_rssi() const;
 private:
  enum class Kind { STATUS, UI, CAPTURE, TOUCH, TEST, REPLAY, SETTINGS };
  struct Job {
    explicit Job(Kind kind) : kind(kind), done(xSemaphoreCreateBinary()) {}
    ~Job();
    Kind kind;
    SemaphoreHandle_t done;
    std::atomic<bool> cancelled{false};
    uint8_t *pixels{};
    size_t allocated{};
    uint32_t sequence{}, copy_us{}, started{}, next_step{}, queued_at{}, queue_us{};
    Gesture gesture{};
    int status{200};
    panel_debug_memory::String result;   // a /ui tree is ~55 kB: PSRAM (psram_string.h)
    DeferredReply deferred_status;
    std::string mode;
    uint32_t duration_ms{};
    panel_debug_touch::Sample *samples{};  // REPLAY, in PSRAM
    size_t sample_count{}, sample_index{};
    uint32_t last_apply{};
    int settings_key{}, settings_a{}, settings_b{};
  };
  static esp_err_t handle_(httpd_req_t *request);
  static int send_(httpd_handle_t server, int socket, const char *data, size_t size, int flags);
  esp_err_t request_(httpd_req_t *request);
  std::string lease_header_(httpd_req_t *request);
  esp_err_t lease_request_(httpd_req_t *request, const std::string &body);
  enum class Auth { OK, DENIED, CLOCK };
  Auth authenticate_(httpd_req_t *request, const std::string &path, std::string_view body);
  std::shared_ptr<Job> submit_(Kind kind, const Gesture &gesture = {}, const std::string &mode = "", uint32_t duration_ms = 0,
                               uint32_t wait_ms = 3500, std::shared_ptr<Job> prepared = {});
  void replay_step_(const std::shared_ptr<Job> &job);
  esp_err_t clip_request_(httpd_req_t *request, const panel_debug_clip::Plan &plan);
  void clip_grab_();
  void finish_(const std::shared_ptr<Job> &job, int status = 200);
  bool physical_touch_() const;
  void pointer_(int x, int y, bool pressed);
  DeferredReply status_() const;
  panel_debug_memory::String ui_() const;
  static esp_err_t reply_(httpd_req_t *request, const char *status, std::string_view body);
  // 200, or 503 when a diagnostic answered {"error":...}.
  static esp_err_t diagnostic_reply_(httpd_req_t *request, const panel_debug_memory::String &body);
  std::string token_;
  uint16_t port_{8080};
  std::string build_id_{"unversioned"};
  httpd_handle_t server_{};
  panel_debug_memory::String *reply_buffer_{};  // Used only by this server's HTTP task.
  // Signal strength is a ~30 ms RPC to the C6 on this board. Its own low-priority task
  // samples it (every second while /status is being polled, else once a minute), so
  // neither the UI task, the HTTP task nor the Home Assistant sensor ever waits for it.
  struct Radio {
    std::atomic<bool> valid{false};
    std::atomic<int32_t> rssi{0};
    std::atomic<uint32_t> sampled_at_ms{0}, took_us{0}, polled_at_ms{0};
    std::atomic<bool> polled{false};
  } radio_;
  static void radio_task_(void *arg);
  void start_radio_task_();
  // Previous /status socket enqueue, measured on the HTTP task. Not delivery/ACK time.
  uint32_t status_send_us_{}, status_send_bytes_{}, status_send_at_ms_{}, status_send_count_{}, status_send_errors_{};
  SemaphoreHandle_t mutex_{};
  std::shared_ptr<Job> pending_, active_;
  Capture capture_;
  std::function<const char *()> page_;
  std::function<void()> touch_;
  std::function<void()> release_;
  std::function<DeferredStatus()> status_extra_;
  std::function<bool(const std::string &)> test_;
  Frame frame_;
  SettingsGet settings_get_;
  SettingsSet settings_set_;
  // A clip: the main task copies each new presented frame into a free slot; the HTTP task encodes and stores it.
  struct ClipSlot {
    uint8_t *pixels{};
    size_t allocated{};
    std::atomic<uint8_t> full{0};
    uint32_t sequence{}, presented_us{}, grabbed_us{}, cost_us{};
  };
  struct Clip {
    std::atomic<bool> active{false}, grabbing{false}, claimed{false};
    panel_debug_clip::Plan plan;
    ClipSlot slots[panel_debug_clip::SLOTS];
    int slot_count{};
    uint32_t until_us{}, last_sequence{};
    bool have_last{false};
    std::atomic<uint32_t> grabbed{0}, busy_drops{0}, missed{0}, errors{0};
    void *ppa{};  // ppa_client_handle_t, registered on first use
    // The slot the PPA is still filling; its done callback marks it full.
    std::atomic<ClipSlot *> pending{nullptr};
  } clip_;
  bool clip_settle_(uint32_t timeout_ms);
  Lease lease_;
  ReplayGuard replay_;  // HTTP task only
  uint32_t boot_id_{};
  uint32_t test_until_{};
  std::string test_mode_{"idle"};
  std::atomic<bool> restore_requested_{false};
  std::atomic<uint32_t> restart_at_ms_{0};  // POST /restart: App.safe_reboot() once the reply is out
  lv_indev_t *pointer_device_{};
  lv_point_t point_{};
  bool pressed_{false};
  std::atomic<uint32_t> captures_{0};
  uint32_t last_capture_ms_{};
  uint32_t last_physical_touch_ms_{};
  uint32_t next_heap_log_ms_{5000};
};
}  // namespace esphome::panel_debug
