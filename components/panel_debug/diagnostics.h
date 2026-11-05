#pragma once
// On-demand diagnostics behind the debug server: logs that survive a restart, the last crash, per-task CPU and
// stack, where LVGL redraws, and the real finger's last samples. See docs/remote-panel.md.
#include <cstdint>
#include <string>
#include "psram_string.h"
#include "lvgl.h"

namespace esphome::panel_debug::diag {
// Before App.setup(): keeps the previous boot's ring, then records every log line of this boot.
void start_logs(uint32_t boot_id, const std::string &build_id);
// On the main task during setup: copies ESPHome's own record of a crash on the previous boot.
void capture_crash_record();
panel_debug_memory::String logs_json();
panel_debug_memory::String crash_json();
// From setup: samples internal and DMA RAM every 5 ms for the first 40 s of the boot (see /tasks "boot_heap").
void start_boot_heap_watch();
// HTTP task. Blocks for window_ms while a timer samples which task each core runs.
panel_debug_memory::String tasks_json(uint32_t window_ms);
// Main task, once LVGL exists. Records the physical touchscreen's reads (never the virtual pointer).
void install_touch_recorder(lv_indev_t *virtual_pointer);
panel_debug_memory::String touches_json();
// Main task, once LVGL exists.
bool install_redraw_map(lv_display_t *display);
// HTTP task. Blocks for window_ms while LVGL's drawn areas are counted.
panel_debug_memory::String redraw_json(uint32_t window_ms);
}  // namespace esphome::panel_debug::diag
