#include "pixel_copy.h"
#include "staged_copy.h"
#include "dma_copy.h"
#include "pixel_rect.h"
#include "masked_blend.h"
#include "color_blend.h"
#include "esp_heap_caps.h"
#include "esphome/core/log.h"
#include "esphome/core/defines.h"
#ifdef USE_ESP32_VARIANT_ESP32P4
#include "esphome/core/hal.h"
#include "esp_async_memcpy.h"
#include "esp_memory_utils.h"
#include "esp_flash_encrypt.h"
#include "soc/soc_caps.h"
#include "esp_cache.h"
#include "driver/ppa.h"
#include "sdkconfig.h"
#include "freertos/FreeRTOS.h"
#include "freertos/semphr.h"
#include "src/draw/lv_draw_buf_private.h"
#endif

namespace {
constexpr size_t CAPACITY = 8 * 1024;
uint8_t *scratch{};
bool installed{};
PanelPixelCopyStats stats{};
#ifdef USE_ESP32_VARIANT_ESP32P4
// PSRAM efficiency grows with the burst: the controller accepts at most 128 bytes.
// A 16-byte burst spent most of each access on command overhead (about 50 MB/s
// measured for both engines); the destination of a DMA write must follow its burst.
constexpr size_t DMA_BURST = 128, PPA_BURST = 128;
async_memcpy_handle_t dma{};
SemaphoreHandle_t completed{};
size_t source_alignment{16};
lv_draw_buf_copy_cb_t original_copy{};
ppa_client_handle_t ppa{};
constexpr size_t ppa_alignment = CONFIG_CACHE_L1_CACHE_LINE_SIZE > CONFIG_CACHE_L2_CACHE_LINE_SIZE ?
    CONFIG_CACHE_L1_CACHE_LINE_SIZE : CONFIG_CACHE_L2_CACHE_LINE_SIZE;
bool copy_rectangle(lv_draw_buf_t *dest, const panel_debug_copy::Rect &to, const void *source,
                    int source_width, int source_height, const panel_debug_copy::Rect &from) {
  if (!stats.ppa_ready || !dest || !source || dest->header.cf != LV_COLOR_FORMAT_RGB565 ||
      dest->header.stride != dest->header.w * 2 ||
      !panel_debug_copy::valid_rect_copy(dest->header.w, dest->header.h, dest->data_size, to,
                                       source_width, source_height, from) ||
      uint64_t(to.width) * to.height < 4096 ||
      ((reinterpret_cast<uintptr_t>(dest->data) | dest->data_size | dest->header.stride) & (ppa_alignment - 1)) ||
      (!esp_ptr_external_ram(source) && !esp_ptr_dma_capable(source)) ||
      (!esp_ptr_external_ram(dest->data) && !esp_ptr_dma_capable(dest->data)) || source == dest->data)
    return false;
  const uint32_t began = esphome::micros();
  // IDF invalidates the entire destination row window, including pixels outside
  // the copied rectangle. Commit all dirty CPU pixels there before invalidation.
  const auto preserved = esp_cache_msync(dest->data + to.y * dest->header.stride,
      size_t(to.height) * dest->header.stride, ESP_CACHE_MSYNC_FLAG_DIR_C2M);
  stats.ppa_sync_us += esphome::micros() - began;
  ppa_srm_oper_config_t config{};
  config.in.buffer = source;
  config.in.pic_w = source_width; config.in.pic_h = source_height;
  config.in.block_w = from.width; config.in.block_h = from.height;
  config.in.block_offset_x = from.x; config.in.block_offset_y = from.y;
  config.in.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
  config.out.buffer = dest->data; config.out.buffer_size = dest->data_size;
  config.out.pic_w = dest->header.w; config.out.pic_h = dest->header.h;
  config.out.block_offset_x = to.x; config.out.block_offset_y = to.y;
  config.out.srm_cm = PPA_SRM_COLOR_MODE_RGB565;
  config.scale_x = config.scale_y = 1;
  config.mode = PPA_TRANS_MODE_BLOCKING;
  const auto result = preserved == ESP_OK ? ppa_do_scale_rotate_mirror(ppa, &config) : preserved;
  if (result != ESP_OK) {
    ++stats.ppa_errors; stats.ppa_ready = 0;
    ESP_LOGW("pixel_copy", "PPA rectangle failed (%s); native copy retained", esp_err_to_name(result));
    return false;
  }
  ++stats.ppa_copies; stats.ppa_bytes += uint64_t(to.width) * to.height * 2;
  stats.ppa_us += esphome::micros() - began;
  return true;
}
bool copy_image_rectangle(lv_draw_sw_blend_image_dsc_t *dsc) {
  if (!stats.ppa_ready || dsc->dest_w <= 0 || dsc->dest_h <= 0) return false;
  auto *display = lv_display_get_default();
  auto *buffer = display ? lv_display_get_buf_active(display) : nullptr;
  if (!buffer || dsc->dest_stride != int(buffer->header.stride)) return false;
  const auto &area = dsc->relative_area;
  panel_debug_copy::Rect to{area.x1, area.y1, dsc->dest_w, dsc->dest_h};
  if (!panel_debug_copy::contains(buffer->header.w, buffer->header.h, to) ||
      dsc->dest_w != lv_area_get_width(&area) || dsc->dest_h != lv_area_get_height(&area) ||
      dsc->dest_buf != buffer->data + to.y * dsc->dest_stride + to.x * 2) return false;
  const int width = lv_area_get_width(&dsc->src_area), height = lv_area_get_height(&dsc->src_area);
  panel_debug_copy::Rect from{area.x1 - dsc->src_area.x1, area.y1 - dsc->src_area.y1,
                             dsc->dest_w, dsc->dest_h};
  if (dsc->src_stride != width * 2 || !panel_debug_copy::contains(width, height, from)) return false;
  const auto *source = static_cast<const uint8_t *>(dsc->src_buf) - from.y * dsc->src_stride - from.x * 2;
  return copy_rectangle(buffer, to, source, width, height, from);
}
bool IRAM_ATTR copy_done(async_memcpy_handle_t, async_memcpy_event_t *, void *context) {
  BaseType_t high = pdFALSE;
  xSemaphoreGiveFromISR(static_cast<SemaphoreHandle_t>(context), &high);
  return high == pdTRUE;
}
void copy_buffer(lv_draw_buf_t *dest, const lv_area_t *to, const lv_draw_buf_t *source, const lv_area_t *from) {
  if (to && from && source->header.cf == LV_COLOR_FORMAT_RGB565 && source->header.stride == source->header.w * 2 &&
      copy_rectangle(dest, {to->x1, to->y1, lv_area_get_width(to), lv_area_get_height(to)}, source->data,
                     source->header.w, source->header.h,
                     {from->x1, from->y1, lv_area_get_width(from), lv_area_get_height(from)})) return;
  if (to && from && dest->header.cf == LV_COLOR_FORMAT_RGB565 && source->header.cf == LV_COLOR_FORMAT_RGB565 &&
      to->x1 == 0 && from->x1 == 0 && to->x2 == int(dest->header.w) - 1 && from->x2 == to->x2 &&
      source->header.w == dest->header.w && dest->header.stride == dest->header.w * 2 &&
      source->header.stride == dest->header.stride && lv_area_get_height(to) == lv_area_get_height(from) &&
      panel_pixel_copy_bytes(dest->data + to->y1 * dest->header.stride,
                             source->data + from->y1 * source->header.stride,
                             size_t(lv_area_get_height(to)) * dest->header.stride)) return;
  original_copy(dest, to, source, from);
}
#endif
}
extern "C" PanelPixelCopyStats panel_pixel_copy_snapshot() { return stats; }
extern "C" int panel_pixel_copy_bytes(void *destination, const void *source, size_t bytes) {
#ifdef USE_ESP32_VARIANT_ESP32P4
  if (!stats.ready) return 0;
  if ((!esp_ptr_external_ram(source) && !esp_ptr_dma_capable(source)) ||
      (!esp_ptr_external_ram(destination) && !esp_ptr_dma_capable(destination))) return 0;
  const uint32_t started = esphome::micros();
  const auto result = panel_debug_copy::dma_chunks(destination, source, bytes, [](uint8_t *out, const uint8_t *in, size_t chunk) {
    if (esp_async_memcpy(dma, out, const_cast<uint8_t *>(in), chunk, copy_done, completed) != ESP_OK) return false;
    // No source/destination reuse until completion. The existing task watchdog bounds a hardware hang.
    xSemaphoreTake(completed, portMAX_DELAY);
    return true;
  }, source_alignment, DMA_BURST);
  if (result != panel_debug_copy::CopyResult::COMPLETE) {
    if (result == panel_debug_copy::CopyResult::FAILED) { ++stats.dma_errors; stats.ready = 0; }
    return 0;  // No pending transfer on enqueue failure; the caller can copy normally.
  }
  ++stats.dma_copies; stats.dma_bytes += bytes; stats.dma_us += esphome::micros() - started;
  return 1;
#else
  (void)destination; (void)source; (void)bytes;
  return 0;
#endif
}
extern "C" void panel_pixel_copy_install() {
  if (installed) return;
  installed = true;
#ifdef USE_ESP32_VARIANT_ESP32P4
  if (!esp_flash_encryption_enabled()) {
    ppa_client_config_t ppa_config{};
    ppa_config.oper_type = PPA_OPERATION_SRM;
    ppa_config.max_pending_trans_num = 1;
    static_assert(PPA_BURST == PPA_DATA_BURST_LENGTH_128);
    ppa_config.data_burst_length = PPA_DATA_BURST_LENGTH_128;
    if (ppa_register_client(&ppa_config, &ppa) == ESP_OK) { stats.ppa_ready = 1; stats.ppa_burst = PPA_BURST; }
    else { ++stats.ppa_errors; ESP_LOGW("pixel_copy", "PPA allocation failed"); }
  }
  async_memcpy_config_t config = ASYNC_MEMCPY_DEFAULT_CONFIG();
  config.backlog = 1;
  config.dma_burst_size = DMA_BURST;
  // TX does not require the RX burst alignment. Encrypted PSRAM keeps its stricter alignment.
  source_alignment = esp_flash_encryption_enabled() ? SOC_MEMSPI_ENCRYPTION_ALIGNMENT : 4;
  completed = xSemaphoreCreateBinary();
  if (completed && esp_async_memcpy_install_gdma_axi(&config, &dma) == ESP_OK) {
    stats.ready = 1;
    stats.dma_burst = DMA_BURST;
  } else {
    ++stats.dma_errors;
    ESP_LOGW("pixel_copy", "AXI DMA copy unavailable; native copy retained");
  }
  if (stats.ready || stats.ppa_ready) {
    auto *handlers = lv_draw_buf_get_handlers();
    original_copy = handlers->buf_copy_cb;
    handlers->buf_copy_cb = copy_buffer;
  }
  if (!stats.ready && !stats.ppa_ready)
#endif
  {
    scratch = static_cast<uint8_t *>(heap_caps_aligned_alloc(64, CAPACITY, MALLOC_CAP_INTERNAL | MALLOC_CAP_8BIT));
    if (!scratch) ESP_LOGW("pixel_copy", "Internal staging unavailable; native copy retained");
  }
}
extern "C" lv_result_t panel_pixel_copy_rgb565(lv_draw_sw_blend_image_dsc_t *dsc) {
  // The LVGL caller already established full opacity, no mask and normal RGB565 blending.
  // Accelerate only a contiguous full-width image. Crops, transparency and strided regions
  // keep the native implementation, preserving its clipping and alpha behavior.
  const bool contiguous = dsc->dest_w >= 640 && dsc->dest_h >= 16 &&
                          dsc->dest_stride == dsc->dest_w * 2 && dsc->src_stride == dsc->dest_stride;
  const size_t bytes = size_t(dsc->dest_stride) * dsc->dest_h;
#ifdef USE_ESP32_VARIANT_ESP32P4
  // Whole rows go by the AXI DMA first: 133 MB/s on the panel against PPA's 78 (2026-10-30, page_draw). PPA keeps
  // the rectangles that are not contiguous.
  if (contiguous && panel_pixel_copy_bytes(dsc->dest_buf, dsc->src_buf, bytes)) return LV_RESULT_OK;
  if (copy_image_rectangle(dsc)) return LV_RESULT_OK;
#endif
  if (!contiguous) return LV_RESULT_INVALID;
  if (!panel_pixel_copy_bytes(dsc->dest_buf, dsc->src_buf, bytes)) {
    if (!scratch) return LV_RESULT_INVALID;
    panel_debug_copy::staged_copy(dsc->dest_buf, dsc->src_buf, bytes, scratch, CAPACITY);
    ++stats.staged_copies;
  }
  return LV_RESULT_OK;
}
extern "C" lv_result_t panel_pixel_mask_rgb565(lv_draw_sw_blend_image_dsc_t *dsc) {
#ifdef USE_ESP32_VARIANT_ESP32P4
  if (!dsc->src_buf || !dsc->dest_buf || !dsc->mask_buf || dsc->dest_w <= 0 || dsc->dest_h <= 0 ||
      int64_t(dsc->dest_w) * dsc->dest_h < 4096 ||
      dsc->mask_stride < dsc->dest_w || dsc->src_stride < dsc->dest_w * 2 ||
      dsc->dest_stride < dsc->dest_w * 2 ||
      ((reinterpret_cast<uintptr_t>(dsc->dest_buf) | reinterpret_cast<uintptr_t>(dsc->src_buf) |
        dsc->src_stride | dsc->dest_stride) & 1)) return LV_RESULT_INVALID;
  const uint32_t began = esphome::micros();
  const auto result = panel_debug_copy::masked_rows<false>(static_cast<uint8_t *>(dsc->dest_buf), dsc->dest_stride,
      static_cast<const uint8_t *>(dsc->src_buf), dsc->src_stride, 0, dsc->mask_buf, dsc->mask_stride,
      dsc->dest_w, dsc->dest_h, lv_color_16_16_mix);
  ++stats.masked_images; stats.masked_pixels += result.pixels; stats.masked_skipped += result.skipped;
  stats.masked_opaque += result.opaque; stats.masked_us += esphome::micros() - began;
  return LV_RESULT_OK;
#else
  (void)dsc; return LV_RESULT_INVALID;
#endif
}

