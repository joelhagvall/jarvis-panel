# Input priority and weather drawing, 2026-11-02

Installed and verified build `01078ada6f783bb84a5e5e2e8a226e43`. All checked-flash gates passed: visual, baseline responsiveness, baseline production throughput, turntable, 120-second soak and crash/rollback scan. The release ran 1,059 free local tests. No Jarvis deployment or paid voice/model call was needed.

Weather/calendar entry feedback now dims only the heading. Noncritical detail data uses a bounded latest-value queue; real and remote pointer feedback/page drawing have priority. Weather tracks old/final bounds of changed objects and flex rows, merges them with the existing damage reducer, and skips identical curve points.

**Measured benefit:** weather press p95 30.768 → 16.482 / 16.267 ms; weather refresh p95 32.830 → 27.605 / 27.658 ms. Calendar press p95 25.830 → 21.453 / 17.434 ms. Weather page-change p95 moved only slightly, 60.412 → 57.353 / 57.470 ms; full-page drawing remains material. Do not claim that all navigation became twice as fast.

The first queue/damage candidate passed the gates but did not improve the input tail (`input-weather-detail-after-20261102/`); the smaller heading feedback was then added. Keep that intermediate evidence. Queue correctness is established by actual native LVGL pointer/page-order tests; do not attribute the entire measured press gain to the queue alone.

All following times are **p50 / p95 / p99 / maximum, ms**. Each run retains 10 fresh update frames per page and 20 remote-pointer taps per page, with the same synthetic data cadence and background cover work. The two final runs total 120 test taps. Small populations have p99 equal to the sampled maximum. No samples or slow maxima were removed.

| Page / measurement | Before | Final | Repeat |
| --- | --- | --- | --- |
| weather / update.refresh_work_ms | 31.957 / 32.830 / 32.830 / 32.830 | 26.488 / 27.605 / 27.605 / 27.605 | 27.157 / 27.658 / 27.658 / 27.658 |
| weather / navigation.remote_press_ms | 16.507 / 30.768 / 31.790 / 31.790 | 3.081 / 16.482 / 17.311 / 17.311 | 2.868 / 16.267 / 16.505 / 16.505 |
| weather / navigation.remote_release_ms | 42.925 / 60.412 / 60.744 / 60.744 | 40.414 / 57.353 / 59.726 / 59.726 | 40.584 / 57.470 / 57.810 / 57.810 |
| calendar / update.refresh_work_ms | 29.997 / 31.332 / 31.332 / 31.332 | 28.750 / 32.073 / 32.073 / 32.073 | 28.715 / 31.978 / 31.978 / 31.978 |
| calendar / navigation.remote_press_ms | 16.192 / 25.830 / 25.989 / 25.989 | 15.329 / 21.453 / 21.745 / 21.745 | 16.240 / 17.434 / 17.762 / 17.762 |
| calendar / navigation.remote_release_ms | 39.987 / 44.168 / 45.812 / 45.812 | 39.332 / 42.404 / 43.723 / 43.723 | 39.112 / 42.762 / 51.639 / 51.639 |
| ai / update.refresh_work_ms | 16.358 / 17.359 / 17.359 / 17.359 | 16.251 / 18.106 / 18.106 / 18.106 | 16.153 / 18.723 / 18.723 / 18.723 |
| ai / navigation.remote_press_ms | 3.988 / 17.142 / 17.806 / 17.806 | 4.454 / 19.304 / 19.533 / 19.533 | 4.538 / 19.423 / 19.712 / 19.712 |
| ai / navigation.remote_release_ms | 35.312 / 38.320 / 38.881 / 38.881 | 35.082 / 40.680 / 40.691 / 40.691 | 33.272 / 37.197 / 37.282 / 37.282 |

The refresh measurement starts at LVGL REFR_START and ends at refresh completion. Applying queued fields and assembling regions before that boundary are not separately timed here. It is not total update CPU time. Press/release samples include main-task work after the remote pointer mark, but exclude GT911 scan and input waiting before that mark. None of these values establishes finger-to-photon latency.

