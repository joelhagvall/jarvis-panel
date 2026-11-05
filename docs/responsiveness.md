# Responsiveness is a release gate

The later 2026-11-02 input-priority and precise weather-drawing results (two final runs, all tails,
host/observer costs and the intermediate candidate) are in [the follow-up measured report](baseline/input-weather-2026-11-02.md).

The accepted 2026-11-02 detail-page results, all timing quantiles, host/observer costs and retained failed
runs are in [the measured report](baseline/detail-pages-2026-11-02.md).

`tools/panel detail-load --out .panel-ci/NEW-DIRECTORY` additionally tests the weather, calendar and AI detail
pages. Each case runs six seconds of synthetic updates every 600 ms through the ordinary production renderers,
then 20 real return/entry taps while those updates and background cover preparation continue. The snapshots,
all raw timing samples, UI trees and separate host/observer costs are private. The fixture never writes HA,
calendar or AI usage records, never calls a model and never plays audio. It restores the latest actual sensor
values, so a real changed value preempts the test; renewing a mode extends only its deadline.

This is a static-page workload, not a 60 fps animation benchmark. Its new diagnostic update budget is p95
40 ms / maximum 100 ms, at least eight fresh redraws and eight actual data steps; real navigation retains
the existing absolute press/release limits. Every sample requires active foreground and background loads.
Text clipped by an ancestor, missing new covers or missing fresh input samples fails. Do not compare this
different workload with the idle/orb baseline; those mandatory release gates remain unchanged.

The initial cold weather-card probe (`.panel-ci/detail-card-before.json`, 2026-11-02) measured press→redraw
150.263 ms and release→redraw 90.279 ms: the card had no press feedback. Entry cards now have an immediate
heading-only hint on the weather/calendar cards; the AI heading keeps its smaller overlay. Painting the whole
230×230 entry card cost about 8 ms in the loaded input traces, so weather/calendar now dim only their heading
and restore its opacity on release or `LV_EVENT_PRESS_LOST`. Each full-screen return target also highlights
its heading while pressed. The six-second load measures new data, including a full calendar within
the server's ordinary right-column row budget. Canonical fixture code: `tools/panel_detail_codegen.py`;
free regressions: `tests/test_panel_detail_load.py`.

The first detail-load run (`.panel-ci/detail-pages-first-20261102/`) failed with weather/calendar/AI update
p95 of 66.073/51.690/40.595 ms. Weather and AI exhausted the dirty-area list and repainted the whole screen;
calendar hid and showed every day on every update. `ui_update.h` now avoids unchanged text/style/visibility
writes without splitting an update over frames. Calendar hides only removed rows/days. The weather curve uses
local coordinates in a tight object. Predeclaring only its graph region was insufficient: the preserved
`.panel-ci/detail-pages-optimized-20261102/` still failed at weather p95 56.637 ms, while calendar/AI passed.
Centred forecast temperature rows invalidate their old/new layout boxes too; the graph and forecast now each
predeclare bounded regions before their mutations. Consolidation alone removed the full redraw but the
preserved `.panel-ci/detail-pages-final-20261102/` still failed at weather p95 51.107 ms: growing bars temporarily
invalidated beyond their final baseline and layout spent about 19 ms walking invalidations.
`ui_update::Batch` settles pre-existing layout damage normally, records the old/final visible bounds of changed
objects (the whole flex row when siblings move), then performs that synchronous subtree update/layout with intermediate invalidation disabled. It retains
earlier unrelated dirty areas and restores invalidation before returning; there is no refresh or event-loop
yield in the scope. It reuses `screen_swap::Areas` to merge damage down to the existing region budget.
Bounded scratch lives in PSRAM; allocation/capacity failure uses the original complete weather regions.
Identical curve points and unchanged fields do not invalidate anything. Use it only for the bounded weather
subtree, which has no blur/overflow effects.
`tests/test_static_ui_updates.py` uses actual native LVGL to compare the curve's pixels, require zero redraws
for unchanged values and verify shorter/longer calendar updates. Its weather control reproduces the full-screen
fallback without consolidation and compares every partial frame with a full redraw and the unbatched renderer,
including empty/short/full forecasts, dry/wet bars, changing text widths and unrelated pending layout damage.
It recompiles LVGL's refresh without the simulator's legacy byte swap, matching firmware RGB565.
Keep failed runs when comparing the candidate.

`board_config/ui_updates.py` routes actual and fixture detail data through the same eight-slot latest-value
queue (`ui_priority.h`). Pending fields drain together after an input pass, once pointer hold/release feedback
has drawn and the short release guard has elapsed. A tap that draws nothing has a bounded escape; real and
remote pointers use the same LVGL events. Only noncritical detail data is queued; alarms, voice and input stay
on their normal paths. Pending replacements retain the newest value. Restoration waits for actual data to drain.
`tests/test_ui_priority.py` checks held/released/blank taps, wraparound and reentrant submissions;
`test_static_ui_updates.py` also drives real native LVGL pointer events and verifies that the destination page
draws before data applies, bounds the pressed pixels to the heading and checks cancellation/release recovery.
`/status` snapshots queue counts on the UI task and formats them on the worker
(`ui_update_pending/applied/replaced/deferred/max_wait_ms`). A bounded `ui_work` health line is logged at most
once a minute when data has arrived (`tools/panel logs`), without sensor payloads.

