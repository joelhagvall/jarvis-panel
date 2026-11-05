# Cached startup experiment, 2026-10-26

Joel advanced the planned [private schedule omitted] task to start immediately. The one-shot schedule
was deleted before work, preventing a second run. Previous work is saved in
commit 61a68aa; pre-existing branding, board/socket/C6/GT911 and boot-log changes
remain in the working tree. This experiment does not commit or push.

## Candidate and boundaries

A fixed 2560-byte versioned/checksummed NVS record stores only eight weather and
calendar presentation fields. It never publishes restored HA sensor states,
restores relative metro countdowns, or caches smoke/water/door flags or chat.
Weather/calendar are explicitly marked “Sparade uppgifter · ej aktuella” with
the saved local date/time; calendar heading says “SPARAD KALENDER”. Departures
wait for fresh states. Cache expiration is 24 hours once trusted time exists;
future timestamps and corrupt/wrong-schema/oversized snapshots are rejected.
With unknown time the saved timestamp is only historical text, never a clock.
Without a valid record the original first-data fence/offline escape remains.
The cached-to-fresh weather/calendar/metro update waits for its complete 13-field
cohort plus valid time and renders in one synchronous group before hiding the
notice. Empty real states are valid and cache contents never satisfy has_state.
The four chat states remain fresh-only, with no saved voice history.

The existing 220 ms fade, normal-render fence, night/audio rules and alarm bypass
remain. A local-router-only SNTP clock provides real time independently of HA.
A read-only UDP probe established that router.example.invalid answers NTP in about 4 ms with
a Unix timestamp within one second of the Mac. No router, host-clock, HA or
firewall setting changed. HA time remains available. No Internet NTP service is
configured. Current-time labels show --:-- when time is unavailable.

Both 2560-byte cache buffers live in the global object, not as task-stack
locals. Writes require 65 seconds of uptime and a real clock, fresh complete
cohort and a real HA voice-client connection, not just an API diagnostics client.
A per-field received-state mask resets on the real HA client disconnect, so
retained sensor has_state flags cannot certify a fresh post-reconnect snapshot. Timestamp-based hourly limits persist across
reboots; unchanged payload needs only a six-hour timestamp refresh. Preferences
uses its existing queued flash synchronization. Oversize/failing saves preserve
the prior record. Native ASan/UBSan tests exercise every-byte corruption, bounds,
age/future/unknown clock, maximum payload, empty data, storage failures and the
cross-reboot write budget. Generated/native render regressions cover freshness
separation, no restored safety/voice states, grouped updates and alarms/offline.

## Measurement plan and retained failures

The unchanged prior runtime has two normal USB samples at 21.166 and 12.712 s
and an OTA sample at 8.733 s for first complete dashboard. See the previous boot
report; all are firmware times, not optical/acoustic power-on measurements.
A fresh responsiveness “before” run is retained in
.panel-ci/boot-cache-20261026/before-responsiveness/.
The candidate must pass the same full checked pipeline and original limits,
with the explicitly measured orb-v5 profiles. Cold/no-cache first boot seeds
the cache only after health/freshness checks; subsequent normal boots establish
actual cache use, first frame, fresh-data timing, time source and audio/fade.

The first full offline test attempt ran inside the restricted sandbox, where
local HTTP test listeners were denied. That failed log is retained as local.log.
Targeted cache/render/format tests passed (14); the complete suite is rerun
with the normal release runner permissions, without skips or weakened checks.
Measurements, full reports and final installation decision follow below.

The first checked build stopped before OTA: this ESPHome release accepts the
script argument dictionary in validation but fails code generation for a lambda
parameter. Generated UI calls now use the normal typed script.execute C++ method;
no firmware was installed by that failed run. The report/log remains at
.panel-ci/run-20261026-055822-869779/.

The second build also stopped before OTA because ESPHome copies only explicitly
listed custom headers: the pure cache header dependency was missing from its
generated source directory. Both adapter and pure header are now declared and
checked in the generator regression. Retained report:
.panel-ci/run-20261026-060215-228462/. The 65 HA tests passed independently.

