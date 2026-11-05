# Local MIPI DSI extension

Copied from ESPHome 2026.9.1's `components/mipi_dsi` (licence text retained in `LICENSE`).
The only runtime changes are configurable `frame_buffers` (component default 1; board.py selects 2),
and getters for the panel handle/color-transfer semaphore. Model definitions and initialization
remain upstream's. `bench/direct_display.h` installs the board's asynchronous LVGL DIRECT adapter.
Recheck these changes when updating ESPHome; do not patch the installed Python package.