Joel's standing requirement: measure responsiveness rigorously on every firmware release,
preserve raw evidence, compare equivalent workloads, and improve the tail as well as the mean.
The target is immediate feedback and consistent motion. We have no measured Tesla reference;
"Tesla level" is a design goal, not a benchmark result.

```sh
tools/panel responsiveness --out /tmp/hallpanel-response-NEW
# Offline comparison, no panel interaction:
tools/panel responsiveness --check /tmp/hallpanel-response-NEW/responsiveness.json
# First reference, or a separately reviewed new reference; never overwrites:
tools/panel responsiveness --check /tmp/hallpanel-response-NEW/responsiveness.json \
  --record-baseline .panel-ci/baselines/responsiveness.json
```

`tools/flash_panel.sh` and `tools/panel ci` always run this gate. Normal installation now requires the measured reference to exist and match the protocol
and policy before OTA. Both paths always compare relative regressions as well as absolute
ceilings. Only explicit `--record-baseline` creation may operate without an existing reference;
it must pass every absolute gate. A first-reference creation is not a relative comparison. Never promote a
failed run, relax limits to hide a regression, or overwrite the accepted reference.
Run before and after performance changes. A changed workload or policy needs separate review
and a new reference. The first instrumentation installation cannot have old-device measurements
of metrics the previous firmware did not expose.

## Fixed workload and boundaries

Protocol `tabs-20-orb-10s-v2` performs 20 alternating real taps between the dashboard and Hemmet,
then 10 seconds of the listening orb over the real current painting. Each tap holds for exactly
60 ms. It obtains a fresh visible widget tree, verifies each resulting page and requires a new
release-to-redraw sample. It does not change settings, launch voice, play sound or call models.
It acquires the existing exclusive device lease and restores the original normal tab.
Real touch, voice, alarms, announcements, boot/firmware changes and Wi-Fi disconnection stop
the run. A disconnect never causes an input retry. Synthetic mode expires within 30 seconds.
Before/after test state is private; the timed workload takes no screenshots.

Legacy `render_max_ms` and `touch_refresh_p95_ms` still remain in raw interval reports,
but their previous 60-second history is explicitly listed as `not_compared` for idle
and animation. Fresh per-event maxima and touch percentiles are mandatory in the
responsiveness gate. No legacy baseline is overwritten.

Firmware keeps six independent 128-sample rings with monotonic totals, approximately 3 KiB
of raw timing storage. The main task records samples without allocating or printing per-frame
logs. `/status` copies immutable counters on the panel task and serializes them on the HTTP task (core 0). The host subtracts sequence totals, rejects overwritten
samples and counter resets, and archives only new values. It never reuses an earlier percentile
as a current measurement. Physical input and injected remote input have separate series.

| Metric | What it measures |
| --- | --- |
| `remote_press_ms` | Injected press -> next refresh with actual drawing |
| `remote_release_ms` | Injected release -> next refresh with actual drawing; page change verified separately |
| `remote_release_to_<page>_ms` | The same, split by target page (Hemmet or Gå hemifrån); informational, not gated |
| `physical_press_ms`, `physical_release_ms` | GT911 callback -> next refresh with actual drawing, when physical input occurs |
| `refresh_work_ms` | `REFR_START` -> `REFR_READY` for a nonempty refresh, including layout/synchronization and flush submission |
| `refresh_interval_ms` | Time between nonempty refresh completions; first cross-workload interval excluded |
| `empty_refresh_work_ms` | `REFR_START` -> `REFR_READY` for a refresh that drew nothing. Diagnostic only, never gated. Double-buffered direct mode still syncs the previous frame into the other buffer there: about one full-screen copy after each page change, invisible in every other series |
| `slowest` | The slowest render of the last 2 s: `render_us`, `at_us`, and the areas it redrew (`rects`, up to 8, `pixels`). Diagnostic only, never gated; shows what a repeated frame drew |
| `animation.fps` | Reciprocal of the mean observed refresh interval |
| `animation.jank_percent` | Percentage of animation intervals longer than 50 ms |
| `command_roundtrip_ms` | Host HTTP input request -> response, including the intentional 60 ms press |
| `observed_ms` | Host request -> status confirming the new page and a new release/redraw sample |
| `status_roundtrip_ms` | Separate host request latency during measurement |

Seen on 2026-10-28 with unchanged firmware: `animation.refresh_interval_ms.max` above 33.4 ms in 4 of 14 throughput
runs (34.6, 54.1, 148.7, 46.1 ms; the rest 20-22 ms), and `status_roundtrip_ms` failing while `tools/panel host-link`
showed a p95 of 36 ms against a 3 ms median. One such outlier after a change that does not touch the measured page is
Wi-Fi or host noise: compare with the earlier runs (`tools/panel throughput-history`) before
suspecting the firmware. Do not rerun until it passes; stop and report after a second noise failure.