A subsequent local-only compilation exposed ESPHome's custom-header ordering:
custom global types are declared before includes. The cache now follows existing
bench helpers as one namespaced inline global, referenced directly by lambdas.
The same two bounded buffers remain outside the task stack. No OTA occurred.

SDK source inspection also found a random SNTP startup delay and backoff if its
first request precedes Wi-Fi association. This candidate disables only that
random delay and queues the public esp_sntp_restart on Wi-Fi connect; the UDP
request stays on tcpip. Router-only NTP sync has its own boot_ntp_ms milestone.
HA remains a fallback source of real time; neither source uses saved epoch as now.

## Final installed result

Firmware `4abf7144eb733c149e2df3528a250134` is installed and passed all stages
in `.panel-ci/run-20261026-061033-397559/report.json`: 572 free local tests,
Rust fmt/locked Clippy/locked tests, config/build, 65-second boot health, real
navigation/settings, all 14 visual scenes, memory/animation, responsiveness,
production 60 fps, the 120-second soak and runtime logs. All 65 HA-container
tests passed independently; HA software/configuration was not changed.
The first empty-cache OTA was 8.773 s versus the prior 8.733 s: no cold-cache
first-boot gain is claimed. It saved one snapshot, subsequently proven durable
by normal hardware starts. OTA remained silent as intended.

### Normal USB reset measurements

Times are firmware milliseconds. Every sample is retained, including the host
name-resolution interruption. None is an electrical power-cycle/optical or
acoustic measurement or a guaranteed future boot bound. Cache is never seeded
into the current clock. The local-router NTP probe verifies an available real
time source; `boot_ntp_ms` is the SNTP component's time callback and can also
reflect an already-valid shared RTC after a reset. That callback alone does not
prove a particular UDP exchange supplied the clock. HA remains a real-time source.

| Sample | Light | Wi-Fi | Background | Complete cached dashboard | Live 13-field cohort | Chime request | Fade ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| observed | 508 | 6553 | 8445 | 8536 | 12579 | 8556 | 234 |
| DNS-interrupted, later identity/health reviewed | 508 | 6569 | 8447 | 8537 | 20813 | 8559 | 228 |
| serial only, IP 1 | 508 | 6549 | 8434 | 8525 | 17303 | 8558 | 231 |
| serial only, IP 2 | 508 | 6560 | 8443 | 8534 | 15779 | 8576 | 229 |
| Serial only, verified 20% test | 508 | 6550 | 8612 | 8710 | 7100 | 8765 | 233 |

The two retained pre-change normal resets showed their complete first dashboard
at 12.712 and 21.166 s. Four cache-first views were 8.525–8.537 s. A fifth normal reset, explicitly at
20% test volume, had live HA data before the background and revealed a fresh
view at 8.710 s. All five first-view times therefore range 8.525–8.710 s. The three
fully completed direct-IP, serial-only runs give 8.525, 8.534 and 8.710 s with the same 35-second
serial/65-second healthy-boot protocol as the controls. The first new run adds
5 Hz status observation and a first-view screenshot; its image-copy observer
cost is retained separately (29.852 ms on the main task, 716.6 ms complete host
transfer). It is not treated as an unobserved timing sample. The second attempt
finished its serial-only capture, but Mac mDNS failed when initiating health
polling at 35 s. The run stopped. Subsequent read-only exact-IP inspection proved
the exact candidate, normal reset reason 11 and >65 s healthy uptime. Its raw
serial and the original failed host log remain, and two fully completed direct-IP
checks were added; the interrupted attempt is not relabelled as a complete
original-protocol pass. The actual saved first-view timing is retained.

Four report actual cache use; the fifth correctly skips the stale presentation
because live data won the race. All report the same saved snapshot timestamp, zero
new cache writes and zero cache-save errors. The first initialization made one
write; warm resets did not rewrite flash. The historical boot_data_ms remains
zero for a cache-uncovered boot because that one-shot original 17-state fence
was bypassed. boot_live_ms separately records the actual 13-field fresh cohort.
Chat is never cached and continues receiving real states through its existing
handlers; it is not included in that weather/calendar/metro release cohort.

