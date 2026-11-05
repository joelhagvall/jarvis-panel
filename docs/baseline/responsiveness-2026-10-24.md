# Responsiveness investigation, 2026-10-24

The first instrumented physical run found uneven animation pacing that the old mean-FPS
gate did not catch. It failed the new unchanged 50 ms p95 / 5% late-frame limits.
The measurement and failed release report are preserved; they are not an accepted baseline.
No paid model calls were made.

## Physical evidence (16:55–16:56, Europe/Stockholm)

Installed instrumentation build `26e4accf24e460563270c5fa4b3ab605`, ESPHome 2026.9.1,
ESP-IDF 5.5.5, same compressed atlas, pixels and double-buffer display path.
Private evidence: `.panel-ci/run-20261024-164943-833840/report.json` and
`responsiveness/responsiveness.json` in that run. All earlier boot, crash-log,
physical, synthetic, visual and legacy performance gates passed. The new gate failed.
The original normal page was restored successfully.

| Measurement | n | p50 | p95 | Maximum |
| --- | ---: | ---: | ---: | ---: |
| Injected press -> nonempty refresh | 20 | 20.65 ms | 21.44 ms | 21.77 ms |
| Injected release -> page redraw | 20 | 80.59 ms | 83.59 ms | 83.78 ms |
| Steady real-background refresh work | 249 | 17.13 ms | 17.77 ms | 18.21 ms |
| Steady nonempty refresh interval | 248 | 34.30 ms | 63.61 ms | 81.35 ms |
| Input HTTP roundtrip, including 60 ms hold | 20 | 91.48 ms | 101.40 ms | 102.71 ms |
| Status HTTP roundtrip during the suite | 39 | 246.01 ms | 735.53 ms | 765.99 ms |

Animation averaged 24.38 redraws/s, while **35.08%** of intervals exceeded 50 ms.
Rounded to 5 ms, the interval distribution had 160 samples near 35 ms, 73 near 50 ms,
9 near 65 ms and 6 near 80 ms. This shows why average FPS alone is insufficient.
These are LVGL software timings, not optical presentation measurements. HTTP status timing
includes animation contention, device queueing, serialization and transport; it is not
pure Wi-Fi latency. Physical GT911 scan/I2C and LCD scanout are outside injected-input timing.

## Diagnosis and checked candidate

The installed ESPHome core defaults to a 16 ms component-loop interval. Its LVGL component
calls `lv_timer_handler()` in that loop. LVGL display and animation timers also default to
16 ms. Around 17 ms of measured refresh work, plus loop scheduling and other component work,
can quantize a 40 ms animation into short and long intervals. This is the leading diagnosis;
we have not proven that it explains every physical outlier or the HTTP status tail.

The candidate `bench/frame_pacing.h` lowers the component-loop, display-refresh and animation
timer periods to 5 ms. It leaves the animation's own 40 ms frame cadence, GT911's 10 ms sample
interval, clock rates, image assets, double buffering and audio configuration intact.
It does not busy-spin. More frequent component servicing can increase idle CPU/power use;
that tradeoff and any network/audio scheduling effects still need physical verification.

`tools/check_frame_pacing.py` runs the real native LVGL timer implementation with an explicitly
synthetic 17 ms rendering cost and 2 ms other-loop cost. The 2 ms is a controlled assumption,
not a measured decomposition of the physical loop. With those identical costs:

| Native timer model | p95 interval | Maximum | Intervals >50 ms |
| --- | ---: | ---: | ---: |
| 16 ms scheduling | 53 ms | 53 ms | 30.36% |
| 5 ms candidate | 45 ms | 45 ms | 0% |

The model supports testing the candidate. It does not establish hardware improvement.
Its source hashes and result are in `.panel-ci/frame-pacing-native.json`.
This optional diagnostic needs the native simulator library. The mandatory suite remains self-contained; physical CI gates the real timings.

## Installation boundary

The failed new responsiveness gate created `.panel-ci/quarantine.json`. It did not report
a crash, bootloop or failed restoration. The quarantine nevertheless blocks ordinary
installation and further physical benchmarking. It must not be deleted to continue.

Prepare a frozen candidate with `tools/panel ci --local-only`, then use the repository's
bounded recovery plan. The plan binds the checked source, firmware/ELF hashes, this diagnosis
and the active quarantine. Existing recovery policy requires Joel's explicit approval of
that concrete plan before installation. Both permitted runs must pass every normal gate,
including the same responsiveness limits and the existing reviewed visual reference
`.panel-visual/reference-v2-20261024-2`. No baseline or acceptance threshold is relaxed.
After a passing physical result, record the first responsiveness reference explicitly.


## First approved 5 ms installation: host subscription race