extern "C" lv_result_t panel_color_opa_rgb565(lv_draw_sw_blend_fill_dsc_t *dsc) {
  if (!dsc->dest_buf || dsc->dest_w <= 0 || dsc->dest_h <= 0) return LV_RESULT_OK;
  panel_debug_copy::color_opa_rows(static_cast<uint8_t *>(dsc->dest_buf), dsc->dest_stride, dsc->dest_w,
                                   dsc->dest_h, lv_color_to_u16(dsc->color), dsc->opa);
  return LV_RESULT_OK;
}
extern "C" lv_result_t panel_color_mask_rgb565(lv_draw_sw_blend_fill_dsc_t *dsc) {
  if (!dsc->dest_buf || !dsc->mask_buf || dsc->dest_w <= 0 || dsc->dest_h <= 0) return LV_RESULT_INVALID;
#ifdef USE_ESP32_VARIANT_ESP32P4
  const uint32_t began = esphome::micros();
#endif
  panel_debug_copy::color_mask_rows(static_cast<uint8_t *>(dsc->dest_buf), dsc->dest_stride, dsc->dest_w,
                                    dsc->dest_h, lv_color_to_u16(dsc->color), dsc->mask_buf, dsc->mask_stride);
  ++stats.mask_blends; stats.mask_pixels += uint64_t(dsc->dest_w) * dsc->dest_h;
#ifdef USE_ESP32_VARIANT_ESP32P4
  stats.mask_us += esphome::micros() - began;
#endif
  return LV_RESULT_OK;
}
extern "C" lv_result_t panel_color_mask_opa_rgb565(lv_draw_sw_blend_fill_dsc_t *dsc) {
  if (!dsc->dest_buf || !dsc->mask_buf || dsc->dest_w <= 0 || dsc->dest_h <= 0) return LV_RESULT_INVALID;
  panel_debug_copy::color_mask_opa_rows(static_cast<uint8_t *>(dsc->dest_buf), dsc->dest_stride, dsc->dest_w,
                                        dsc->dest_h, lv_color_to_u16(dsc->color), dsc->mask_buf,
                                        dsc->mask_stride, dsc->opa);
  return LV_RESULT_OK;
}
