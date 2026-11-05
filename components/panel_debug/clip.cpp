// Short clips of presented frames (POST /clip). The main task copies each newly presented frame's region into a
// free slot with the PPA (scaled when asked); this HTTP task JPEG-encodes the slots into a PSRAM store and sends
// it once the clip is over. A frame that arrives while every slot waits for the encoder is counted, not hidden.
#include "panel_debug.h"
#include "sdkconfig.h"
#include <algorithm>
#include <cstring>
#include <string>
#include "driver/jpeg_encode.h"
#include "driver/ppa.h"
#include "esp_flash_encrypt.h"
#include "esp_heap_caps.h"
#include "esphome/core/hal.h"
#include "esphome/core/log.h"
#include "freertos/task.h"
#include "lwip/sockets.h"

namespace esphome::panel_debug {
static const char *const TAG = "panel_debug";
using namespace panel_debug_clip;

namespace {
SemaphoreHandle_t ppa_done{};  // given by the PPA's done interrupt, once per transaction
bool ppa_finished(ppa_client_handle_t, ppa_event_data_t *, void *user_data) {
  static_cast<std::atomic<uint8_t> *>(user_data)->store(1, std::memory_order_release);  // the slot's `full`
  BaseType_t woken = pdFALSE;
  xSemaphoreGiveFromISR(ppa_done, &woken);
  return woken == pdTRUE;
}
}  // namespace

// Waits for the PPA copy still reading the previously presented buffer. Called before LVGL may draw into that
// buffer again (the next refresh start) and before the HTTP task frees the slots.
bool PanelDebug::clip_settle_(uint32_t timeout_ms) {
  if (!clip_.pending.load()) return true;
  if (xSemaphoreTake(ppa_done, pdMS_TO_TICKS(timeout_ms)) != pdTRUE) return false;
  clip_.pending.store(nullptr);
  return true;
}

void PanelDebug::clip_grab_() {
  // Main task only: from LVGL's refresh start (before it draws over anything) and from loop().
  clip_.grabbing.store(true);  // seq_cst with `active`: the HTTP task never frees a slot this is writing
  if (!clip_.active.load()) {
    clip_.grabbing.store(false);
    return;
  }
  const uint32_t now = micros();
  // The PPA may still read the frame presented last time; LVGL draws into that buffer once this returns.
  if (!clip_settle_(100)) {
    clip_.errors.fetch_add(1);
    clip_.active.store(false);  // a copy that never finishes: stop, never reuse its slot
    clip_.grabbing.store(false);
    return;
  }
  if (int32_t(now - clip_.until_us) >= 0) {
    clip_.active.store(false);
    clip_.grabbing.store(false);
    return;
  }
  const uint8_t *pixels = nullptr;
  uint32_t sequence = 0, presented_us = 0;
  if (!frame_ || !frame_(pixels, sequence, presented_us) || !pixels ||
      (clip_.have_last && sequence == clip_.last_sequence)) {
    clip_.grabbing.store(false);
    return;
  }
  if (clip_.have_last && sequence - clip_.last_sequence > 1) clip_.missed.fetch_add(sequence - clip_.last_sequence - 1);
  clip_.have_last = true;
  clip_.last_sequence = sequence;
  ClipSlot *slot = nullptr;
  for (int i = 0; i < clip_.slot_count && !slot; ++i)
    if (!clip_.slots[i].full.load(std::memory_order_acquire)) slot = &clip_.slots[i];
  if (!slot) {
    clip_.busy_drops.fetch_add(1);
    clip_.grabbing.store(false);
    return;
  }
  const auto &plan = clip_.plan;
  bool ok = false;
  // The PPA wants the output's address and size on whole L1 and L2 cache lines; the JPEG allocator only
  // promises enough bytes (2026-10-29: "out.buffer addr or out.buffer_size not aligned", no frames).
  constexpr size_t LINE = CONFIG_CACHE_L1_CACHE_LINE_SIZE > CONFIG_CACHE_L2_CACHE_LINE_SIZE ?
      CONFIG_CACHE_L1_CACHE_LINE_SIZE : CONFIG_CACHE_L2_CACHE_LINE_SIZE;
  const size_t usable = slot->allocated / LINE * LINE;
  const bool aligned = (reinterpret_cast<uintptr_t>(slot->pixels) % LINE) == 0 &&
                       usable >= size_t(plan.enc_w) * plan.enc_h * 2;
  if (clip_.ppa && aligned) {
    ppa_srm_oper_config_t config{};
    config.in.buffer = pixels;
    config.in.pic_w = WIDTH;
    config.in.pic_h = HEIGHT;
    config.in.block_w = plan.w;
    config.in.block_h = plan.h;
    config.in.block_offset_x = plan.x;
    config.in.block_offset_y = plan.y;
    config.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
    config.out.buffer = slot->pixels;
    config.out.buffer_size = usable;
    config.out.pic_w = plan.enc_w;
    config.out.pic_h = plan.enc_h;
    config.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
    config.scale_x = config.scale_y = 1.0f / float(plan.scale);
    // Asynchronous: the PPA reads the presented buffer while LVGL draws the next frame into the other one.
    // The presented buffer only becomes a drawing target after the next flip, at a refresh start that settles
    // this copy first (2026-10-29: blocking here cost 10-18 ms per frame and halved the frame rate).
    config.mode = PPA_TRANS_MODE_NON_BLOCKING;
    config.user_data = &slot->full;
    slot->sequence = sequence;
    slot->presented_us = presented_us;
    slot->grabbed_us = now;
    clip_.pending.store(slot);
    ok = ppa_do_scale_rotate_mirror(static_cast<ppa_client_handle_t>(clip_.ppa), &config) == ESP_OK;
    if (!ok) clip_.pending.store(nullptr);
    else {
      slot->cost_us = micros() - now;  // main-task time only; the copy itself runs on the PPA
      clip_.grabbed.fetch_add(1);
      clip_.grabbing.store(false);
      return;
    }
  } else {
    // No PPA (flash encryption, or a buffer it cannot use): plain row copies, every scale-th pixel.
    auto *out = reinterpret_cast<uint16_t *>(slot->pixels);
    const auto *in = reinterpret_cast<const uint16_t *>(pixels);
    for (int row = 0; row < plan.out_h; ++row) {
      const uint16_t *source = in + size_t(plan.y + row * plan.scale) * WIDTH + plan.x;
      uint16_t *target = out + size_t(row) * plan.enc_w;
      if (plan.scale == 1) std::memcpy(target, source, size_t(plan.out_w) * 2);
      else for (int col = 0; col < plan.out_w; ++col) target[col] = source[col * 2];
    }
    ok = true;
  }
  if (!ok) {
    slot->full.store(0);
    clip_.errors.fetch_add(1);
  } else {
    slot->sequence = sequence;
    slot->presented_us = presented_us;
    slot->grabbed_us = now;
    slot->cost_us = micros() - now;
    slot->full.store(1, std::memory_order_release);
    clip_.grabbed.fetch_add(1);
  }
  clip_.grabbing.store(false);
}

esp_err_t PanelDebug::clip_request_(httpd_req_t *request, const Plan &plan) {
  if (!frame_) return reply_(request, "503 Service Unavailable", "{\"error\":\"clips need the direct double-buffered display\"}");
  if (clip_.claimed.exchange(true)) return reply_(request, "409 Conflict", "{\"error\":\"a clip is already being recorded\"}");
  const size_t slot_bytes = size_t(plan.enc_w) * plan.enc_h * 2;
  const int slots = int(std::min<size_t>(SLOTS, (2560u * 1024u) / slot_bytes));
  const size_t free_psram = heap_caps_get_free_size(MALLOC_CAP_SPIRAM);
  // Keep 4 MB for the panel's own work (image downloads peaked there); the store takes up to 4 MB of the rest.
  const size_t reserve = 4u * 1024 * 1024 + size_t(std::max(slots, 0)) * slot_bytes + slot_bytes;
  const size_t store_capacity = free_psram > reserve ? std::min<size_t>(4u * 1024 * 1024, free_psram - reserve) : 0;
  if (slots < 2 || store_capacity < 256 * 1024) {
    clip_.claimed.store(false);
    return reply_(request, "503 Service Unavailable", "{\"error\":\"not enough PSRAM for this clip; use a smaller region or scale 2\"}");
  }
  if (!clip_.ppa && !esp_flash_encryption_enabled()) {
    ppa_client_config_t config{};
    config.oper_type = PPA_OPERATION_SRM;
    config.max_pending_trans_num = 1;
    ppa_client_handle_t client{};
    if (!ppa_done) ppa_done = xSemaphoreCreateBinary();
    ppa_event_callbacks_t callbacks{};
    callbacks.on_trans_done = ppa_finished;
    if (ppa_done && ppa_register_client(&config, &client) == ESP_OK) {
      if (ppa_client_register_event_callbacks(client, &callbacks) == ESP_OK) clip_.ppa = client;
      else ppa_unregister_client(client);
    }
  }
  bool ok = true;
  for (int i = 0; i < slots; ++i) {
    jpeg_encode_memory_alloc_cfg_t memory{};
    memory.buffer_direction = JPEG_ENC_ALLOC_INPUT_BUFFER;
    auto &slot = clip_.slots[i];
    slot.pixels = static_cast<uint8_t *>(jpeg_alloc_encoder_mem(slot_bytes, &memory, &slot.allocated));
    slot.full.store(0);
    if (!slot.pixels) ok = false;
    else std::memset(slot.pixels, 0, slot.allocated);  // the encoder's padding rows stay black
  }
  jpeg_encode_memory_alloc_cfg_t output_memory{};
  output_memory.buffer_direction = JPEG_ENC_ALLOC_OUTPUT_BUFFER;
  size_t output_capacity = 0;
  auto *output = ok ? static_cast<uint8_t *>(jpeg_alloc_encoder_mem(slot_bytes / 2 + 4096, &output_memory, &output_capacity)) : nullptr;
  auto *store = ok && output ? static_cast<uint8_t *>(heap_caps_malloc(store_capacity, MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)) : nullptr;
  jpeg_encoder_handle_t encoder{};
  jpeg_encode_engine_cfg_t engine{};
  engine.timeout_ms = 500;
  if (store && jpeg_new_encoder_engine(&engine, &encoder) != ESP_OK) encoder = nullptr;
  size_t stored = 0;
  uint32_t frames = 0, encode_errors = 0, encode_us_max = 0;
  uint64_t encode_us = 0;
  bool truncated = false;
  const uint32_t started_ms = millis();
  if (encoder) {
    clip_.plan = plan;
    clip_.slot_count = slots;
    clip_.have_last = false;
    clip_.grabbed.store(0);
    clip_.busy_drops.store(0);
    clip_.missed.store(0);
    clip_.errors.store(0);
    clip_.until_us = micros() + plan.duration_ms * 1000;
    clip_.active.store(true);
    const uint32_t deadline_ms = started_ms + plan.duration_ms + 1500;
    for (;;) {
      ClipSlot *next = nullptr;
      for (int i = 0; i < slots; ++i)
        if (clip_.slots[i].full.load(std::memory_order_acquire) && (!next || int32_t(clip_.slots[i].sequence - next->sequence) < 0))
          next = &clip_.slots[i];
      if (!next) {
        if (!clip_.active.load()) break;
        if (int32_t(millis() - deadline_ms) >= 0) break;
        vTaskDelay(1);
        continue;
      }
      jpeg_encode_cfg_t config{};
      config.width = plan.enc_w;
      config.height = plan.enc_h;
      config.src_type = JPEG_ENCODE_IN_FORMAT_RGB565;
      config.sub_sample = JPEG_DOWN_SAMPLING_YUV420;
      config.image_quality = plan.quality;
      uint32_t length = 0;
      const uint32_t began = micros();
      const esp_err_t error = jpeg_encoder_process(encoder, &config, next->pixels, next->allocated, output, output_capacity, &length);
      const uint32_t took = micros() - began;
      encode_us += took;
      encode_us_max = std::max(encode_us_max, took);
      if (error != ESP_OK) {
        ++encode_errors;
      } else if (stored + FRAME_HEADER + length > store_capacity) {
        truncated = true;
      } else {
        frame_header(store + stored, length, next->sequence, next->presented_us, next->grabbed_us, next->cost_us);
        std::memcpy(store + stored + FRAME_HEADER, output, length);
        stored += FRAME_HEADER + length;
        ++frames;
      }
      next->full.store(0, std::memory_order_release);
      if (truncated) break;
    }
  }
  clip_.active.store(false);
  while (clip_.grabbing.load()) vTaskDelay(1);
  if (!clip_settle_(500)) {
    // The PPA still owns a slot: leaking it is safe, freeing it is not.
    for (int i = 0; i < slots; ++i)
      if (&clip_.slots[i] == clip_.pending.load()) clip_.slots[i].pixels = nullptr;
    ESP_LOGW(TAG, "Clip: a PPA copy did not finish; its buffer is left allocated");
  }
  if (encoder) jpeg_del_encoder_engine(encoder);
  for (int i = 0; i < slots; ++i) {
    std::free(clip_.slots[i].pixels);
    clip_.slots[i].pixels = nullptr;
    clip_.slots[i].full.store(0);
  }
  clip_.slot_count = 0;
  std::free(output);
  esp_err_t result;
  if (!encoder) {
    result = reply_(request, "503 Service Unavailable", "{\"error\":\"no memory or JPEG encoder for the clip\"}");
  } else {
    std::string header = "{\"format\":\"hpclip1\",\"width\":" + std::to_string(plan.out_w) +
                         ",\"height\":" + std::to_string(plan.out_h) + ",\"encoded_width\":" + std::to_string(plan.enc_w) +
                         ",\"encoded_height\":" + std::to_string(plan.enc_h) + ",\"x\":" + std::to_string(plan.x) +
                         ",\"y\":" + std::to_string(plan.y) + ",\"region_width\":" + std::to_string(plan.w) +
                         ",\"region_height\":" + std::to_string(plan.h) + ",\"scale\":" + std::to_string(plan.scale) +
                         ",\"quality\":" + std::to_string(plan.quality) + ",\"duration_ms\":" + std::to_string(plan.duration_ms) +
                         ",\"frames\":" + std::to_string(frames) + ",\"grabbed\":" + std::to_string(clip_.grabbed.load()) +
                         ",\"missed_presentations\":" + std::to_string(clip_.missed.load()) +
                         ",\"busy_drops\":" + std::to_string(clip_.busy_drops.load()) +
                         ",\"grab_errors\":" + std::to_string(clip_.errors.load()) +
                         ",\"encode_errors\":" + std::to_string(encode_errors) +
                         ",\"encode_us_total\":" + std::to_string(encode_us) + ",\"encode_us_max\":" + std::to_string(encode_us_max) +
                         ",\"slots\":" + std::to_string(slots) + ",\"ppa\":" + (clip_.ppa ? "true" : "false") +
                         ",\"truncated\":" + (truncated ? "true" : "false") + ",\"store_bytes\":" + std::to_string(stored) + "}\n";
    httpd_resp_set_type(request, "application/octet-stream");
    httpd_resp_set_hdr(request, "Cache-Control", "no-store");
    const int disabled = 0;
    setsockopt(httpd_req_to_sockfd(request), IPPROTO_TCP, TCP_NODELAY, &disabled, sizeof(disabled));
    result = httpd_resp_send_chunk(request, header.data(), header.size());
    for (size_t at = 0; result == ESP_OK && at < stored; at += 64 * 1024)
      result = httpd_resp_send_chunk(request, reinterpret_cast<const char *>(store + at), std::min<size_t>(64 * 1024, stored - at));
    if (result == ESP_OK) result = httpd_resp_send_chunk(request, nullptr, 0);
    ESP_LOGI(TAG, "Clip %ux%u x%d: %u frames, %u missed, %u busy, %u bytes, %u ms", unsigned(plan.out_w), unsigned(plan.out_h),
             plan.scale, unsigned(frames), unsigned(clip_.missed.load()), unsigned(clip_.busy_drops.load()), unsigned(stored),
             unsigned(millis() - started_ms));
  }
  heap_caps_free(store);
  clip_.claimed.store(false);
  return result;
}
}  // namespace esphome::panel_debug