| Page / host or observer measurement | Before | Final | Repeat |
| --- | --- | --- | --- |
| weather / command_host_ms | 71.504 / 80.332 / 108.435 / 108.435 | 69.514 / 77.462 / 78.809 / 78.809 | 69.324 / 78.827 / 81.551 / 81.551 |
| weather / status_host_ms | 24.008 / 53.026 / 63.866 / 63.866 | 23.256 / 57.506 / 84.064 / 84.064 | 24.548 / 56.493 / 68.802 / 68.802 |
| weather / observer_main_ms | 0.505 / 0.559 / 0.571 / 0.571 | 0.508 / 0.597 / 0.599 / 0.599 | 0.521 / 0.558 / 0.620 / 0.620 |
| calendar / command_host_ms | 69.362 / 84.998 / 97.528 / 97.528 | 68.182 / 72.801 / 85.886 / 85.886 | 68.028 / 74.282 / 78.464 / 78.464 |
| calendar / status_host_ms | 23.112 / 39.372 / 45.492 / 45.492 | 21.114 / 50.293 / 52.977 / 52.977 | 19.425 / 40.720 / 48.575 / 48.575 |
| calendar / observer_main_ms | 0.516 / 0.586 / 0.618 / 0.618 | 0.520 / 0.573 / 0.581 / 0.581 | 0.526 / 0.592 / 0.599 / 0.599 |
| ai / command_host_ms | 69.670 / 81.066 / 101.805 / 101.805 | 68.194 / 81.045 / 82.627 / 82.627 | 68.688 / 88.449 / 97.941 / 97.941 |
| ai / status_host_ms | 27.673 / 68.326 / 72.072 / 72.072 | 19.561 / 44.142 / 57.924 / 57.924 | 20.973 / 35.286 / 35.704 / 35.704 |
| ai / observer_main_ms | 0.519 / 0.601 / 0.608 / 0.608 | 0.515 / 0.611 / 0.629 / 0.629 | 0.532 / 0.594 / 0.595 / 0.595 |

Command round trips include the requested 60 ms pointer hold and device scheduling. Status round trips include the Mac/network path and the device handoff. The observer column measures only the UI snapshot cost, with response formatting on the worker.

| Final run / page | Applied fields | Deferred ticks | Replaced pending values |
| --- | --- | --- | --- |
| after / weather | 54 | 56 | 0 |
| after / calendar | 18 | 39 | 0 |
| after / ai | 18 | 29 | 0 |
| repeat / weather | 60 | 41 | 0 |
| repeat / calendar | 18 | 32 | 0 |
| repeat / ai | 18 | 18 | 0 |

Deferred ticks include the initial settle guard as well as input blocking; they are not a count of deferred user commands. Both final runs passed all three cases and restored the latest actual data. The final pending count is zero, test mode idle and page page_glass_v2. Actual brightness 35%, night brightness 15% and daytime volume 65% were preserved.

| Standard production throughput | FPS | Jank % | Repeated frames | Presented interval p50/p95/p99/max | Host status p50/p95/p99/max |
| --- | --- | --- | --- | --- | --- |
| 10 s | 60.384 | 0.000 | 0 | 16.563 / 16.566 / 16.572 / 16.589 | 22.267 / 68.756 / 79.593 / 79.593 |
| 120 s soak | 60.378 | 0.000 | 0 | 16.563 / 16.566 / 16.571 / 16.653 | 21.475 / 48.081 / 71.641 / 96.702 |

The throughput populations cover the listening orb, not every screen. Standard responsiveness also passed against its unchanged policy/baseline:

| Standard navigation / host | Before | Final |
| --- | --- | --- |
| navigation.remote_press_ms | 5.356 / 6.281 / 6.311 / 6.311 | 5.383 / 6.276 / 6.286 / 6.286 |
| navigation.remote_release_ms | 32.075 / 34.790 / 36.616 / 36.616 | 30.282 / 31.726 / 31.801 / 31.801 |
| status_roundtrip_ms | 23.608 / 70.280 / 84.079 / 84.079 | 35.020 / 58.487 / 60.918 / 60.918 |

Native regressions cover pixel equality versus full/unbatched redraws, no drawing for unchanged data, moving flex siblings, capacity fallback, pointer-release/page-first ordering, blank/cancelled taps, wraparound, latest-value replacement, reentrant enqueue and restoring heading opacity. Config tests require both locally included priority headers.

Lossless physical captures were inspected. Calendar and AI captures are pixel-identical to the pre-change load captures. Weather differs only in its live sky text: the saved UI trees confirm Molnigt became Mulet; the controlled graph/forecast pixels match. No visual reference was replaced.

Private raw evidence:

- `.panel-ci/input-weather-before-20261102/`, `.panel-ci/input-weather-detail-before-20261102/`: fresh pre-change measurements.
- `.panel-ci/input-weather-detail-after-20261102/`: retained first candidate, without a material input-tail gain.
- `.panel-ci/input-weather-detail-hint-20261102/`, `.panel-ci/input-weather-detail-hint-repeat-20261102/`: both passing final runs, all raw samples/trees/captures.
- `.panel-ci/input-weather-install-console.log`: missing local header stopped the first build before upload; explicit include list and regression fixed it.
- `.panel-ci/input-weather-hint-install-console.log`, `.panel-ci/last-flash.json`: final installation identity/gates.
- `.panel-ci/input-weather-final-inspect-20261102/`, `.panel-ci/input-weather-final-settings.json`: actual restored state.