The soak also fails on Spotify's song changes while music plays (2026-10-28: late presentations, repeated frames
and orb prefetch fallbacks in both 120 s windows, with songs starting at 21:41:31 and 21:45:01): a new cover's
download blocks the loop in 140-180 ms steps (docs/now-playing.md). Check the songs' start times in
`journalctl --user-unit hallpanel-soloist` on Jarvis; if one fell in a window, run `tools/panel gates --from soak`
right after the next song has started. A 409 from a panel tool means another client holds the device lease (five
minutes after a client that did not release it): wait it out, never retry a tap in a loop.

Every timing distribution includes count, minimum, median (p50), p95, p99 and maximum.
Percentiles use nearest rank. With 20 navigation samples, p99 is the maximum; it does not
establish population-level 99th-percentile accuracy. Use repeated fixed runs to investigate
tail variation. The animation requires at least 200 fresh work/interval samples.

The normal orb is designed for 25 fps, a 40 ms budget. Initial release ceilings are p95
50 ms / worst 100 ms cadence, at most 5% of intervals over 50 ms, at least 22 redraws/s,
and p95 30 ms / worst 100 ms refresh work. Input budgets are p95 100 ms for press and
120 ms for release, with a 250 ms maximum. These are starting acceptance limits, not claims
that the panel achieves 60 fps. A 60 fps target would require 16.67 ms frame intervals and
appropriately authored animation frames. Relative gates in
`tests/performance/responsiveness.json` catch smaller deterioration below these ceilings.
Host command p95 also has a 750 ms ceiling. Network and panel numbers remain separate.

Version 2 separates the status snapshot from JSON formatting. `status_main_us` measures
snapshot work on the UI task (p95 limit 5 ms, maximum 10 ms); `status_worker_us` measures
total HTTP-task work, including `status_radio_us` for the Wi-Fi coprocessor RPC.
`status_main_heap_us` isolates memory-stat capture work. These observer-cost metrics are required fresh measurements. This change addresses an
observer effect found in version 1: every remaining late frame in the 5 ms run followed a
status poll. Version 1 results remain diagnostic evidence; they cannot be an accepted
version 2 baseline. No original timing ceiling was relaxed.

## Host-latency failures: check the Mac's link first

`status_roundtrip_ms` and `command_roundtrip_ms` include the Mac's own network path. When
either fails, the report carries a `hint`. Run `tools/panel host-link` (read-only, about 9 s) before
rerunning or blaming the panel. It reports whether the Mac reaches the panel over Wi-Fi or a wired
port, the Mac's Wi-Fi band and channel, the panel's RSSI and 20 pings to the panel. A simultaneous router
ping shows which half of the path is slow. It exits 2 with a concrete action when the link would
distort the gate. The panel's ESP32-C6 radio is 2.4 GHz only; `/status` reports its RSSI and
`boot_wifi_channel`, so a Mac on the same or an overlapping 2.4 GHz channel is flagged. Run it alone:
its Wi-Fi profiler query disturbs the Mac's radio, and a run beside a throughput gate failed that gate
on status latency (p95 108 ms; 59 ms when rerun alone). It refuses while the device lock is held.
On 2026-10-26 the gate failed with status p95 230–269 ms against the 71 ms baseline. Waiting about an
hour for a "calmer network" did not help. The Mac was on 2.4 GHz channel 6, the panel's channel.
Moving the Mac to 5 GHz took Mac -> panel ping from 90/245 ms to 5.7/13 ms (avg/max). The tool pings
before it reads the Mac's Wi-Fi profile, because `system_profiler SPAirPortDataType` disturbs the radio:
a parallel ping rose from 6–8 ms to 44–73 ms average. Ping thresholds in the tool are diagnostic,
not release gates; the gate's limits and baselines are unchanged.
On 2026-10-27 the gate failed twice on status p95 (300 and 234 ms against 47 ms) with the Mac on
5 GHz at -72 dBm; animation metrics passed. The same `/status` from Jarvis (wired) took p95 11 ms,
which pins the delay on the Mac's link: compare that way before suspecting firmware.

## What the instruments cannot prove

The prepared external-video procedure and uncertainty-aware annotation analyser are in
[optical-latency.md](optical-latency.md), via `tools/panel optical`. No optical result exists without an actual
external recording. A retimed playback frame rate must not be substituted for the declared capture rate.

The remote pointer bypasses GT911 scan/I2C latency. Refresh completion does not establish LCD
presentation or finger-to-photon timing: asynchronous double buffering can still be scanning.
Proving that requires an external synchronized optical measurement/high-speed camera. Framebuffer
screenshots also cannot prove optical flicker or sound quality. No CPU utilization, optical
latency or acoustic values are fabricated from software timing.

