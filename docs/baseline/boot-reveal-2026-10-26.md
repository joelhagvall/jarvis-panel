# Boot reveal without forced black, 2026-10-26

Joel reported a brief black blink during boot and explicitly requested removing
the orange saved-data text. The previous PWM fade intentionally set brightness
to zero before uncovering the dashboard and waiting for its normal refresh.
It then started the 220 ms brightness transition; retained normal traces show
20–55 ms between the first dashboard refresh and fade/sound action. This is a
concrete software cause consistent with his observed black interval. The report
is not an optical measurement and does not prove that no other physical fault
exists. The previous measured boot-cache build is retained as 4abf7144eb733c149e2df3528a250134.

## Candidate

The final boot_fade exposes the prepared dashboard background immediately and
fades only the splash logo/label drawing opacity over it. It leaves backlight
brightness unchanged. It never animates the group's object opacity, allocates
a full-screen alpha layer, resets the display or calls a nested refresh. A real
alarm bypasses the reveal, and an alarm during it uncovers immediately on the
next animation step; later steps never hide its page again. Startup audio also
explicitly excludes smoke/water/timer alarms. The ordinary LVGL refresh callback
records visibility only after the cover starts exposing content or is hidden.
The configured duration remains 220 ms. The complete-data/cache gate, 8 KiB
image chunks, C6 timing, early compiled splash and quiet-hours/software-restart
audio guards remain. Saved brightness is never changed by this reveal.

The orange saved-data note and timestamp widget, including its expired-cache
warning, are removed as requested. Cache limits/expiry/freshness separation and
the saved-calendar heading remain; old departures/safety/chat are still not
restored. The NVS record schema/payload remains unchanged.

## Verification plan

The generated-configuration regression rejects any backlight/brightness or nested
refresh calls inside boot_done/boot_fade. Real native LVGL runs the actual reveal
helper plus generated render observer under ASan/UBSan. It checks that an opaque
splash does not release audio, steps through at least three mixed frames, checks
monotonic foreground opacity and unchanged group opacity, verifies every software frame
has splash or dashboard pixels, and exercises an alarm during the transition.
The native test does not measure optical LCD output or physical sound.

Fifteen targeted cache/render/boot/status tests pass. The free responsiveness
before cohort is in .panel-ci/boot-reveal-20261026/before-responsiveness/.
Before any restart, test volume is explicitly set/verified at 20% and original
page/day volume is recorded. All full release/HA checks, original visual and
performance policies, 60 fps/120-second soak, normal boot and restoration follow.
Original references are not replaced. No paid model/voice test, commit or push.

## First measured candidate and refinement

Candidate 5d99f6a308b02f17fc86e6a7059815ce passed the full physical pipeline at
.panel-ci/run-20261026-065810-767218/: all local/HA, 14 visual, responsiveness,
60 fps and 120-second soak gates passed. It removed the forced PWM blackout,
but boot.log retained a 141 ms LVGL operation; first dashboard refresh was
8981 ms and the reveal finished at 8982 ms after starting at 8711 ms. This sample does not isolate blend work from concurrent image loading. The
refinement removes the full-screen blend regardless, reducing the required
per-pixel work without claiming the warning alone proves its entire cause.

The final refinement exposes the prepared main background atomically at the
old splash brightness, then fades only the compiled logo/text for 220 ms. No
partially opaque full-screen black rectangle is painted. The native LVGL test
now checks foreground opacity progress with background opacity fixed transparent,
no black software frame and the same completed-render/audio/alarm fences.

The attempted normal USB test stopped before resetting anything because the USB
port was absent. A read-only serial inventory confirmed no ESP32 USB device;
the physical panel remains healthy over its known IP. Wireless checked OTA plus
physical UI/performance gates verify installation; a new USB/power-on or optical
recording is not claimed. The failed host precheck and absent-port log remain
in .panel-ci/boot-reveal-20261026/normal-boots.log. No test added a restart API.

## Final verified installation