The approved frozen candidate `566fbe7e95d3de1c7d3d276b0dac44d8` was installed at about
17:08. Boot identity verification passed at 65.9 seconds, reset reason 3, healthy assets,
new boot ID `44243655`, and matching ELF prefix `79a1ef4a0`. The boot log contained no
crash signature, but its API client was not subscribed before OTA finished. It missed
`Update complete` / `Rebooting safely`, so the required boot-log gate correctly failed.
No physical pacing result was collected. The plan stopped after its first attempt;
its second installation was not attempted. It cannot be resumed as a successful run.

Root cause: `runtime_logs()` started a subprocess and immediately yielded to OTA without
proving that DNS, authentication and log subscription had completed. The host correction
now waits up to 20 seconds for a successful encrypted handshake followed by an actual
native log record **before** yielding to protected work. It uses `pread`, leaving the
child writer's shared file offset unchanged. Exit/timeout stops before an upload starts.
The ordinary required OTA-boundary check remains unchanged.

A free regression proves that a connected-only log is insufficient, that an authenticated
native record passes, that an exited process fails, and that the shared offset is unchanged.
A passive physical log probe at 17:12 proved the new handshake-plus-native-record check
against the installed panel without touching the UI or restarting it. The resulting log
had no crash signature (`.panel-ci/frame-pacing-log-probe/runtime.log`).

The replacement bounded plan must bind this host correction and retain the original
failed plan/attempt and quarantine. It still requires all physical gates and explicit
approval. The 5 ms firmware is installed, but its physical performance remains unverified.


## Measured 5 ms result and observer effect

Approved plan v2 installed build `b1ca884d5e2200ab2fcb15646237bc22` and passed the unchanged
boot-boundary, physical, synthetic, visual and legacy performance gates. The corrected
log subscription worked. Evidence: `.panel-ci/run-20261024-172142-430076/report.json`.

The version-1 responsiveness workload was complete and restored the original page:

| Metric | 16 ms initial | 5 ms candidate |
| --- | ---: | ---: |
| Press median / p95 / max | 20.65 / 21.44 / 21.77 ms | 9.72 / 18.00 / 18.00 ms |
| Release median / p95 / max | 80.59 / 83.59 / 83.78 ms | 78.39 / 82.60 / 82.68 ms |
| Refresh interval median / p95 / p99 / max | 34.30 / 63.61 / 81.29 / 81.35 ms | 37.69 / 58.83 / 73.59 / 74.63 ms |
| Intervals >50 ms | 35.08% | 6.64% |
| Average redraws/s | 24.38 | 25.08 |
| Refresh work p95 | 17.77 ms | 21.18 ms |
| Input HTTP roundtrip p95 | 101.40 ms | 124.31 ms |
| Status HTTP roundtrip p95 | 735.53 ms | 719.59 ms |

This is an 81% reduction in the observed late-frame fraction, but p95 58.83 ms and
6.64% late frames still fail the unchanged 50 ms / 5% ceilings. The run was not accepted
or promoted to a baseline. The plan stopped after one attempt; no second attempt ran.

Offline analysis of consecutive immutable telemetry records found that every remaining
late interval was the first new interval following a `/status` snapshot. In contrast,
the initial 16 ms run had repeated late intervals throughout each sample window.
Source inspection found that status JSON, including floating-point formatting and the
sample arrays, was constructed synchronously on the LVGL task. The working hypothesis
is a significant observer-induced pause. This is not an excuse to omit those frames.

The next candidate retains the same 5 ms timers. It snapshots mutable device state and
copies the fixed sample rings on the ESPHome task, then formats only immutable captures
on the existing HTTP task, already pinned to core 0. ESPHome's loop task runs on core 1.
No worker reads live LVGL objects or the changing recorder. A native ASan/UBSan regression
formats a frozen copy concurrently while the owner keeps recording, checking that output
stays identical. Firmware compilation validates the complete callback/worker integration.

Protocol `tabs-20-orb-10s-v2` explicitly identifies this measurement-profile change.
The old failed reports remain diagnostic evidence, not an equivalent v2 reference.
Two new required metrics expose the observer cost: `status_main_us` (snapshot on the
panel task) and `status_worker_us` (work on the HTTP task), `status_radio_us` (C6 query), and `status_main_heap_us` (heap capture). The UI-task snapshot
must stay below 5 ms p95 / 10 ms max. Every original responsiveness ceiling is retained.
The candidate still requires physical verification; no v2 baseline has been invented.


## Observer timing exposed the synchronous radio query

The first deferred-formatting build `05089b8998188def031df11be018d029` passed boot,
physical navigation, synthetic modes, visual comparison and idle metrics. Its raw animation
report measured `status_main_us` at 29.25–29.43 ms while `status_encode_us` was only
0.88–0.91 ms. JSON formatting was not the dominant remaining pause; the initial attribution
to formatting alone was incomplete. These measurements are preserved in
`.panel-ci/run-20261024-174050-932004/animation/metrics.json`.

