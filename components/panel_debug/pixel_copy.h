#pragma once
#include <stdint.h>
#include <stddef.h>
#include "lvgl.h"
#include "src/draw/sw/blend/lv_draw_sw_blend_private.h"
#ifdef __cplusplus
extern "C" {
#endif
void panel_pixel_copy_install(void);
typedef struct {
  uint32_t ready, dma_copies, dma_errors, staged_copies, dma_burst;
  uint64_t dma_bytes, dma_us;
  uint32_t ppa_ready, ppa_copies, ppa_errors, ppa_burst;
  uint64_t ppa_bytes, ppa_us, ppa_sync_us;   // ppa_sync_us: the cache write-back before each copy, part of ppa_us
  uint32_t masked_images;
  uint64_t masked_pixels, masked_skipped, masked_opaque, masked_us;
  // LVGL's colour-through-mask blends (text, rounded edges): how much of a page change is blending (page_draw.h).
  uint32_t mask_blends;
  uint64_t mask_pixels, mask_us;
} PanelPixelCopyStats;
PanelPixelCopyStats panel_pixel_copy_snapshot(void);
int panel_pixel_copy_bytes(void *destination, const void *source, size_t bytes);
lv_result_t panel_pixel_copy_rgb565(lv_draw_sw_blend_image_dsc_t *dsc);
lv_result_t panel_pixel_mask_rgb565(lv_draw_sw_blend_image_dsc_t *dsc);
// LVGL's colour blends (translucent fills, text, rounded edges) with lv_color_16_16_mix inlined (color_blend.h).
lv_result_t panel_color_opa_rgb565(lv_draw_sw_blend_fill_dsc_t *dsc);
lv_result_t panel_color_mask_rgb565(lv_draw_sw_blend_fill_dsc_t *dsc);
lv_result_t panel_color_mask_opa_rgb565(lv_draw_sw_blend_fill_dsc_t *dsc);
#ifdef __cplusplus
}
#endif
#define LV_DRAW_SW_RGB565_BLEND_NORMAL_TO_RGB565(dsc) panel_pixel_copy_rgb565(dsc)
#define LV_DRAW_SW_RGB565_BLEND_NORMAL_TO_RGB565_WITH_MASK(dsc) panel_pixel_mask_rgb565(dsc)
#define LV_DRAW_SW_COLOR_BLEND_TO_RGB565_WITH_OPA(dsc) panel_color_opa_rgb565(dsc)
#define LV_DRAW_SW_COLOR_BLEND_TO_RGB565_WITH_MASK(dsc) panel_color_mask_rgb565(dsc)
#define LV_DRAW_SW_COLOR_BLEND_TO_RGB565_MIX_MASK_OPA(dsc) panel_color_mask_opa_rgb565(dsc)
