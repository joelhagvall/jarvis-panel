# Page-change redraw and backdrop cover (2026-10-26)

## Findings

- **Hidden full-screen fill.** LVGL 9.5's `lv_image` inherits `lv_obj`'s cover check, which
  answers "does not cover" for any object without its own opaque bg; the image check itself
  never answers "covers". Every redrawn area of a page with an opaque full-screen picture
  therefore filled the screen's bg colour first, then drew the picture over it. Native LVGL
  counted 614,400 filled pixels per full redraw. On the panel this is a CPU fill into PSRAM,
  also under every orb frame.
- **Full redraw on every tab change.** `board.py` forces `page.show` to `NONE`, so ESPHome calls
  `lv_screen_load`, which invalidates the whole new screen as its last step. Gå hemifrån and
  Hemmet share the same `bg`, so only their widgets differ. The double-buffered direct mode then
  also copies the whole screen into the other buffer on the next refresh (`refr_sync_areas`).
  That copy happened in a refresh that drew nothing, which no responsiveness series recorded.

## Change

`styles/glass-v2/screen_swap.h`, installed by `gen.py`'s `bar_hook` on every page:

1. `cover_backdrop()` reports the cover LVGL's image check intended for an opaque, untransformed
   RGB565/RGB888 picture, so LVGL starts drawing at the picture. Shown pixels do not change.
2. Between screens whose first child is the same opaque full-screen picture, the load's
   full-screen area is replaced at `REFR_START` by `lv_obj_invalidate()` of every visible object
   that draws something on both screens, plus every other invalidation made meanwhile. Nested
   loads, animations, another backdrop, a second full-screen area or a visible transformed layer
   keep LVGL's own full redraw. `/status` exposes `screen_swap_*` counters.

`bench/responsiveness.h` adds the diagnostic, never-gated `empty_refresh_work` series.

## Verification

- `tools/check_screen_swap.py --lvgl-dir <firmware LVGL 9.5.0 source> --out NEW` (no panel):
  26,658 presented frames over 20 seeds bit-identical to LVGL's own full redraw, 3,249 page
  changes through the new path, ASan/UBSan clean. The simulator's LVGL is built with
  `LV_COLOR_16_SWAP`, which makes partial and full redraws differ by itself; the check builds
  its own LVGL with `tests/native/lv_conf_screen_swap.h`.
- Simulator with the real pages: the path applied on 99 of 100 tab changes; it redraws 358,612
  (to Hemmet) and 370,240 (to Gå hemifrån) of 614,400 pixels. Mac times are not P4 times.
- Physical, same protocol `tabs-20-orb-10s-v2`, before = verified build `1287f8e6`, after = this
  candidate via `tools/flash_panel.sh` (visual check, responsiveness, throughput and soak passed):

| Metric | Before p50 / p95 / max | After p50 / p95 / max |
| --- | --- | --- |
| navigation.remote_release_ms | 63.68 / 65.64 / 65.85 | 49.50 / 54.17 / 54.45 |
| navigation.render_work_ms | 2.37 / 50.07 / 51.21 | 2.25 / 38.79 / 39.53 |
| navigation.refresh_work_ms | 15.85 / 63.17 / 64.49 | 15.68 / 49.48 / 51.66 |
| animation.render_work_ms | 8.71 / 9.29 / 14.43 | 7.10 / 7.57 / 12.13 |
| animation.fps | 24.81 | 24.81 |

Raw reports: `.panel-ci/screen-swap-20261026/before-responsiveness/` and
`.panel-ci/responsiveness/run-20261026-093744-809042/`. On the panel the last applied change
redrew 360,920 pixels in 19 areas; deciding them took 5,163 us on the UI task, inside the
measured release time. Software timing does not establish finger-to-photon latency. The
accepted baselines were not replaced.

## Decision cost

In LVGL 9.5 every `lv_obj_invalidate()` also walks the active, previous and layer trees for blurred
widgets. The first version called it for every drawn widget of both pages and merged areas with
64-bit pair scans. The second computes the same areas with `lv_obj_area_is_visible` (falling back
on any blur/drop-shadow style), skips children already inside a drawn parent, merges with 32-bit
arithmetic and cached best partners, and allows 24 areas. Still bit-identical in
`tools/check_screen_swap.py`. On the panel (`run-20261026-103606-099558`, all flash gates passed):

| | First version | Second version |
| --- | --- | --- |
| Old-page collection at unload | not measured | 21 us |
| Decision at refresh start (incl. LVGL layout of the new page) | 5,163 us | 2,723 us |
| Areas / pixels redrawn | 19 / 360,920 | 24 / 345,844 |
| remote_release p50 / p95 / max | 49.50 / 54.17 / 54.45 ms | 49.61 / 52.13 / 53.57 ms |

The release median did not move: it sits at three display frames (3 x 16.56 ms = 49.7 ms). The
release redraw completes on a frame boundary, so a saving shows in the median only once it brings
the redraw under the previous boundary (33.1 ms).