ESPHome's `WiFiComponent::wifi_rssi()` calls `esp_wifi_sta_get_ap_info()`. On this P4/C6
board ESP Wi-Fi Remote forwards that call through ESP-Hosted RPC to the C6. It must not
run synchronously on the LVGL task. The corrected formatter fetches signal strength
through the IDF getter on the existing HTTP task. It captures only the connection-state
boolean on the owner task. The new radio-duration metric will verify the contribution
rather than assuming it from the source. Heap capture also has its own duration metric.

That same run exposed another old-gate problem: animation compared a 98.15 ms touch
percentile left over from earlier smoke/settings actions against an old 21.61 ms window.
It did not sample a new touch during animation, and the value reset to zero midway through
the run. Its actual animation mean was 13.88 ms at 24.77 redraws/s. The stale value cannot
be attributed to this workload. No baseline was replaced. Interval evaluation now explicitly
lists historical touch and worst-render windows as `not_compared`; their raw values remain
archived. The full release still requires the new responsiveness gate's fresh 20 press and
release observations, actual per-event maxima, absolute budgets and measured comparisons.
A regression proves that stale history is retained and separated, and another proves that
fresh touch/observer-budget violations still fail even without a baseline.


## Accepted physical result, 18:01–18:07

The final firmware is build `8cad6879fd3ed8c47ca936495a8fef87`, OTA SHA-256
`585d1b00d22208e111ca3b37bbebf2bf59af96ad2fc4bb7c37f73cfea10ea4da`.
The same frozen binary passed two complete physical CI runs:
`.panel-ci/run-20261024-175708-288354` and `.panel-ci/run-20261024-180322-127766`.
Both proved a distinct normal software boot, clean crash logs, physical navigation,
synthetic modes, all fourteen visual scenes, legacy interval metrics and the fresh
responsiveness gate. The firmware candidate passed 397 free tests and configuration
validation. No paid model calls were used.

| Measurement | First run p50 / p95 / p99 / max | Second run p50 / p95 / p99 / max |
| --- | --- | --- |
| Press -> redraw, n=20 each | 10.11 / 17.98 / 17.98 / 17.98 ms | 17.51 / 17.95 / 17.99 / 17.99 ms |
| Release -> page redraw, n=20 each | 77.11 / 80.00 / 80.09 / 80.09 ms | 77.76 / 80.53 / 81.12 / 81.12 ms |
| Animation interval, n=255 each | 38.67 / 44.37 / 44.71 / 45.04 ms | 42.42 / 43.83 / 44.52 / 44.92 ms |
| Animation refresh work, n=256 each | 17.26 / 18.12 / 27.27 / 28.30 ms | 16.75 / 17.49 / 18.36 / 20.08 ms |
| UI-task status capture | 0.215 / 0.228 / 0.254 / 0.254 ms | 0.211 / 0.275 / 0.338 / 0.338 ms |
| Worker radio query | 30.35 / 30.83 / 30.84 / 30.84 ms | 30.37 / 30.68 / 40.06 / 40.06 ms |
| Input HTTP roundtrip, n=20 each | 89.75 / 125.47 / 798.33 / 798.33 ms | 86.08 / 93.94 / 100.73 / 100.73 ms |
| Status HTTP roundtrip | 238.50 / 726.17 / 931.63 / 931.63 ms | 213.04 / 711.46 / 727.77 / 727.77 ms |

Both animation workloads averaged 25.00 redraws/s. None of the 510 measured intervals
exceeded 50 ms. This confirms that moving the approximately 30 ms radio query off the UI
task removed the repeatable status-poll pause. Heap capture was approximately 0.10 ms,
and the remaining formatting work was roughly 1–3 ms on the worker. The 5 ms timer change
and the observer fix are separate improvements; v1 and v2 are not interchangeable baselines.
The original 35.08% late-frame diagnostic used the older scheduling and observer path.

The first passing v2 report was explicitly recorded as
`.panel-ci/baselines/responsiveness.json`. The second passed every relative comparison
against it without changing any tolerance. `recovery finish` resolved the quarantine and
preserved its original failure and every attempt. The default visual reference now selects
the existing reviewed `reference-v2-20261024-2`; the original v1 directory is preserved at
`.panel-visual/reference-v1-preserved-20261024`. No golden pixels were regenerated.

Final host hardening requires a complete compatible responsiveness reference before
ordinary CI or checked flash uploads. Explicit first-reference creation remains separate.
Free regressions cover missing/incompatible references and prove that no upload occurs.

The host transport still has occasional long response times; these were retained, not
subtracted from local device timing. This work does not prove RF repair or 60 fps rendering.
The software boundary still excludes GT911 scan/I2C and optical LCD scanout. Two short
animation runs establish this release result, not multi-hour worst-case latency or acoustic
quality. Rendering remains about 17 ms per animation frame; 60 fps would require a 16.67 ms
whole-frame budget and a separately measured rendering/asset optimization.


The final host-only baseline preflight and documentation changes passed all 400 free tests
and ESPHome configuration validation. They do not change the installed firmware runtime.
Missing or incompatible responsiveness references now stop before device interaction/upload.