The older bench only measured `RENDER_START` -> `RENDER_READY`, which excludes parts of
layout/buffer synchronization. It also completed a touch on `REFR_READY` even when nothing
was drawn, and discarded touches slower than 500 ms. Instrumentation now requires actual
drawing and keeps slow observations. The legacy 60-second touch histogram still resets for
logs; the new monotonic sample rings are independent and never reset when Home Assistant
reads its diagnostic window.

The physical investigation and accepted 5 ms scheduling/observer corrections are documented in
[the measured report](baseline/responsiveness-2026-10-24.md). Its failed gate is preserved.

## Hardware direction

Keep LVGL and the existing asynchronous double framebuffers. Rewriting the firmware in another language (Rust
was asked about on 2026-10-28) is not a lever: the costs are float work on one in-order core and PSRAM
traffic, the same machine code either way, and the wins so far came from doing less work (the wave went
from 29.5 to 14.6 ms by algorithm). The wait people notice is the voice reply (docs/voice-latency.md). Investigate measured tail
outliers before adding animation complexity. ESP32-P4 has a Pixel Processing Accelerator
for filling/blending/rotation/scaling; it is not a desktop-class 3D GPU. LVGL's PPA integration
is experimental, and memory bandwidth/alignment affect the result. ESPHome 2026.9 forces `LV_USE_PPA 0` on the
P4 ("until upstream bugs fixed"), and LVGL 9.5's PPA unit takes only opaque fills without radius or gradient and
translucent, untransformed RGB565/RGB888 images: no text, rounded fills or images with alpha.