The first actual cached-view screenshot was reviewed on the physical display's
framebuffer: correct current clock/date, orange “Sparade uppgifter · ej aktuella”
and saved timestamp, “SPARAD KALENDER”, no saved metro countdowns and no saved
conversation. The later fresh view is separately captured and verified to have
removed the stale notice/heading. No golden reference was replaced.

Normal-boot chime requests follow the completed first refresh by 20–55 ms.
Measured fades were 228–234 ms for a configured 220 ms transition. Both quiet-hour
and software-reset guards remain. No detected crash, heap corruption, SDIO card
init failure, audio underrun or frame-presentation fault occurred. This establishes
software playback requests and log health, not physical sound quality.

### Responsiveness, memory and observer cost

The before/after runs use the fixed responsiveness protocol; its animation
workload is 25 fps and distinct from the production 60 fps soak. Values are ms.

| Phase | Metric | p50 | p95 | p99 | Max |
| --- | --- | ---: | ---: | ---: | ---: |
| before | Remote press | 17.806 | 18.388 | 18.525 | 18.525 |
| before | Remote release | 64.037 | 68.372 | 68.485 | 68.485 |
| before | 25 fps refresh interval | 41.656 | 42.632 | 43.049 | 43.346 |
| before | Host HTTP roundtrip | 20.587 | 72.595 | 76.606 | 76.606 |
| after | Remote press | 17.898 | 18.170 | 18.502 | 18.502 |
| after | Remote release | 64.681 | 69.456 | 69.598 | 69.598 |
| after | 25 fps refresh interval | 41.561 | 42.628 | 42.931 | 43.353 |
| after | Host HTTP roundtrip | 20.333 | 76.562 | 113.857 | 113.857 |

Both fixed responsiveness runs report 0% jank and pass the unchanged
baseline/policy. The separate production soak reports 60.377
source changes/s, 0% jank and presented interval p50/p95/p99/max
16.563/16.568/16.572/16.629 ms.
Soak host HTTP roundtrip is separately
20.287/35.459/53.355/67.829 ms.
The status observer snapshots cache counters on LVGL and formats on its existing
HTTP worker; it performs no synchronous network/NVS operation on LVGL. The
actual worker minimum-free stack stayed at least 4272 bytes in the cold release
and these warm checks. Native JSON/stack regression passes.

Comparable idle Hemmet capture cohorts (five samples) have median free internal
RAM 285276 → 272180 bytes and free PSRAM
16915584 → 16868356 bytes. This candidate costs about
12.8 KiB internal RAM plus 46.1 KiB PSRAM in that cohort; it is a
startup-time improvement, not a memory reduction. Total CPU utilization is not
supported by this telemetry; render work/host latency and raw samples are retained
without claiming a made-up CPU percentage. Existing measured orb-v5 profiles,
original references and all absolute/relative limits remain unchanged.

### Final state

The verified faster candidate is retained. The final dedicated 20% audio test was verified in real settings after reboot.
The prior four checks were subsequently found at 65%: the existing [private schedule omitted]
wakeup had intentionally changed volume from 20% to 65% during this task.
HA history confirms [private schedule omitted] volume/playback and completion at [private schedule omitted].
The earlier assumption that 20% still applied was wrong. No volume-policy
firmware bug is inferred. The original authorized 65% day preference was
saved, temporarily set to 20% for the dedicated check, then restored without
playing anything at 65%. Natt applies its existing 35% cap while preserving
that day preference. Wakeup helpers/schedule were never changed. Native API
readback confirms unchanged [private schedule omitted] quiet hours. The panel is restored to
Natt. Jarvis, HA and the previously authorized health/smoke timers remain active.
No host/router/HA installation, paid model/voice test, firmware partition or C6
firmware change was made. This new boot work is not committed or pushed.
The earlier work remains committed as 61a68aa and others' existing changes remain.

Raw final summary, pre/post latency, all four normal captures/status/serial logs,
the mDNS failure, cold release, transient/fresh/settings screenshots and final
state are in `.panel-ci/boot-cache-20261026/`.
