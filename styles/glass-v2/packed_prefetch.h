#pragma once
#define HALLPANEL_ORB_PREFETCH 1
#include <functional>
#include <string>
#include <new>
#include "prefetch_slots.h"
#include "packed_atlas.h"
#include "virtual_frames.h"
#include "voice_wave.h"
#include "lvgl.h"
#include "src/draw/lv_image_decoder_private.h"
#include "src/libs/bin_decoder/lv_bin_decoder.h"
#include "src/libs/lz4/lz4.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#ifdef USE_ESP32
#include "esp_heap_caps.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#endif

namespace packed_orb {
class Prefetch {
 public:
  static constexpr size_t SLOTS = 3;
  unsigned method() const { return method_.load(); }
  void forget(uint32_t index) { pool_.forget(index); }
  // Per-image parameters of drawn voice waves (recipes whose second is WAVE).
  void set_waves(const voice_wave::Params *waves) { waves_ = waves; }
  void set_method(unsigned value) { if (value < 4) method_.store(value); }
  bool begin(const Atlas &atlas, lv_image_dsc_t *frames, size_t count, size_t listen, size_t morph, size_t speak,
             const Recipe *recipes = nullptr) {
#ifdef USE_ESP32
    if (task_) return true;
    if (count >= NO_FRAME) return false;  // Three 10-bit predictions fit one atomic request.
    atlas_ = &atlas; frames_ = frames; count_ = count; raw_count_ = atlas.count(); recipes_ = recipes;
    for (size_t i = 0; i < SLOTS; ++i) {
      auto &slot = pool_.slots[i];
      slot.data = static_cast<uint8_t *>(heap_caps_aligned_alloc(64, FRAME_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
      if (!slot.data || lv_draw_buf_init(&draws_[i], SIDE, SIDE, LV_COLOR_FORMAT_RGB565A8,
                                         SIDE * 2, slot.data, FRAME_BYTES) != LV_RESULT_OK) {
        free_buffers_();
        return false;
      }
    }
    if (recipes_) for (auto &raw : worker_raw_) {
      raw.data = static_cast<uint8_t *>(heap_caps_aligned_alloc(64, FRAME_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
      if (!raw.data) { free_buffers_(); return false; }
    }
    if (recipes_) worker_scratch_ = static_cast<uint8_t *>(heap_caps_aligned_alloc(
        64, INTERPOLATION_SCRATCH, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (recipes_) worker_sparse_.words = static_cast<ChangedWord *>(heap_caps_aligned_alloc(
        64, SPARSE_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    // The voice wave's working rows are read hundreds of thousands of times per frame: internal RAM.
    if (recipes_) worker_wave_ = static_cast<float *>(heap_caps_aligned_alloc(
        64, voice_wave::SCRATCH_BYTES, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (recipes_ && !worker_wave_) worker_wave_ = static_cast<float *>(heap_caps_aligned_alloc(
        64, voice_wave::SCRATCH_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
    worker_sparse_.capacity = SPARSE_CAPACITY;
    decoder_ = lv_image_decoder_create();
    if (!decoder_) { free_buffers_(); return false; }
    atlas_ = &atlas;
    frames_ = frames;
    count_ = count;
    listen_ = listen;
    morph_end_ = listen + morph;
    speak_end_ = morph_end_ + speak;
    if (xTaskCreatePinnedToCore([](void *arg) { static_cast<Prefetch *>(arg)->work_(); },
                               "orb-prefetch", 4096, this, 1, &task_, 0) != pdPASS) {
      lv_image_decoder_delete(decoder_);
      decoder_ = nullptr;
      free_buffers_();
      return false;
    }
    decoder_->user_data = this;
    decoder_->name = "orb_prefetch";
    lv_image_decoder_set_info_cb(decoder_, [](lv_image_decoder_t *decoder, lv_image_decoder_dsc_t *dsc, lv_image_header_t *header) {
      auto *self = static_cast<Prefetch *>(decoder->user_data);
      const auto index = self->index_(dsc->src);
      if (index == Pool::NONE) return LV_RESULT_INVALID;
      *header = self->frames_[index].header;
      return LV_RESULT_OK;
    });
    lv_image_decoder_set_open_cb(decoder_, [](lv_image_decoder_t *decoder, lv_image_decoder_dsc_t *dsc) {
      return static_cast<Prefetch *>(decoder->user_data)->open_(decoder, dsc);
    });
    lv_image_decoder_set_get_area_cb(decoder_, lv_bin_decoder_get_area);
    lv_image_decoder_set_close_cb(decoder_, [](lv_image_decoder_t *decoder, lv_image_decoder_dsc_t *dsc) {
      auto *self = static_cast<Prefetch *>(decoder->user_data);
      for (size_t i = 0; i < SLOTS; ++i) {
        if (dsc->decoded == &self->draws_[i]) {
          self->pool_.release(self->pool_.slots[i]);
#ifdef USE_ESP32
          xTaskNotifyGive(self->task_);
#endif
          return;
        }
      }
      if (self->recipes_ && self->index_(dsc->src) >= self->raw_count_) {
        auto *temporary = static_cast<Temporary *>(dsc->user_data);
        release_(temporary->scratch); release_(temporary->memory); release_(temporary->wave); delete temporary;
        return;
      }
      lv_bin_decoder_close(decoder, dsc);
    });
    ESP_LOGI("orb_prefetch", "%u decoded buffers, %u bytes; decode/interpolation worker on core 0", unsigned(SLOTS),
             unsigned((SLOTS + (recipes_ ? 2 : 0)) * FRAME_BYTES + (worker_sparse_.words ? SPARSE_BYTES : 0)));
    return true;
#else
    (void)atlas; (void)frames; (void)count; (void)listen; (void)morph; (void)speak; (void)recipes;
    return false;
#endif
  }
  std::function<std::string()> deferred_status() const {
    const auto held = held_.load();
    const auto hits = hits_.load(), misses = misses_.load(), decoded = decoded_.load(), errors = errors_.load(), took = last_decode_us_.load(), changes = source_changes_.load();
    const size_t virtual_frames = recipes_ && decoder_ ? count_ - raw_count_ : 0;
    const size_t bytes = decoder_ ? (SLOTS + (recipes_ ? 2 : 0)) * FRAME_BYTES + (worker_sparse_.words ? SPARSE_BYTES : 0) : 0;
    const bool probed = probe_complete_.load(std::memory_order_acquire);
    std::array<uint32_t, 12> samples{};
    if (probed) for (size_t i = 0; i < samples.size(); ++i) samples[i] = probe_samples_[i];
    const auto method = method_.load();
    const auto probe_fastest = probe_fastest_.load();
    const auto lead = miss_lead_max_.load(), unpack = last_unpack_us_.load(), mix = last_mix_us_.load();
    const uint32_t wave_copy = last_wave_copy_us_.load();
    const size_t scratch_bytes = worker_scratch_ ? INTERPOLATION_SCRATCH : 0;
    const auto trace = trace_;
    const auto trace_total = trace_total_;
    return [held, hits, misses, decoded, errors, took, bytes, changes, virtual_frames, probed, samples, method, probe_fastest, scratch_bytes, lead, unpack, mix, trace, trace_total, wave_copy]() {
      std::string probe = ",\"orb_interpolation_method\":" + std::to_string(method) +
                          ",\"orb_probe_fastest_method\":" + std::to_string(probe_fastest) +
                          ",\"orb_interpolation_scratch_bytes\":" + std::to_string(scratch_bytes);
      probe += ",\"orb_prefetch_miss_lead_max\":" + std::to_string(lead) +
               ",\"orb_prefetch_last_unpack_us\":" + std::to_string(unpack) +
               ",\"orb_prefetch_last_mix_us\":" + std::to_string(mix) +
               ",\"orb_wave_copy_us\":" + std::to_string(wave_copy) +
               ",\"orb_wave_prep_us\":" + std::to_string(voice_wave::last_prep_us.load(std::memory_order_relaxed)) +
               ",\"orb_wave_pixels_us\":" + std::to_string(voice_wave::last_pixels_us.load(std::memory_order_relaxed));
      if (probed) for (size_t i = 0; i < samples.size(); ++i)
        if (samples[i] != UINT32_MAX) probe += ",\"orb_probe_" + std::to_string(i) + "_us\":" + std::to_string(samples[i]);
      probe += ",\"orb_prefetch_trace\":{\"total\":" + std::to_string(trace_total) + ",\"events\":[";
      const auto count = std::min<uint64_t>(trace_total, trace.size());
      for (uint64_t i = trace_total - count; i < trace_total; ++i) {
        if (i != trace_total - count) probe += ',';
        probe += '[';
        const auto &event = trace[i % trace.size()];
        for (size_t j = 0; j < event.size(); ++j) {
          if (j) probe += ',';
          probe += std::to_string(event[j]);
        }
        probe += ']';
      }
      probe += "]}";
      return probe + ",\"orb_wave_held\":" + std::to_string(held) + ",\"orb_prefetch_hits\":" + std::to_string(hits) + ",\"orb_prefetch_fallbacks\":" + std::to_string(misses) +
             ",\"orb_prefetch_decoded\":" + std::to_string(decoded) + ",\"orb_prefetch_errors\":" + std::to_string(errors) +
             ",\"orb_prefetch_last_decode_us\":" + std::to_string(took) + ",\"orb_prefetch_bytes\":" + std::to_string(bytes) +
             ",\"orb_source_changes\":" + std::to_string(changes) + ",\"orb_virtual_frames\":" + std::to_string(virtual_frames) +
             ",\"orb_target_fps\":" + std::to_string(virtual_frames ? 60 : 25);
    };
  }
 private:
  using Pool = PrefetchSlots<SLOTS>;
  struct Raw { uint8_t *data{}; uint32_t index{Pool::NONE}; };
  struct Temporary { lv_draw_buf_t draw{}; uint8_t *memory{}; uint8_t *scratch{}; SparseMix sparse{}; float *wave{}; };
  static void interpolate_(unsigned method, uint8_t *out, const uint8_t *a, const uint8_t *b,
                           unsigned weight, uint8_t *scratch, SparseMix *sparse = nullptr,
                           uint32_t first = 0, uint32_t second = 1) {
    if (method == 0) interpolate565a8_original(out, a, b, SIDE * SIDE, weight);
    else if (method == 2 && scratch) interpolate565a8_staged(out, a, b, SIDE * SIDE, weight, scratch);
    else if (method == 3 && sparse) sparse->blend(out, a, b, SIDE * SIDE, weight, first, second);
    else interpolate565a8(out, a, b, SIDE * SIDE, weight);
  }
  bool decode_(uint32_t index, uint8_t *out) const {
    const auto *packet = atlas_->frame(index);
    return packet && LZ4_decompress_safe(reinterpret_cast<const char *>(packet + 12),
        reinterpret_cast<char *>(out), int(read32(packet + 4)), int(FRAME_BYTES)) == int(FRAME_BYTES);
  }
  Raw *raw_(uint32_t index, Raw *raw, const Raw *pinned = nullptr) const {
    for (unsigned i = 0; i < 2; ++i) if (raw[i].index == index) return &raw[i];
    auto *slot = pinned == &raw[0] ? &raw[1] : &raw[0];
    if (!decode_(index, slot->data)) return nullptr;
    slot->index = index;
    return slot;
  }
  bool prepare_(uint32_t index, uint8_t *out, Raw *raw, uint8_t *scratch, SparseMix *sparse,
                uint32_t *unpack = nullptr, uint32_t *mix = nullptr, float *wave = nullptr) const {
    const uint32_t began = esphome::micros();
    if (index < raw_count_) {
      const bool valid = decode_(index, out);
      if (unpack) *unpack = esphome::micros() - began;
      if (mix) *mix = 0;
      return valid;
    }
    if (!recipes_) return false;
    const auto &r = recipes_[index];
    const auto *a = raw_(r.first, raw);
    if (!a) return false;
    if (r.second == WAVE) {
      if (!waves_ || !wave) return false;
      const uint32_t drawn = esphome::micros();
      if (unpack) *unpack = drawn - began;
      // The line's hollow_00 is fully transparent: clearing writes the same bytes as copying it, without
      // reading 145 kB of PSRAM (the copy took 3.1 ms per wave on the panel, 2026-10-28). Checked once.
      if (clear_wave_.load(std::memory_order_relaxed) != r.first) {
        const uint8_t *alpha = static_cast<const uint8_t *>(a->data) + FRAME_BYTES / 3 * 2;
        const uint8_t *colors = static_cast<const uint8_t *>(a->data);
        bool clear = true;
        for (size_t i = 0; clear && i < FRAME_BYTES / 3; ++i) clear = alpha[i] == 0;
        for (size_t i = 0; clear && i < FRAME_BYTES / 3 * 2; ++i) clear = colors[i] == 0;
        if (clear) clear_wave_.store(r.first, std::memory_order_relaxed);
      }
      if (clear_wave_.load(std::memory_order_relaxed) == r.first) std::memset(out, 0, FRAME_BYTES);
      else std::memcpy(out, a->data, FRAME_BYTES);
      last_wave_copy_us_.store(esphome::micros() - drawn);
      voice_wave::clock_us = &esphome::micros;
      voice_wave::render(out, waves_[index], wave);
      if (mix) *mix = esphome::micros() - drawn;
      return true;
    }
    const auto *b = r.weight ? raw_(r.second, raw, a) : a;
    if (!b) return false;
    const uint32_t blended = esphome::micros();
    if (unpack) *unpack = blended - began;
    interpolate_(method_.load(), out, a->data, b->data, r.weight, scratch, sparse, r.first, r.second);
    if (mix) *mix = esphome::micros() - blended;
    return true;
  }
  uint32_t index_(const void *src) const {
    const auto address = reinterpret_cast<uintptr_t>(src), first = reinterpret_cast<uintptr_t>(frames_);
    if (address < first || (address - first) % sizeof(lv_image_dsc_t)) return Pool::NONE;
    const auto index = (address - first) / sizeof(lv_image_dsc_t);
    return index < count_ ? uint32_t(index) : Pool::NONE;
  }
  uint32_t next_(uint32_t index) {
    if (index != previous_) {
      source_changes_.fetch_add(1);
      step_three_ = index < listen_ && previous_ < listen_ &&
                    (index == previous_ + 3 || (step_three_ && index == 0 && previous_ + 3 >= listen_));
      previous_ = index;
    }
    return following_(index);
  }
  uint32_t following_(uint32_t index) const {
    if (index >= raw_count_) return recipes_[index].next == NO_FRAME ? Pool::NONE : recipes_[index].next;
    if (index < listen_) { const auto next = index + (step_three_ ? 3 : 1); return next < listen_ ? next : 0; }
    if (index < morph_end_) return index + 1;
    if (index < speak_end_) return index + 1 < speak_end_ ? index + 1 : uint32_t(morph_end_);
    return index + 1 < raw_count_ ? index + 1 : Pool::NONE;
  }
  lv_result_t open_(lv_image_decoder_t *decoder, lv_image_decoder_dsc_t *dsc) {
    const uint32_t started = esphome::micros();
    const auto index = index_(dsc->src);
    if (index == Pool::NONE) return LV_RESULT_INVALID;
    std::array<uint32_t, 9> trace{started, index, 0, 0, NO_FRAME, NO_FRAME};
    for (size_t i = 0; i < SLOTS; ++i) {
      const auto state = pool_.slots[i].state.load();
      const auto frame = pool_.slots[i].index.load();
      trace[6+i] = ((frame == Pool::NONE ? NO_FRAME : frame) << 3) |
                   (state == pool_.slots[i].state.load() ? state : 4U);
    }
    auto *slot = pool_.take(index);
    // A drawn voice wave the worker has not finished: show the last finished one again rather than draw it
    // here, which took ~20 ms on the UI task and starved the main loop. The wave then moves at the
    // worker's pace instead of stalling everything.
    bool held = false;
    if (!slot && recipes_ && index >= raw_count_ && recipes_[index].second == WAVE) {
      const auto last = last_wave_.load(std::memory_order_acquire);
      if (last != Pool::NONE && (slot = pool_.take(last))) { held = true; held_.fetch_add(1); }
    }
    trace[2] = slot ? 1 : 0;
    lv_result_t result;
    if (slot) {
      const size_t which = size_t(slot - pool_.slots.data());
      dsc->decoded = &draws_[which];
      if (!held) hits_.fetch_add(1);
      result = LV_RESULT_OK;
    } else {
      // A prediction miss or slow worker never waits or drops a frame. Use the proven decoder.
      misses_.fetch_add(1);
      if (index < raw_count_) result = lv_bin_decoder_open(decoder, dsc);
      else {
#ifdef USE_ESP32
        const auto temporary_method = method_.load();
        auto *temporary = new (std::nothrow) Temporary;
        if (temporary) temporary->memory = static_cast<uint8_t *>(heap_caps_aligned_alloc(
            64, 3 * FRAME_BYTES + (temporary_method == 3 ? SPARSE_BYTES : 0), MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        if (temporary && temporary_method == 2) temporary->scratch = static_cast<uint8_t *>(heap_caps_aligned_alloc(
            64, INTERPOLATION_SCRATCH, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
        const bool drawn_wave = recipes_[index].second == WAVE;
        if (temporary && drawn_wave) temporary->wave = static_cast<float *>(heap_caps_aligned_alloc(
            64, voice_wave::SCRATCH_BYTES, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT));
        bool valid = temporary && temporary->memory && (!drawn_wave || temporary->wave);
        if (valid) {
          if (temporary_method == 3) temporary->sparse = {
              reinterpret_cast<ChangedWord *>(temporary->memory + 3 * FRAME_BYTES), SPARSE_CAPACITY};
          Raw raw[2]{{temporary->memory + FRAME_BYTES, Pool::NONE}, {temporary->memory + 2 * FRAME_BYTES, Pool::NONE}};
          valid = prepare_(index, temporary->memory, raw, temporary->scratch, &temporary->sparse, nullptr, nullptr, temporary->wave) &&
                  lv_draw_buf_init(&temporary->draw, SIDE, SIDE, LV_COLOR_FORMAT_RGB565A8,
                                   SIDE * 2, temporary->memory, FRAME_BYTES) == LV_RESULT_OK;
        }
        if (valid) { dsc->decoded = &temporary->draw; dsc->user_data = temporary; result = LV_RESULT_OK; }
        else {
          if (temporary) { release_(temporary->scratch); release_(temporary->memory); release_(temporary->wave); delete temporary; }
          errors_.fetch_add(1); result = LV_RESULT_INVALID;
        }
#else
        result = LV_RESULT_INVALID;
#endif
      }
    }
#ifdef USE_ESP32
    trace[3] = slot ? 0 : esphome::micros() - started;
    auto next = next_(index);
    if (!slot && index >= raw_count_) {
      // A virtual miss decodes two raw inputs synchronously. Account for that
      // measured delay plus one ordinary render interval, instead of preparing
      // frames that have already expired by the next animation callback.
      const uint32_t lead = std::max<uint32_t>(1, std::min<uint32_t>(8, (esphome::micros() - started + 25000U) / 16667U));
      if (lead > miss_lead_max_.load()) miss_lead_max_.store(lead);
      for (unsigned step = 1; step < lead && next != Pool::NONE; ++step) next = following_(next);
    }
    if (next != Pool::NONE) {
      const auto second = following_(next);
      trace[4] = next; trace[5] = second == Pool::NONE ? NO_FRAME : second;
      const auto third = second == Pool::NONE ? Pool::NONE : following_(second);
      requested_.store(next | ((second == Pool::NONE ? NO_FRAME : second) << 10) |
                       ((third == Pool::NONE ? NO_FRAME : third) << 20), std::memory_order_release);
      xTaskNotifyGive(task_);
    }
    trace_[trace_total_++ % trace_.size()] = trace;
#endif
    return result;
  }
#ifdef USE_ESP32
  void probe_() {
    if (!recipes_) return;
    probe_samples_.fill(UINT32_MAX);
    const auto *a = raw_(0, worker_raw_.data());
    const auto *b = a ? raw_(1, worker_raw_.data(), a) : nullptr;
    if (!b) { errors_.fetch_add(1); return; }
    // Pool slots are still unpublished. No LVGL reader can own these outputs.
    bool exact = true;
    for (unsigned repeat = 0; repeat < 3; ++repeat) {
      for (unsigned method = 0; method < 4; ++method) {
        if (method == 2 && !worker_scratch_) continue;
        if (method == 3 && !worker_sparse_.words) continue;
        const uint32_t started = esphome::micros();
        auto *output = pool_.slots[std::min(method, 2U)].data;
        interpolate_(method, output, a->data, b->data, 128, worker_scratch_, &worker_sparse_);
        probe_samples_[repeat * 4 + method] = esphome::micros() - started;
        if (method) exact &= !std::memcmp(pool_.slots[0].data, output, FRAME_BYTES);
      }
    }
    unsigned selected = 0;
    uint64_t fastest = UINT64_MAX;
    for (unsigned method = 0; method < 4; ++method) {
      const uint64_t total = uint64_t(probe_samples_[method]) + probe_samples_[method + 4] + probe_samples_[method + 8];
      if (total < fastest) { fastest = total; selected = method; }
    }
    probe_fastest_.store(selected);
    // Measure every method, but keep the production experiment deterministic.
    // Startup cache noise is not evidence of which method wins during live drawing.
    unsigned production = 1;  // Controlled live A/B won over staged/sparse, despite the startup ranking.
    if (!exact) { errors_.fetch_add(1); production = 0; }
    method_.store(production);
    probe_complete_.store(true, std::memory_order_release);
  }
  void work_() {
    probe_();
    uint32_t active_request = Pool::NONE;
    for (;;) {
      ulTaskNotifyTake(pdTRUE, portMAX_DELAY);
      const auto request = requested_.exchange(Pool::NONE, std::memory_order_acquire);
      if (request != Pool::NONE) active_request = request;
      if (active_request == Pool::NONE) continue;
      for (unsigned shift : {0U, 10U, 20U}) {
        // A new UI request supersedes the remaining stale predictions. Finish the
        // current image atomically, then prioritize the latest visible timeline.
        if (requested_.load(std::memory_order_acquire) != Pool::NONE) break;
        const auto index = (active_request >> shift) & NO_FRAME;
        if (index == NO_FRAME) continue;
        const auto first = (active_request >> ((shift + 10) % 30)) & NO_FRAME;
        const auto second = (active_request >> ((shift + 20) % 30)) & NO_FRAME;
        // A skipped animation index changes the FIFO cursor's age ordering.
        // Never evict a nearer READY prediction while preparing another member.
        auto *slot = pool_.reserve(index, first == NO_FRAME ? Pool::NONE : first,
                                 second == NO_FRAME ? Pool::NONE : second);
        if (!slot) continue;
        const uint32_t start = esphome::micros();
        uint32_t unpack = 0, mix = 0;
        const bool valid = prepare_(index, slot->data, worker_raw_.data(), worker_scratch_, &worker_sparse_, &unpack, &mix, worker_wave_);
        last_unpack_us_.store(unpack); last_mix_us_.store(mix);
        last_decode_us_.store(esphome::micros() - start);
        if (valid) {
          decoded_.fetch_add(1); pool_.publish(*slot);
          if (recipes_[index].second == WAVE && index >= raw_count_) last_wave_.store(index, std::memory_order_release);
        }
        else { errors_.fetch_add(1); pool_.abort(*slot); }
      }
    }
  }
  TaskHandle_t task_{};
#endif
  void free_buffers_() {
    for (auto &slot : pool_.slots) { release_(slot.data); slot.data = nullptr; }
    for (auto &raw : worker_raw_) { release_(raw.data); raw.data = nullptr; }
    release_(worker_scratch_); worker_scratch_ = nullptr;
    release_(worker_sparse_.words); worker_sparse_.words = nullptr;
    release_(worker_wave_); worker_wave_ = nullptr;
  }
  static void release_(void *data) {
#ifdef USE_ESP32
    heap_caps_free(data);
#else
    std::free(data);
#endif
  }
  Pool pool_;
  std::array<lv_draw_buf_t, SLOTS> draws_{};
  const Atlas *atlas_{};
  lv_image_dsc_t *frames_{};
  size_t count_{}, raw_count_{}, listen_{}, morph_end_{}, speak_end_{};
  const Recipe *recipes_{};
  std::array<Raw, 2> worker_raw_{};
  uint8_t *worker_scratch_{};
  SparseMix worker_sparse_{};
  float *worker_wave_{};
  const voice_wave::Params *waves_{};
  std::array<uint32_t, 12> probe_samples_{};
  std::atomic<bool> probe_complete_{false};
  std::atomic<unsigned> method_{1};
  std::atomic<unsigned> probe_fastest_{0};
  std::array<std::array<uint32_t, 9>, 16> trace_{};
  uint64_t trace_total_{};
  std::atomic<uint32_t> miss_lead_max_{0}, last_unpack_us_{0}, last_mix_us_{0};
  mutable std::atomic<uint32_t> last_wave_copy_us_{0};   // written in the const prepare_
  mutable std::atomic<uint32_t> clear_wave_{UINT32_MAX};  // the frame found all zero, cleared instead of copied
  uint32_t previous_{Pool::NONE};
  bool step_three_{};
  lv_image_decoder_t *decoder_{};
  std::atomic<uint32_t> last_wave_{Pool::NONE}, held_{0};
  std::atomic<uint32_t> requested_{Pool::NONE}, hits_{0}, misses_{0}, decoded_{0}, errors_{0}, last_decode_us_{0}, source_changes_{0};
};
inline Prefetch prefetch;
}  // namespace packed_orb
