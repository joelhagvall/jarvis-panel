#pragma once
#define HALLPANEL_ASYNC_ARTWORK 1
#include <atomic>
#include <functional>
#include <string>
#include "esphome/core/component.h"
#include "esphome/core/automation.h"
#include "esphome/components/image/image.h"
#include "esphome/components/runtime_image/runtime_image.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "contract.h"
namespace esphome::async_artwork {
class AsyncArtwork : public PollingComponent, public image::Image {
 public:
  explicit AsyncArtwork(const std::string &url);
  void setup() override;
  void loop() override;
  void set_url(const std::string &url);
  void update() override;
  // Download the next cover ahead into the free buffer, never published by itself; update() for the same URL then
  // publishes it at once (HA's artwork_next, docs/now-playing.md). Ignored while a download runs.
  void prefetch(const std::string &url);
  // Priority 1 only while Spelas nu shows the record, else 0: at 1 a song change during the voice orb's soak
  // competed with the orb prefetch worker (also 1) and the orb dropped frames (2026-10-29).
  void set_foreground(bool on){if(task_)vTaskPrioritySet(task_,on?1:0);}
  uint16_t *small_data() const {return buffers_[handoff_.shown]+artwork_contract::PIXELS;}
  uint64_t picture_identity() const {return handoff_.shown>=0?picture_identity_[handoff_.shown]:0;}
  template<class F>void add_on_finished_callback(F &&fn){finished_.add(std::forward<F>(fn));}
  template<class F>void add_on_error_callback(F &&fn){error_.add(std::forward<F>(fn));}
  // The cover downloaded ahead, for the record to build ahead (vinyl.h source_ahead); valid until the next download.
  template<class F>void add_on_ahead_callback(F &&fn){ahead_ready_.add(std::forward<F>(fn));}
  const uint16_t *ahead_pixels() const {return ahead_.slot>=0?buffers_[ahead_.slot]:nullptr;}
  uint64_t ahead_identity() const {return ahead_.slot>=0?picture_identity_[ahead_.slot]:0;}
  std::function<std::string()> deferred_status() const;
 private:
  void worker();
  bool download();
  runtime_image::RuntimeImage decoder_;
  uint16_t *buffers_[2]{};
  artwork_contract::Handoff handoff_;
  int target_{};
  std::string prefix_,url_,job_url_;
  bool valid_{};
  TaskHandle_t task_{};
  std::atomic<bool> requested_{false};
  std::atomic<int> done_{0};
  bool busy_{};  // main thread only
  bool prefetching_{},pending_update_{};  // main thread: the running job is ahead; an update waits behind it
  bool job_ahead_{};  // the worker's copy, written before requested_'s release
  artwork_contract::Ahead ahead_;          // main thread
  // The cover shown before the current one, kept in a third buffer so "föregående" needs no download (2026-10-30).
  // Main thread; buffers swap pointers, never pixels.
  uint16_t *behind_{};
  uint64_t behind_identity_{};
  std::string behind_url_,shown_url_;
  // Per buffer: a download ahead must not change the identity of the picture still shown. The worker writes its
  // target's before done's release; the callback reads the shown one after acquire.
  uint64_t picture_identity_[2]{};
  std::atomic<uint32_t> requests_{0},errors_{0},bytes_{0},worker_us_{0},publish_us_{0},aheads_{0},ahead_hits_{0},behind_hits_{0};
  CallbackManager<void()> finished_,error_,ahead_ready_;
};
extern AsyncArtwork *instance;
}  // namespace esphome::async_artwork
