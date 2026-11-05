#pragma once
// Where LVGL actually redraws: a 16 px grid counting, per cell, how many frames drew it. Pure C++ (tested on
// the Mac). Measured from LVGL's joined invalid areas at render start, so it shows drawing work, not pixels
// that changed colour, and it never draws anything on the panel itself.
#include <cstddef>
#include <cstdint>

namespace panel_debug_redraw {
constexpr int CELL = 16, WIDTH = 1024, HEIGHT = 600;
constexpr int COLS = (WIDTH + CELL - 1) / CELL, ROWS = (HEIGHT + CELL - 1) / CELL;
constexpr size_t MAX_FRAMES = 1024;

struct Area {
  int32_t x1, y1, x2, y2;  // inclusive, like lv_area_t
};

struct Grid {
  uint16_t cells[ROWS * COLS];
  uint32_t frame_px[MAX_FRAMES];  // drawn pixels of each recorded frame
  uint32_t stamp[ROWS * COLS];  // frame number that last counted each cell
  uint32_t frames, areas;
  uint64_t pixels;

  void clear() {
    for (auto &c : cells) c = 0;
    for (auto &s : stamp) s = 0;
    frames = areas = 0;
    pixels = 0;
  }
  // One rendered frame: each cell counts once however many areas cover it.
  void add_frame(const Area *list, size_t count) {
    const uint32_t generation = frames + 1;
    uint64_t frame = 0;
    for (size_t i = 0; i < count; ++i) {
      Area a = list[i];
      if (a.x1 < 0) a.x1 = 0;
      if (a.y1 < 0) a.y1 = 0;
      if (a.x2 > WIDTH - 1) a.x2 = WIDTH - 1;
      if (a.y2 > HEIGHT - 1) a.y2 = HEIGHT - 1;
      if (a.x2 < a.x1 || a.y2 < a.y1) continue;
      ++areas;
      frame += uint64_t(a.x2 - a.x1 + 1) * uint64_t(a.y2 - a.y1 + 1);
      for (int row = a.y1 / CELL; row <= a.y2 / CELL; ++row)
        for (int col = a.x1 / CELL; col <= a.x2 / CELL; ++col) {
          const int index = row * COLS + col;
          if (stamp[index] == generation) continue;
          stamp[index] = generation;
          if (cells[index] != UINT16_MAX) ++cells[index];
        }
    }
    if (frames < MAX_FRAMES) frame_px[frames] = uint32_t(frame);
    ++frames;
    pixels += frame;
  }
};
}  // namespace panel_debug_redraw
