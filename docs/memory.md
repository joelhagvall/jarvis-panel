# Internal RAM budget

The ESP32-P4's internal RAM is the scarce memory; PSRAM (32 MB) is not. On 2026-11-03 the panel had ~100 kB
internal free in normal running (300 kB on 2026-10-24), lowest 46 kB since boot, largest block 42–52 kB.

## Where it goes (build 2b56d71c, 2026-11-02 21:57)

| Part | Size |
|---|---|
| IRAM code (`.iram0.text`) | 73 kB |
| Static data + bss (`.dram0.data` + `.dram0.bss`) | 148 kB: ESP-IDF/Wi-Fi ~34 kB, ESPHome core ~31 kB, panel code ~45 kB, components' storage |
| Heap allocated at runtime | ~337 kB |

LVGL's heap, draw buffers, images and the orb are already in PSRAM (ESPHome's `lv_custom_mem_alloc`). ESPHome's
`psram:` forces `CONFIG_SPIRAM_USE_CAPS_ALLOC`, so every plain `malloc`/`new` (std::string, vectors, HA text
states, task stacks from `xTaskCreate`) lands in internal RAM. No TLS is used (only http to HA), so mbedTLS
buffers are not part of it.

## Rules

- Cold, zero-initialised statics carry `EXT_RAM_BSS_ATTR` (enabled by `CONFIG_SPIRAM_ALLOW_BSS_SEG_EXTERNAL_MEMORY`
  in `board_config/hardware.py`). Headers that may be compiled on the host define it empty when `esp_attr.h` is
  missing (see `calendar_color.h`). Never on objects with `std::atomic`, ISR access, or DMA buffers.
- A background task that never touches flash with the cache off gets its stack in PSRAM with
  `xTaskCreatePinnedToCoreWithCaps(..., MALLOC_CAP_SPIRAM | MALLOC_CAP_8BIT)` (artwork, vinyl-build, radio-rssi).
  Per-frame workers (vinyl, orb-prefetch) keep internal stacks. Such a task is never deleted with `vTaskDelete`.
- `tests/test_memory_placement.py` keeps these in place.

## Measuring

```sh
tools/panel memory          # internal/DMA/PSRAM free, lowest since boot, largest block, boot low point
tools/panel tasks           # stack margin per task
tools/panel health          # all limits in one report
```

Static size per build: `riscv32-esp-elf-size -A .panel-ci/elf/<build id>.elf` (`.dram0.data`, `.dram0.bss`);
`riscv32-esp-elf-nm -S --size-sort -C` lists the biggest symbols. The toolchain is under
`~/Library/Caches/esphome/idf/tools/riscv32-esp-elf/`. The lowest-since-boot value is usually set by the
post-flash gates in the first 5 minutes; normal running sits higher. The passive monitor pushes "minne lågt"
only after three samples in a row under the floor (docs/panel-monitor.md).

## Log

| Date | Change | Static internal | Internal free (idle) |
|---|---|---|---|
| 2026-11-02 20:22 | Calendar week details: `static std::string detail[7][6][4]` (+4 kB static) | 221.3 kB | ~100 kB |
| 2026-11-03 06:50 | Build 21e75751: recorder, boot cache, fixtures and calendar details to `.ext_ram.bss` (17.1 kB); artwork, vinyl-build and radio-rssi stacks in PSRAM (13.3 kB) | 204.2 kB | 133 kB after all gates (lowest 88 kB, largest block 96 kB; before: 98 / 46 / 51 kB) |
| 2026-11-04 | `voice_go` (show_page by voice, cd9d517) as one lambda instead of nine `if` branches (~90 ESPHome actions, 1.96 kB `.dram0.bss` plus their heap objects); cd9d517 had left the panel idling at 96.0 kB, under the 96 KiB monitor floor, and pushed "minne lågt" | see `.dram0.bss` of the next build | see below |

Every ESPHome action, condition and `if` branch is a heap object in internal RAM plus static storage (~20–60 B each): a
per-item list of branches should be one lambda (`tests/test_show_page.py` guards `voice_go`).
The flash gates sample in the first minutes, when internal free sits ~10 kB above where it settles after a voice turn,
so cd9d517 passed them and still pushed "minne lågt" 30 min later: after a change that adds many actions, compare
`.dram0.bss` with the previous ELF in `.panel-ci/elf/`. Build 1661d1a9 (2026-11-04 14:48): 116.4 kB `.dram0.bss` (−1.6 kB), 109.5 kB free
at 25 min idle (before: 96.0 kB).

Not tried yet: `CONFIG_SPIRAM_USE_MALLOC` with a small `CONFIG_SPIRAM_MALLOC_ALWAYSINTERNAL` (plain `malloc` above
the threshold to PSRAM). It is the largest unmeasured lever, but it changes where render-path allocations land:
flash it on its own and compare responsiveness and throughput against the immediately prior build.
