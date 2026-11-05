# Frame-clock pacing, copy bursts and the status tail, 2026-10-25

Starting point: build `f66ef313c9f08675a8025f47faad178d`, verified in
`.panel-ci/run-20261025-055129-141946/`. Its production listening orb measured
59.98 source changes/s, cadence p50/p95/p99/max 16.53 / 18.72 / 20.34 / 26.08 ms
and 0.67% of intervals over 25 ms (`throughput/throughput.json`). The fixed
`tabs-20-orb-10s-v2` workload measured press 17.81 / 18.44 ms and release
78.74 / 85.66 ms (p50 / p95); status HTTP p50 / p95 was 39.66 / 107.38 ms.
Every number below is a software timestamp on the panel or the Mac. None
establishes optical scanout, finger-to-photon latency or acoustic quality.

## Diagnosis from the retained per-frame samples

The 602 consecutive orb refreshes in that report show two patterns, not noise:

- Consecutive completion intervals alternate about 15.5 / 17.5 ms. The LVGL
  animation advances its image on a 1 ms tick, so 2600 ms / 156 images steps
  by 16 or 17 ms, never 16.67.
- Every 155–160 frames the flush wait jumps from under 3 ms to 11–14 ms, the
  refresh takes 20–23 ms and the next interval is 25–26 ms (frames 120, 282,
  442 and 591 of that run). The configured scan is 52 MHz / (1354 × 636) =
  60.385 Hz, 16.560 ms per frame. An animation step of 16.667 ms drifts 0.107 ms
  per frame against it; after about 155 frames a submission misses its frame
  boundary and waits a whole frame. That is the measured 0.3–0.7% jank and the
  26 ms maximum, and it would recur forever: the two clocks were free-running.