Final firmware `b7d1bde085551606f6796b4890402131` passed all stages at
`.panel-ci/run-20261026-071351-880320/report.json`. The suite includes 572 free local
tests, configuration/build, Rust fmt/locked Clippy/tests, boot identity and
65-second health, real navigation/settings, synthetic faults, 14 visual scenes,
idle/animation memory, responsiveness and the configured 60 fps capacity plus
120-second soak. The 65 HA tests passed on the unchanged HA candidate files.
No paid model/voice evaluation or HA software/configuration change occurred.
No golden/profile or performance limit was replaced.

Software boot times in milliseconds; the first column is a measured intermediate
candidate, not the old PWM-blackout release. Reset type is OTA/software, so sound
is intentionally silent. These values do not measure optical LCD output.

| Milestone | Full-background blend | Final logo/text-only reveal |
| --- | ---: | ---: |
| Splash ready | 505 | 506 |
| Initial light write | 505 | 506 |
| Background ready | 8672 | 8651 |
| First exposed dashboard refresh | 8981 | 8763 |
| Reveal start | 8711 | 8692 |
| Reveal end | 8982 | 8935 |
| Reveal elapsed | 271 | 243 |

No LVGL long-operation warning appears in this final boot log. The prior 141 ms
warning and its complete report remain retained; the two samples are not a
controlled optical fade benchmark. The final native regression steps the actual
helper and generated observer through rendered RGB565 frames: main background
fully exposed, group opacity unchanged, at least three foreground blend steps,
no all-black software frame, completion/hide and mid-transition alarm bypass.
The generated boot/reveal scripts contain no backlight call, brightness/transition
setter or nested refresh. This removes the intentional PWM blackout; a camera
measurement or a new normal USB/power-on test is not claimed.

The physical framebuffer's final dashboard was independently inspected and
captured in final-main-view/screen.png. No orange saved-data/timestamp widget
is generated in cached, live or expired-cache paths. Cached calendar heading and
missing-fresh-departure placeholders remain. Cache age/schema/freshness protection
and the original early-load behavior are unchanged.

### Responsiveness and workload scope

The fixed responsiveness workload uses 25 fps; its latency values in ms are
separate from the production 60 fps orb throughput/soak.

| Phase | Metric | p50 | p95 | p99 | Max |
| --- | --- | ---: | ---: | ---: | ---: |
| before | Remote press | 17.704 | 17.968 | 17.989 | 17.989 |
| before | Remote release | 63.227 | 64.870 | 64.939 | 64.939 |
| before | 25 fps refresh interval | 41.752 | 42.578 | 43.198 | 43.557 |
| before | Host HTTP roundtrip | 32.603 | 77.384 | 86.631 | 86.631 |
| after | Remote press | 17.662 | 18.167 | 18.204 | 18.204 |
| after | Remote release | 65.374 | 69.883 | 69.968 | 69.968 |
| after | 25 fps refresh interval | 41.557 | 42.733 | 43.469 | 44.071 |
| after | Host HTTP roundtrip | 24.006 | 86.400 | 124.618 | 124.618 |

Both fixed responsiveness runs pass the unchanged baseline/policy and report
0% jank. The production soak reports 60.377 source changes/s,
0% jank and presented intervals p50/p95/p99/max
16.563/16.567/16.571/16.588 ms.
Soak host HTTP roundtrip is separately
23.958/44.258/76.368/141.232 ms.
HTTP worker minimum free stack is 4272 bytes after all gates.
This steady listening-orb test does not establish 60 fps for every boot frame or
finger-to-photon latency. Raw status/latency/observer timings remain with the run.

### Restored state and remaining verification boundary

Original page was page_glass_v2 and authorized day volume 65%. Temporary test
volume 20% was explicitly set and verified after installation. The original
main view and 65% day volume were restored without playback; no wakeup helper,
quiet-hour or brightness setting was changed. Later manual changes would have
been preserved rather than overwritten. The restored volume is acknowledged in
volume-restored.json and given the normal preference-save window.

The USB port was absent, confirmed by read-only serial inventory, so the planned
USB tests were not performed and their host failure is retained. The final
installation was verified through the checked wireless OTA, physical UI and full
performance gates. No speculative reset route or bypass upload was added.
No new commit/push. Other agents' pre-existing changes remain.

Raw before/after, both candidate reports, native/HA logs, final screenshots and
settings/state are in .panel-ci/boot-reveal-20261026/.