Measured 2026-10-30 without the panel: a draw unit that only classifies tasks (`ppa_evaluate`'s own conditions)
in the glass-v2 simulator, alternating Gå hemifrån and Hemmet. **0 of the page change's tasks qualify**, either
direction: 5–18 fills (rounded or translucent), 3 borders, 32–56 labels and 8–30 images that are either opaque
copies, which `pixel_copy.h` already sends to PPA on the board, or carry alpha. Enabling the unit cannot shorten a
page change, so it was not flashed. What is left is software text and alpha-image blending. Probe, config and
log: `.panel-ci/ppa-probe-20261030/`.

Sources: [Espressif PPA](https://docs.espressif.com/projects/esp-idf/en/latest/esp32p4/api-reference/peripherals/ppa.html),
[LVGL 9.4 PPA](https://lvgl.io/docs/open/9.4/details/integration/chip_vendors/espressif/hardware_accelerator_ppa).

## Network and bounded decode experiments

The wall-powered board requests `wifi.power_save_mode: NONE`. ESPHome otherwise defaults
to LIGHT on ESP32, and modem sleep can delay incoming traffic to a DTIM boundary.
This trades radio power for lower receive latency; electrical power is not measured here.
The debug HTTP server sets `TCP_NODELAY` on each accepted socket so small header/body
writes do not wait for Nagle coalescing. Measure the complete request separately from
UI-task and radio-RPC time. Reopening TCP connections alone did not improve the first
24-pair physical probe, whose raw evidence is retained privately.

`packed_prefetch.h` predicts the next orb image and decodes it on core 0. Three fixed
RGB565A8 PSRAM buffers occupy 435,600 bytes in total. Slot ownership pins source pixels
while LVGL draws; the worker cannot overwrite a pinned buffer. A miss uses LVGL's existing
binary decoder immediately. It never waits for the worker or substitutes a different image.
Compressed assets, animation durations and asynchronous display framebuffers stay intact.
Status exposes hits, fallbacks, completed decodes, errors, last decode time and resident bytes.
The responsiveness report calculates counter deltas during the animation workload;
cumulative totals do not establish its hit rate. Missing feature telemetry is explicitly
unsupported on older firmware. Partial/invalid counters, resets, changing buffer residency
or any reported decode error fail the check. Request submission, time to headers and body
receipt now have separate raw samples and p50/p95/p99/max summaries for status and commands.
These extra diagnostics preserve the fixed workload, policy and measured baseline.

`tests/test_orb_prefetch.py` checks parallel publication and reuse with ASan/UBSan.
For optional byte-for-byte verification against actual native LVGL, run:

```sh
.venv/bin/python tools/check_orb_prefetch.py --out /tmp/NEW-prefetch-report.json
```

This diagnostic uses the existing native simulator library, enables its binary LZ4 decoder,
and compares every decoded byte across listening, thinking, speaking, transitions and
unpredictable frame orders. It does not access the panel. Its first run passed 719 exact
image comparisons with zero decode errors. Physical CI must still establish timing,
memory, visual regression and runtime stability before acceptance.

Sources: [ESPHome Wi-Fi power saving](https://esphome.io/components/wifi/),
[Espressif power saving and latency](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32/api-guides/wifi.html#wi-fi-power-saving-mode).


## Accepted physical result

Both complete physical CI runs passed on October 24 after the C6 query moved off the
UI task. They measured 510 animation intervals combined with none over 50 ms, p95
43.83–44.37 ms and maxima below 45.05 ms at 25 redraws/s. UI-task status snapshots
were 0.228–0.275 ms at p95; the radio query itself was approximately 30 ms on the
HTTP task. The first passing v2 report is the explicitly recorded baseline, and the
second run passed all measured comparisons. See the full measured investigation for
sample counts, host latency, prior failures and limitations.

The subsequent [bounded decode and network experiment](baseline/prefetch-network-2026-10-24.md)
passed full physical CI and a repeated fixed workload. It reduced orb refresh-work p95
from 17.55 ms to 12.79–12.88 ms, and status HTTP p95 from 704 ms to 118–125 ms.
The accepted reference now points to that successful full-CI measurement, with the
earlier passing reference preserved. Both new runs remained at 25 redraws/s with no
interval over 50 ms; the report retains input distributions and large-transfer outliers.

## Production 60 fps gate

The listening orb now has a separate original-duration, interpolated-frame protocol
`orb-60-virtual-10s-v1`. It is distinct from the fixed 25 fps responsiveness workload
and the earlier accelerated 64-frame diagnostic. Both physical measurements remain
mandatory; their raw samples and host/observer timings remain separate.

```sh
tools/panel throughput --out /private/tmp/hallpanel-60-NEW
tools/panel throughput --check /private/tmp/hallpanel-60-NEW/throughput.json --require-target
# Explicit first reference only, into a NEW path. Never overwrites.
tools/panel throughput --check /private/tmp/REVIEWED/throughput.json --record-baseline .panel-ci/baselines/throughput-NEW.json
```

The current measured reference is `.panel-ci/baselines/throughput.json`, from the
first full physical CI pass, with the original raw file and SHA retained. Absolute
limits remain 400 fresh intervals, at least 58 actual source changes and redraws/s,
cadence p95 <=20 ms, max <=33.4 ms, <=1% intervals over 25 ms, and zero decoder/copy
errors. Relative checks also guard render/refresh p95, source rate, jank and HTTP
p95. Missing or incompatible references stop before OTA. Recording requires an
explicit passing production report; A/B and accelerated protocols cannot become
this baseline. Normal `flash_panel.sh` requires both references and runs both gates.

Production uses three owned prefetch slots, exact RGB565+A8 intermediate images,
857072 bytes of bounded external working memory plus a 6 KiB internal staging
allocation. Opaque rectangles use PPA when eligible; masked image blending skips
native-equivalent transparent/opaque work. All fractional blending stays native.
See [the measured investigation](baseline/60fps-investigation-2026-10-24.md) for
failed candidates, exact pixel checks, memory alternatives and physical results.

The 60 fps claim covers the measured listening orb. Other screens and every orb
state are not established at 60 fps. Remote press/release timing does not measure
GT911 sensing or physical scanout. A high-speed camera/photodiode and real contact
trigger would be required for finger-to-photon latency. Host network stalls remain
in their own raw distribution; previous socket enqueue time is not delivery time.

Two full installations passed on October 25, build
`2fd1677277e8f8985afba9328b5cfa05`: 59.978/60.088 source changes/s, cadence p95
18.605/18.852 ms, max 26.691/26.158 ms, jank 0.662/0.333%, and zero fallbacks or
copy/decode errors. All 425 local tests and 14 physical visual scenes passed each
time. The recovery is resolved and night mode restored. The measured investigation
retains every distribution, preceding failed candidate and corrected test contract.
The final fixed-workload release p95 is 77.694 ms versus 80.686 ms initially;
press p95 remains around 18 ms. Larger refresh/HTTP maxima remain visible.

## Frame-clock pacing and presentation telemetry

The retained per-frame samples of those runs explained the remaining cadence tail:
the animation stepped on LVGL's 1 ms tick (16 or 17 ms per image) while the panel
scans at 60.385 Hz (16.560 ms), so the two drifted and about every 155 frames a
submission missed its boundary and waited a whole frame. The production orb now
takes its image from the display frame interrupt's boundary count: one new image
per frame, chosen at the boundary, with the main loop woken by that interrupt.
Listen uses 157 images and speak 121 so one image per frame keeps 2600.0 and
2003.8 ms; the other states keep their counts and stay within 0.7% of their
authored durations. The LVGL animation still owns start, repeat and completion,
golden scenes still freeze by deleting it, the 25 fps benchmark timeline is
unchanged, and a stalled clock or the simulator falls back to the animation value.

Since 2026-10-25 listen uses 314 images (5199.9 ms) from `orb_atlas_v4.lz4`. Its
96 listen frames are drawn (`orbs/glass-jarvis/calm_*`), not blended from 24: the
old blends showed two half-bright copies of a filament that had moved up to 30°.
The atlas now decodes 18.5 drawn listen frames per second instead of 24.6.
`orb_atlas_v5.lz4` does the same for speak: 72 drawn frames (`talk_*`), 181 images
(2997.4 ms) instead of 121, so its decode rate stays 24 frames per second. The
responsiveness test modes `animation`/`animation_live` keep their 25 fps workload
(one native image per 40.625 ms) whatever the production loop length.

Since 2026-10-27 production frames come from the director (`orb_director.h`,
enabled by `packed_orb::direct()` after the atlas v6 install). It writes one
recipe per display frame into a 32-image ring three frames ahead, chained
through `next`, so the prefetch worker prepares exactly the coming images while
speeds ease (listen to think over 36 frames, at most 0.048 drawn frames per frame
while speaking). Planned changes enter at a drawn frame both sets share
(calm_00 = swing_00, swing_23 = talk_00 = rest_00), so they never cross-fade;
only interruptions do (12 frames). Listening follows the microphone's loudness
(`PanelMicrophone::level()`). Since atlas v7 the speaking wave is drawn on the panel
(`voice_wave.h` on the empty glass `hollow_00`, said to take about 3 ms on the prefetch worker, a figure never measured on the panel; since atlas v8 on
2026-10-28 the free Jarvis line on a transparent `hollow_00`): each of
its three lines rises with Jarvis's loudness and sinks in his pauses, then sinks flat to
settle. A wave image the worker has not finished is never drawn on the UI task (that took
~20 ms and starved the main loop); the last finished one is shown again (`orb_wave_held`).
During speech the worker runs about one frame behind, so most wave frames are held ones.
The drawn wave reaches 60 fps since 2026-10-28. `tools/panel wave --measure` (20 s of the silent test mode
`speaking_live`, no captures) timed it on the panel:

| Build | Render p50 / p95 | Copy | Bands | Pixels | Wave images/s (display 60.4) |
| --- | --- | --- | --- | --- | --- |
| Line as first shipped (7-column search) | 29.5 / 34.0 ms | | | | 33.4 |
| 3 aimed columns, 11 px reach, exact bands | 20.9 / 24.7 ms | | | | 47.8 |
| + transparent base read skipped | 19.6 / 23.2 ms | 3.1 ms | 0.7 ms | 15.6 ms | 49.0 |
| + column by column, hollow cleared not copied | 14.6 / 17.5 ms | 1.7 ms | 0.7 ms | 12.2 ms | 60.4 |

Mac timings did not predict the panel: the column-wise loop was 19 % faster on the Mac and 22 % on the panel,
but the first step was 28 % on both. Rank variants offline with `.venv/bin/python tools/wave_bench.py
[-DFLAG]` (error against talk_* and Mac time), then run `tools/panel wave --measure` before and after any change to
`voice_wave.h` or the wave's path in `packed_prefetch.h`; it reports render, copy, band and pixel time
(`orb_wave_*_us` in status). It cannot measure the wave following the real microphone.

Before flashing a change to the motion, `tools/orb_director_sim.py REPLY.wav` runs the director on a
real reply's loudness and prints state changes, cross-faded frames and the largest speed/height steps.
The fixed timelines above remain for the simulator, test modes and a stalled clock.

Status now carries `presentation`: frame boundaries, presentations and the last
128 presentation intervals from the interrupt. Reports add
`animation.presented_interval_ms`, `animation.repeated_frames`,
`animation.repeated_percent`, `animation.vsync_per_s`, `animation.presented_per_s`
and the `vsync_latency` phase (boundary to refresh start). A repeated frame is a
boundary without a presentation: that is what the eye sees, independent of when
LVGL finished. Completion intervals keep render jitter and can never be exactly
one frame; they remain gated with their unchanged ceilings. The policy additionally
requires at least 400 presentation intervals, at most 0.5% repeated frames and a
presented-interval p95 of at most 17 ms, and compares boundary-to-refresh latency
against the reference. The reference is `throughput-vsync-20261025.json`, recorded
under that policy; the first 60 fps reference is kept beside it. A 120 s production
soak follows the gate in CI and the checked flash path (`--seconds 120 --require-soak`):
at most one late presentation, three repeated frames, a 4 ms boundary-to-refresh p99
and zero prefetch fallbacks, so a stall recurring every 60 s is seen at least twice.
A failed soak is measured once more before the run stops; soaks never become references.
Four installations presented a new image at all 2409 measured boundaries, and a
300 s soak repeated two frames in one hiccup across 18,107 boundaries.
The AXI DMA and PPA copies use 128-byte bursts, the PSRAM controller's maximum.
Signal strength is sampled by its own low-priority task; the status reply, the UI
task and the Home Assistant sensor only read the sample, so the once-a-minute 30 ms
C6 query no longer stalls the UI task. See [the measured report](baseline/vsync-pacing-2026-10-25.md).

## Page changes and opaque backdrops

`styles/glass-v2/screen_swap.h` skips LVGL's hidden bg fill under opaque full-screen pictures and
redraws only the differing widgets between Gå hemifrån and Hemmet. Prove changes to it with
`tools/check_screen_swap.py --out NEW` (actual LVGL, no panel); it must stay bit-identical to
LVGL's full redraw. See [the measured result](baseline/screen-swap-2026-10-26.md).

Reviewed again on 2026-10-28 (`run-20261028-080442-619590`): a tab change redraws about 346,000 pixels,
render work p95 39 ms, so the release lands on the third display frame (49.7 ms). Reaching the second
(33.1 ms) needs about 35-40 % less render work; the cards' glass is already baked into the backdrop, so
what is left is the backdrop copy and the text. LVGL's PPA draw unit takes none of a page change's tasks
(measured 2026-10-30, see Hardware direction), so it is not that lever.

## Where a page change's time goes (2026-10-30)

Every page change logs its render time per LVGL draw task type: `tools/panel logs | grep page_draw`, medians per page and area budget with
`tools/panel logs | .venv/bin/python tools/page_draw_summary.py`
(`bench/page_draw.h`; a draw unit that never claims a task charges the time until the next task's creation to
the previous one, since LVGL without an OS draws each task right after creating it). Gå hemifrån ↔ Hemmet
through screen_swap (24 areas, ~350 kpx), P4:

| Part | ms |
| --- | --- |
| Before the first task (`widgets`: screen_swap's decision, 4.0 ms now, and LVGL's own preparation) | 10–13 |
| Labels (36–85 tasks, ~360–410 glyph draws) | 14–25 |
| Images (the backdrop in 24 areas through `pixel_copy.h`'s PPA copy, plus icons) | 15–22 |
| Fills / borders | 4–13 / 1–3 |

`styles/glass-v2/glyph_cache.h` keeps each glyph's A8 bitmap after its first 4 bpp unpack (ESPHome's
`Font::get_glyph_bitmap` unpacks pixel by pixel on every draw) and hands LVGL the same bytes: identical pixels,
all flash gates passed, 87 kB of its 384 kB limit after a few page changes. Unpacking is a small share of text:
87 first unpacks took 2.2 ms including allocation, and the same 59-label change to Gå hemifrån drew its labels
in 14.6 ms (3 changes) against 15.3 ms (5) before, about 1 ms. The responsiveness gate's release median went
66.7 → 59.1 ms (`run-20261030-111125-476034` → `run-20261030-112002-125908`) (`tools/panel_responsiveness.py --compare BEFORE_RUN AFTER_RUN` prints both runs' series), but the screen's content changed
between the runs, which moves it by more than that (section below); do not credit the cache with it.
What is left of text is blending and the per-glyph walk (~40 µs per glyph draw).

**The rest of `widgets` was a frame wait** (page_draw's bracket splits it: screen_swap's decision, frame wait,
layout+sync, walk). Each tab change began 2.4 ms after a refresh of just the tapped tab (84×48 or 112×48 px):
LVGL's default theme gives buttons an 80 ms press transition, still running when a 60 ms tap let go, so the tab
redrew in its own frame, and the page change then waited 5.5–8.5 ms in `refr_sync_areas` for that frame to be
shown (double-buffered direct mode cannot draw into the buffer still being scanned). gen.py now detaches the
tabs' transition-only theme styles (`panel_music_ui::instant_button`, as for the music buttons; boot logs
`tabs: N transition-only theme styles detached`): pressed feedback stays and changes at once. After it, the
refresh before a tab change is the press 57–59 ms earlier, frame wait 0.0 ms and `widgets` 4.5–5.4 ms (was
10–13). The gate's release medians did not move (57.0 → 56.1 ms overall, Hemmet 54.2 → 55.7, Gå hemifrån
60.0 → 60.1; `run-20261030-114232-587336` → `run-20261030-115147-030100`) because the dashboard then drew more
text (85 label tasks, 274 kpx, against 76 and 190). A real finger only met the wait when its tap was shorter
than the transition (80 ms plus up to a frame); real tap durations are not measured yet (`tools/panel touches` after Joel's own tab taps would show them).
A `sample` profile of the Mac simulator does not answer where a page change's time goes: SDL's flush
dominates it and LVGL draws there in about 1 % of the time; use the panel's page_draw lines.

**Text as whole-label masks (2026-10-30).** Of 10–15 ms of text per tab change only 4.3–6.0 ms was blending
(page_draw's `mask blends` line, 387–533 calls, 50–70 kpx) and ~1 ms glyph lookups (measured once by timing
`get_glyph_dsc`, then removed); the rest was LVGL's handling of each glyph in each redraw area.
`styles/glass-v2/label_cache.h` is a draw unit that takes plain labels seen in an earlier refresh, builds
their A8 mask once with `lv_draw_label_iterate_characters` and the glyphs' own bitmaps, and blends each in one
step; labels whose glyphs overlap are refused (per-glyph blending would mix twice). Bit-identical to LVGL's
per-glyph drawing in `tools/check_label_cache.py` (6,400 frames, 60,245 cached draws, 372 refused). The
page_draw log adds `label masks: N drawn, N built, N refused`.
On the panel (all flash gates, the exact visual check included): text per tab change 10–15 → 7.9–11.0 ms, every
label from its mask (32 on Hemmet, 56 on Gå hemifrån, none refused); gate release medians 39.9 → 37.5 ms
overall, Hemmet 37.6 → 34.8, Gå hemifrån 42.6 → 39.7 (`run-20261030-144800-756263` →
`run-20261030-151424-711056`). The bookkeeping lives in PSRAM: as std::string/vector it took ~24 kB of internal
RAM and the first install failed the 96 KiB internal floor (97,004 B).

**The backdrop copy (2026-10-30).** page_draw's `backdrop PPA copies` line: 6 copies, 778 kB, ~10 ms per tab change,
the cache write-back before them 0.1 ms, so PPA's SRM engine itself copies ~78 MB/s. LVGL's own CPU copy instead
(an A/B on alternate changes) took the images from 13.5 to 21.8–23 ms. The AXI DMA copies ~133–138 MB/s but only
contiguous memory: `pixel_copy.cpp` now sends whole-row image copies there first (full redraws, e.g. from the
voice page); LVGL's full redraw still lost to 6 areas (Gå hemifrån 32.9 against 30.2 ms, Hemmet 29.4 against
25.2) because it draws every image, and widening the areas to whole rows would come to nearly as many pixels.
Overlapping the copies with drawing is ruled out by IDF's PPA driver, which invalidates the whole destination
row window (it would discard pixels just drawn beside it). The two pages share no identical widgets worth
skipping (the cards sit at the same places, their content differs). Result, gate, same protocol
(`run-20261030-115147-030100` → `run-20261030-160245-768979`): release median 56.1 → 29.5 ms, p95 61.1 → 32.8;
Gå hemifrån 60.1 → 32.0 (p95 33.0), Hemmet 55.7 → 27.9 (p95 28.8): both within two display frames (33.1 ms).

**Fewer areas, inlined colour blends, a cheaper merge (2026-10-30).** The tab bar's mic button changes state at
once too. One experiment build cycled screen_swap's area budget per page change (Gå hemifrån / Hemmet medians, 15
changes each): 24 areas 54.9 / 41.0 ms, 6: 44.1 / –, 3: – / 36.9, 1: 45.6 / –, LVGL's full redraw – / 38.5; with
the colour hooks 6 areas gave 40.2 / 36.1, 3 areas 40.5 / 36.5, full redraw 41.9 / 37.3. Each area walks the
widget tree and starts its own backdrop copy again, so `kBudget` is now 6. `pixel_copy.h` routes LVGL's three
RGB565 colour blends (translucent fills, text and rounded edges through a mask, both) to `color_blend.h`, which
inlines `lv_color_16_16_mix` with the foreground spread once (tests/test_color_blend.py: bit-identical to a
verbatim copy of LVGL's function and loops; a rounding mutant fails it); the dashboard's fills went 8.6 → 6.2 ms
and its labels 14.5 → 13.4 ms at the same budget. The costliest fills were the music row (472×52, radius 22, opa
219, ~3 ms) and the metro rows (444×40, opa 36, ~2.2 ms each); page_draw now logs the six costliest. PPA blending
would round differently from LVGL and fail the exact visual references, so it was not used. screen_swap's merge
first joins every pair whose bounding box adds no pixels, trying only x-neighbours after a sort (`premerge`):
reduce 3.5 → 1.3 ms, the whole decision 4.3 → 2.1 ms, still bit-identical in `tools/check_screen_swap.py`
(26,658 frames). Gate, same protocol, `run-20261030-115147-030100` → `run-20261030-123641-336282`: release median
56.1 → 37.6 ms (Gå hemifrån 60.1 → 40.0, Hemmet 55.7 → 36.6), p95 61.1 → 40.9; navigation render work p95 53.5 →
35.2 ms. The second display frame (33.1 ms) is 3–7 ms away.

## Page changes slowed 2026-10-29 → 30

Release → drawn frame, median per target page (10 changes each), same protocol:
Hemmet 49 ms and Gå hemifrån 56 ms on 2026-10-29 17:47; 57/64 ms after the 09:46 flash; 57/72 ms after the
10:04 flash; 59–64/73–81 ms in three runs at 10:30 on unchanged firmware (one of three failed the baseline gate).
Idle, nothing redraws (`tools/panel redraw`: 0 frames in 3 s) and both cores are 87–95 % idle (`tools/panel tasks`),
so it is the page change's own render work (`render_work_ms` p95 41 → 58 ms). Several firmware changes and live
content (music bar, chat, picture) changed in between.

A/B the same morning (10:50–11:15), two runs each, release → frame median per target page:
`77c5eac` (before the calendar page and the chat's earlier bubbles) Hemmet 57.8/57.9 ms, Gå hemifrån 64.8/65.0 ms;
the current tree right after, 57.7–57.8/56.7 ms and 63.1–65.4 ms. Those changes cost nothing measurable; the
73–81 ms at 10:30 came from what the screen showed then, not the firmware. The ~9 ms on both pages between
2026-10-29 17:47 and `77c5eac` is still unattributed: the Spotify commits between them (5595bbd, 77c5eac) put the
music bar's controls on both pages, but it may as well be the music bar simply showing. Build an older commit with
`sh tools/old_build_worktree.sh COMMIT NEW_DIR`, which also clones in the ESPHome build cache; without it
tools/check.py fails on missing ESPHome/LVGL dependencies before anything is uploaded.