The navigation records also attribute the release redraw: after a tab release the
page render took 57–71 ms, of which the PPA spent about 24 ms on the 1.2 MB
background copy, and the next refresh spent another ~24 ms copying the same
full screen into the other buffer (LVGL's direct-mode sync). Both engines ran
at roughly 40–50 MB/s with 16-byte bursts: 390,980,488 PPA bytes in 9.53 s and
74,956,800 DMA bytes in 1.41 s over the panel's uptime. The ESP32-P4 PSRAM
controller accepts bursts up to 128 bytes; the IDF PPA driver's own default is
128. The status tail was the C6 signal-strength RPC: 9 of 80 status requests in
the 60 fps run carried `status_radio_us` near 29.7 ms, and exactly those had
round trips of 44–60 ms versus at most 35 ms for the rest.

## Candidate

1. **Frame-clock pacing.** The DPI frame interrupt (`on_frame_buf_complete`,
   called by IDF at every frame boundary) now counts boundaries and
   presentations in `bench/vsync_clock.h` and wakes the main loop. The
   production orb chooses its image from the boundary count: exactly one new
   image per display frame, a loop wraps, a one-shot holds its last image, a
   deleted animation (frozen golden scenes) stays frozen, and a stalled clock
   (no boundary for 100 ms) hands control back to the LVGL animation value, as
   does the simulator. Listen uses 157 images and speak 121, so one image per
   16.560 ms frame lasts 2600.0 and 2003.8 ms; think / morph / settle keep 51 /
   39 / 36 (844.6 / 645.9 / 596.2 ms). Durations stay within 0.7% of the
   authored 2600 / 850 / 650 / 2000 / 600 ms, and the 25 fps native benchmark
   timeline and golden scenes are unchanged. `packed_orb::restart` replaces
   `lvgl.animimg.start` for the orb states, so morph and settle start from
   image 0 at the next boundary.
2. **Presentation telemetry.** Status carries `presentation`: boundaries,
   presentations and the last 128 presentation intervals, snapshotted on the
   UI task with the entries the interrupt could have overwritten dropped.
   The responsiveness and throughput reports add `animation.presented_interval_ms`,
   `animation.repeated_frames`, `animation.repeated_percent`, `animation.vsync_per_s`
   and `animation.presented_per_s`, plus `vsync_latency` (boundary to refresh
   start). These are new diagnostics; the 60 fps policy and its reference are
   unchanged in this candidate.
3. **128-byte bursts** for the AXI DMA copy and the PPA client. The DMA
   destination and length now follow the 128-byte write burst; the source keeps
   its 4-byte alignment. Unsupported regions still use LVGL's native copy.
4. **Signal strength on its own task.** A low-priority task on core 0 samples RSSI
   every second while `/status` is being polled and otherwise once a minute. Status
   responses and the Home Assistant `Wi-Fi-signal` sensor (now a template sensor with
   the same entity) read the sampled value and its age; `status_radio_us` is the
   duration of the latest sample. Neither the UI task, the HTTP task nor the sensor
   waits for the C6 any more: ESPHome's `wifi_signal` sensor had run the ~30 ms RPC
   on the UI task once a minute, which is the 30 ms "Looptid" maximum Home Assistant
   had recorded every minute and the single >50 ms interval both earlier runs caught
   at uptime 127 s.

Free checks: `tests/test_vsync_clock.py` (counts, stale detection, concurrent
snapshot never reports an overwritten entry), the extended native LVGL check
(`tools/check_orb_prefetch.py`: one image per boundary, wrap, one-shot hold,
frozen scene, restart, stale fallback, 4869 exact pixel comparisons),
`tests/test_dma_copy.py` (burst alignment) and `tests/test_panel_responsiveness.py`
(presentation parsing and repeated-frame accounting).

## Measured result

Four complete physical CI runs installed the candidates in sequence; every one passed
all 26 gates with exact golden pixels and zero PPA/DMA/decode errors. Runs 1 and 2
measured the frame-clock pacing, run 3 added the signal-strength task and run 4 the
glitch-free re-arming of the bounded fixture for long measurements. Production orb,
10 s, p50 / p95 / p99 / max in ms unless stated:

| Measurement | Before (`f66ef313`, 05:51) | Run 1 `1a514681` (06:59) | Run 2 `534cea62` (07:12) | Run 3 `c4e55723` (07:27) | Run 4 `45aad07e` (07:43) |
| --- | --- | --- | --- | --- | --- |
| Presented interval (display) | not measured | 16.563 / 16.567 / 16.571 / 16.587 | 16.563 / 16.568 / 16.572 / 16.577 | 16.562 / 16.567 / 16.571 / 16.608 | 16.562 / 16.566 / 16.569 / 16.572 |
| Repeated frames | not measured | 0 of 601 | 0 of 597 | 0 of 603 | 0 of 608 |
| Completion interval | 16.53 / 18.72 / 20.34 / 26.08 | 16.31 / 18.46 / 18.90 / 19.56 | 16.47 / 18.89 / 19.70 / 20.47 | 16.35 / 18.80 / 19.66 / 20.40 | 16.44 / 18.86 / 19.58 / 20.12 |
| Intervals over 25 ms | 0.67% | 0% | 0% | 0% | 0% |
| Refresh work | 13.80 / 16.49 / 18.25 / 23.40 | 15.17 / 17.60 / 18.06 / 18.73 | 11.37 / 12.41 / 12.55 / 12.66 | 11.26 / 12.31 / 12.47 / 12.60 | 11.31 / 12.30 / 12.56 / 12.77 |
| Render work | 12.33 / 13.26 / 13.42 / 13.63 | 11.01 / 12.05 / 12.17 / 12.31 | 11.10 / 12.10 / 12.28 / 12.36 | 10.97 / 11.99 / 12.16 / 12.28 | 11.03 / 12.00 / 12.23 / 12.43 |
| Flush wait | 1.35 / 3.25 / 4.97 / 13.56 | 4.27 / 5.75 / 6.14 / 6.75 | 0.01 / 0.02 / 0.02 / 0.03 | 0.01 / 0.02 / 0.02 / 0.03 | 0.01 / 0.02 / 0.02 / 0.03 |
| Boundary to refresh start | not measured | 12.15 / 13.28 / 13.49 / 13.73 | 0.34 / 1.52 / 2.21 / 2.36 | 0.37 / 1.53 / 2.12 / 2.37 | 0.35 / 1.60 / 2.25 / 2.62 |
| Status HTTP | 20.8 / 44.5 / 59.6 / 59.6 | 26.4 / 38.5 / 46.6 / 46.6 | 17.4 / 27.8 / 44.2 / 44.2 | 18.4 / 89.3 / 658.7 / 658.7 | 15.0 / 47.5 / 65.6 / 65.6 |

Run 1 showed why pacing alone was not enough: with the image chosen at the boundary
but the previous buffer still waiting for that boundary to be presented, every refresh
started 12 ms late and blocked 4 ms in the flush wait, leaving 1.4 ms of slack in a
16.56 ms frame. Run 2 adds the rule that a boundary whose buffer is still pending is
skipped; refreshes then start 0.3 ms after the boundary with no wait and 4 ms of slack,
and the image chosen at a boundary is presented at the next one. The run 3 status HTTP
maxima of 658.7 and 582.6 ms were two responses whose UI queue, snapshot, formatting and
socket enqueue each took under 10 ms while their headers or bodies took 310–530 ms on the
network; they are retained and are not attributed to the panel's tasks.

Completion intervals still alternate by about ±1.3 ms because consecutive renders differ
by 1.0 ms at the median and 2.0 ms at p95 (content and buffer dependent); their p95 cannot
reach 16.67 ms while render time varies at all. The display, measured at its own boundaries,
presented a new image at every one of 2409 boundaries across the four runs.

The fixed `tabs-20-orb-10s-v2` workload on the same runs (before -> run 4): release to
software redraw p50 / p95 78.74 / 85.66 -> 63.5 / 65.9 ms; the full page render fell from
57–71 to 47–53 ms as the PPA moved its two 1.2 MB copies per tab from about 24 ms to 13–15 ms
each (75–81 MB/s versus 40–50 MB/s). Press stayed at 17.8 / 18.4 ms. Status HTTP p50 / p95
fell from 39.7 / 107.4 to 17–25 / 71–74 ms; its remaining p95 is a request queued behind a
page render. The 25 fps orb in run 2 kept the single 56.4 ms interval at uptime 127 s that
run 1 had seen at 60.7 ms (both at the 60 s poller tick); runs 3 and 4, with the sampling
task, measured no interval over 46 ms. Minimum free PSRAM was 15,850,772 bytes and minimum
free internal memory 197,816–205,652 bytes across the runs.

### Five-minute soak

`tools/panel throughput --seconds 300` on run 4's firmware kept the production orb running
for 299.9 s by re-arming the bounded fixture every 20 s (18,107 frame boundaries):

- 18,105 presentations: **2 repeated frames**, both from one 49.7 ms presentation at
  uptime 307.5 s. The other five minutes had no presented interval over 16.61 ms.
- Boundary to refresh start p50 / p95 / p99 / max 0.36 / 1.61 / 2.30 / 9.25 ms; the
  9.25 ms main-loop delay is the single hiccup, source not identified.
- Render work p95 / max 11.98 / 12.52 ms, refresh work max 12.88 ms, completion
  interval p95 / p99 / max 18.89 / 19.79 / 40.65 ms, 18,117 prefetch hits, zero fallbacks.
- Status HTTP p50 / p95 / max 18.3 / 40.5 / 236.3 ms over 1,327 polls; free PSRAM and
  internal memory unchanged from start to end.

The old once-a-minute stall would have produced five late presentations in this window.
Raw evidence: the private soak report under this session's scratchpad, retained by its
retention rules; the four CI runs are `.panel-ci/run-20261025-065945-764627`,
`run-20261025-071231-050797`, `run-20261025-072728-962958` and `run-20261025-074351-704065`.

## Guards against regression

- `tests/test_main_task_rpc.py` fails if the C6 signal-strength RPC appears anywhere but
  the sampling task, if the board config uses the `wifi_signal` platform again, or if the
  frame clock is no longer attached.
- The 60 fps policy now requires at least 400 presentation intervals, at most 0.5%
  repeated frames and a presented-interval p95 of at most 17 ms, and compares boundary-to-
  refresh latency against the reference (`.panel-ci/baselines/throughput-vsync-20261025.json`,
  recorded from a fresh 10 s measurement under the new policy; the first reference is kept as
  `throughput-first-60fps-20261024.json`). Run 3's fixed workload is the new responsiveness
  reference (`responsiveness-vsync-20261025.json`); the previous one stays in place.
- CI and the checked flash path run a 120 s production soak after the gate: at most one
  late presentation, three repeated frames, 4 ms boundary-to-refresh p99 and zero prefetch
  fallbacks. A stall recurring every 60 s appears at least twice in 120 s. One failed soak
  is measured once more; a second failure stops the run with both reports retained.
