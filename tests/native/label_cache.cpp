// Real LVGL, double-buffered direct mode: labels drawn through label_cache.h's whole-line masks must give exactly
// the pixels LVGL's own per-glyph drawing gives, refresh after refresh, over partial redraw areas.
//
// Usage: label-cache <mode> <seed> <steps>; mode 1 installs the label cache.
// Prints one line per refresh, "frame <step> <fnv1a of the presented buffer>", then a JSON summary.
#include "lvgl.h"
#include "label_cache.h"
#include <cassert>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <vector>

extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t size) { return std::malloc(size); }
void lv_free_core(void *ptr) { std::free(ptr); }
void *lv_realloc_core(void *ptr, size_t size) { return std::realloc(ptr, size); }
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *monitor) { std::memset(monitor, 0, sizeof(*monitor)); }
}

constexpr int W = 640, H = 400, LABELS = 48;
static uint32_t rng_state, tick;
static uint32_t rnd() { rng_state ^= rng_state << 13; rng_state ^= rng_state >> 17; rng_state ^= rng_state << 5; return rng_state; }
static int rnd(int n) { return int(rnd() % uint32_t(n)); }
static const uint8_t *presented;
static void flush(lv_display_t *display, const lv_area_t *, uint8_t *) {
  if (lv_display_flush_is_last(display)) presented = lv_display_get_buf_active(display)->data;
  lv_display_flush_ready(display);
}
static uint32_t fnv(const uint8_t *data, size_t size) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < size; ++i) h = (h ^ data[i]) * 16777619u;
  return h;
}
// Swedish and English words as on the panel, digits, symbols and a few glyphs Montserrat 14 lacks (placeholders).
static const char *const TEXTS[] = {
  "Gå hemifrån", "Hemmet", "Spelas nu", "10 min", "Hjulsta", "hinner inte", "gå nu", "fredag 30 oktober",
  "Cocktails", "Robin Kadir, Asme · Spotify på JBL Charge 4", "21:45", "8°", "Molnigt, 12 m/s", "WWWW iiii ....",
  "Ett längre stycke text som bryts över flera rader i en smal etikett", "A", "", "   ", "AV To Wa",
  "Kalendern kan inte uppdateras", "100 %", "Nattljus", "Ingen timer igång", "x\ny\nz"};
static constexpr int NTEXT = int(sizeof(TEXTS) / sizeof(TEXTS[0]));

int main(int argc, char **argv) {
  assert(argc == 4);
  const int mode = std::atoi(argv[1]);
  rng_state = uint32_t(std::strtoul(argv[2], nullptr, 10)) | 1u;
  const int steps = std::atoi(argv[3]);
  lv_init();
  lv_tick_set_cb([]() { return tick; });
  auto *display = lv_display_create(W, H);
  lv_display_set_color_format(display, LV_COLOR_FORMAT_RGB565);
  const size_t bytes = size_t(W) * H * 2;
  void *a = std::aligned_alloc(64, bytes), *b = std::aligned_alloc(64, bytes);
  lv_display_set_buffers(display, a, b, uint32_t(bytes), LV_DISPLAY_RENDER_MODE_DIRECT);
  lv_display_set_flush_cb(display, flush);
  lv_timer_pause(lv_display_get_refr_timer(display));
  if (mode & 1) label_cache::install();

  // A busy background so every blend mixes over different pixels.
  std::vector<uint8_t> store(bytes);
  auto *px = reinterpret_cast<uint16_t *>(store.data());
  for (int y = 0; y < H; ++y) for (int x = 0; x < W; ++x) px[y * W + x] = uint16_t((x * 37 + y * 11) ^ (x * y));
  lv_image_dsc_t bg{};
  bg.header.magic = LV_IMAGE_HEADER_MAGIC; bg.header.cf = LV_COLOR_FORMAT_RGB565;
  bg.header.w = W; bg.header.h = H; bg.header.stride = W * 2; bg.data_size = uint32_t(bytes); bg.data = store.data();
  lv_obj_t *screen = lv_screen_active();
  lv_obj_t *image = lv_image_create(screen);
  lv_image_set_src(image, &bg);
  lv_obj_t *labels[LABELS];
  for (auto &l : labels) {
    l = lv_label_create(screen);
    lv_obj_set_pos(l, rnd(W - 60), rnd(H - 20));
    if (rnd(3) == 0) lv_obj_set_width(l, 40 + rnd(200));
    lv_label_set_text(l, TEXTS[rnd(NTEXT)]);
    lv_obj_set_style_text_color(l, lv_color_make(uint8_t(rnd(256)), uint8_t(rnd(256)), uint8_t(rnd(256))), 0);
    lv_obj_set_style_text_opa(l, (lv_opa_t[]){255, 255, 200, 128, 60}[rnd(5)], 0);
    lv_obj_set_style_text_letter_space(l, rnd(6) - 3, 0);   // negative spacing makes glyphs overlap
    lv_obj_set_style_text_line_space(l, rnd(5) - 1, 0);
    lv_obj_set_style_text_align(l, (lv_text_align_t[]){LV_TEXT_ALIGN_LEFT, LV_TEXT_ALIGN_CENTER, LV_TEXT_ALIGN_RIGHT}[rnd(3)], 0);
  }
  for (int step = 0; step < steps; ++step) {
    tick += 60;
    const int action = rnd(10);
    lv_obj_t *l = labels[rnd(LABELS)];
    if (action == 0) lv_label_set_text(l, TEXTS[rnd(NTEXT)]);
    else if (action == 1) lv_obj_set_pos(l, rnd(W - 60), rnd(H - 20));
    else if (action == 2) lv_obj_set_style_text_opa(l, (lv_opa_t[]){255, 140, 30}[rnd(3)], 0);
    else if (action == 3) lv_obj_set_style_text_color(l, lv_color_make(uint8_t(rnd(256)), 40, 200), 0);
    // Several partial areas, as screen_swap and ordinary updates hand LVGL.
    const int areas = 1 + rnd(5);
    for (int i = 0; i < areas; ++i) {
      const int x = rnd(W - 20), y = rnd(H - 10);
      lv_area_t r{x, y, x + 10 + rnd(W - x - 10), y + 5 + rnd(H - y - 5)};
      lv_obj_invalidate_area(screen, &r);
    }
    if (rnd(8) == 0) lv_obj_invalidate(screen);
    lv_refr_now(display);
    std::printf("frame %d %08x\n", step, fnv(presented, bytes));
  }
  std::printf("{\"hits\":%u,\"builds\":%u,\"overlaps\":%u,\"seen\":%u,\"bytes\":%zu}\n", label_cache::stats.hits,
              label_cache::stats.builds, label_cache::stats.overlaps, label_cache::stats.seen, label_cache::stats.bytes);
  return 0;
}
