# Archived boot experience through 2026-10-26

Historical measurements and previous design decisions, preserved without claiming
these versions are currently installed. For current behavior/workflow use
[boot-experience.md](../boot-experience.md); query the panel for live identity.

# Boot experience: installed and verified, 25 October 2026

Joel asked for the fastest practical boot and a deliberate, calm boot appearance.
Firmware `5d99217bc684baeab586f9250b8a2132` is installed. Two distinct software
boots passed all 29 checked stages each, including all images, 14 visual scenes,
memory, responsiveness, production 60-fps capacity and a 120-second soak per boot.
The bounded recovery was completed through the existing tool; quarantine is resolved.
The original failed image/memory/SDIO runs remain preserved; see
[the diagnosis](../boot-image-socket-failure.md). Early NASA refresh is installed on
Jarvis, and clean voice follow-ups are installed on the panel; see
[nasa-refresh.md](../nasa-refresh.md).

## Verified physical results

Both runs used the same immutable candidate and unchanged references/policies:
`.panel-ci/run-20261025-180229-908445/` and
`.panel-ci/run-20261025-180916-003683/`. Boot IDs were 3005641702 and 2107785648;
both reset reasons were 3 (normal software reset). The source-bound resolution is
`.panel-ci/small-read-recovery-20261025.json.resolved.json`.

| Milestone from firmware start | Boot 1 | Boot 2 |
| --- | ---: | ---: |
| First complete UI render | 501 ms | 504 ms |
| Wi-Fi connected | 7,571 ms | 7,550 ms |
| First background | 9,703 ms | 9,643 ms |
| Orb installed | 9,360 ms | 9,439 ms |
| All three backgrounds valid | 13,566 ms | 13,492 ms |
| All resources ready | 13,627 ms | 13,545 ms |

All-resources readiness is approximately 22% earlier than the previous installed
build's measured 17,348 ms. Two runs establish this controlled repeat result,
not a population percentile or the globally fastest possible boot. These are
firmware timestamps after software resets, not electrical power-on to visible
light. The first background still determines normal dashboard uncovering.

Tavla's lossless capture in each run matches the saved pre-change NASA picture
exactly, including all 614,400 pixels. Both logs lack the long OnlineImage blocks
and SDIO receive-queue error observed in the failed 32 KiB repeat. The final
version retains 8 KiB reads. Idle free PSRAM medians were 18,498,824 and 18,498,864
bytes, passing the unchanged measured baseline. No reference was replaced.

### Panel timing and frame pacing

Values below are milliseconds, ordered p50 / p95 / p99 / maximum. Navigation uses
the unchanged 20-tap responsiveness protocol. The production orb uses the separate
original-duration 60-fps protocol; the responsiveness protocol's 25-fps diagnostic
is not presented as production frame rate.

| Device-side measurement | Boot 1 | Boot 2 |
| --- | --- | --- |
| Remote press → next drawn refresh | 18.005 / 18.370 / 18.438 / 18.438 | 17.739 / 18.251 / 18.515 / 18.515 |
| Remote release → next drawn refresh | 62.609 / 65.170 / 65.227 / 65.227 | 63.803 / 65.881 / 66.646 / 66.646 |
| Production presented-frame interval, 120 s | 16.562 / 16.567 / 16.570 / 16.583 | 16.562 / 16.568 / 16.572 / 16.580 |

The two 120-second production runs presented 7,253 and 7,256 new frames at
approximately 60.38/s. Both recorded zero repeated frames, zero >50 ms jank,
zero decode errors and zero prefetch fallbacks. The 25-fps diagnostic also had
zero >50 ms jank. All raw samples, including slow host samples, remain retained.

### Host latency and observer overhead

These values include host/network work and must remain separate from device UI
timings. Command round-trip includes the intentional 60 ms press. Observed page
latency includes polling and confirmation. Values are p50 / p95 / p99 / maximum
in milliseconds.

| Host-side measurement | Boot 1 | Boot 2 |
| --- | --- | --- |
| Navigation command round-trip | 78.973 / 88.692 / 91.984 / 91.984 | 79.131 / 85.509 / 88.970 / 88.970 |
| Navigation page observed | 149.137 / 159.938 / 164.976 / 164.976 | 149.910 / 158.375 / 160.723 / 160.723 |
| Status HTTP during responsiveness | 21.177 / 72.671 / 74.482 / 74.482 | 21.338 / 74.017 / 75.318 / 75.318 |
| Status HTTP during production soak | 20.696 / 31.962 / 48.412 / 66.585 | 20.609 / 31.740 / 48.101 / 147.623 |

On-demand status snapshot work on the LVGL task had p95/max 0.456/0.493 ms and
0.419/0.442 ms respectively. Total status-worker p95/max was 2.565/3.472 ms and
3.318/3.563 ms. Host latency outliers are preserved. These software measurements
do not establish physical finger-to-photon latency, acoustic performance or
optical backlight-fade smoothness.

The final candidate passed 543 free local tests and ESPHome validation. Jarvis's
NASA deployment previously passed 542 local plus 65 HA-runtime tests; the added
543rd local test covers lossless native logo decoding and memory release.
No paid voice/model eval was run. No commit or push was performed.

## Pre-change evidence

Ten successful physical CI reports retained `boot_ready_ms` between 16,545 and
17,348 ms. Fresh status from the then-installed orb firmware reported
17,348 ms. This means assets completed and Wi-Fi was connected; it does not mean
first useful picture, first audible answer, or electrical power-on to visible LCD.
The earlier 16,545 ms example came from another run, not the current firmware.

Older serial evidence in [baseline/boot.tsv](boot.tsv) separates setup,
Wi-Fi and first background, but comes from earlier firmware. Typical historical
Wi-Fi was about 8.9 s and first background about 12.1–12.6 s. Do not combine those
numbers with the new candidate as if they formed a controlled before/after trial.
The 65-second CI boot-health gate is a stability check, not a forced boot screen.

The previous `bg → art → voice_bg → orb atlas` chain was serial. Its atlas is
approximately 4.1 MiB of compressed frames. The three PNGs decode while the main
loop reads only an 8 KiB chunk per pass. Fast-connect and the O2 performance
profile are already enabled. No arbitrary multi-second logo hold was present.

## Installed changes

- Start the existing worker atlas download alongside the background chain as soon
  as Wi-Fi connects. Join both completion paths before setting `assets_loaded`.
  Readiness must work regardless of which request completes first.
- Publish QOI copies of the exact prepared RGB565 pixels and retain PNG copies for
  the installed firmware. The candidate consumes QOI, avoiding PNG DEFLATE/filter
  work. QOI can be larger on the wire: a faster physical boot remains a hypothesis
  until the actual decoder/network balance is measured on the P4.
- Keep online-image chunks at the proven 8 KiB. The experimental 32 KiB chunks
  produced a real SDIO receive-queue stall and 5.5-second main-loop blocks during
  repeat boot. Its failed result is retained; see the diagnosis. Smaller streaming
  chunks also avoid retaining an extra 72 KiB of download buffers.
- Add the compiled Neural Orb mark selected by Joel (blue round option 3). Leave the animated orb as the voice-state
  indicator. The mark is local, so it does not add a network prerequisite.
- Keep the backlight off until a complete LVGL splash has been drawn. Draw after
  LVGL widget setup, before Wi-Fi setup, then start a 180 ms backlight fade while
  the rest of setup continues. Do not hold the cover for a branding minimum.
- Keep `Jarvis` and `Startar …` still during a normal boot. At 15 s, only if the
  cover remains visible, explain a slow image or missing home network. A diagnostic
  API connection no longer pretends to be a Home Assistant progress event.
- Keep uncovering as soon as the first background is available, with the existing
  30-second escape path if it fails. Keep existing alarm/voice/night behavior and
  the muted ready chime after software restarts.

The 180 ms fade is a backlight transition, not a full-screen alpha animation that
would redraw two full layers repeatedly. Its physical appearance still needs a
look on the actual LCD. A framebuffer cannot establish backlight smoothness.

## Independent evidence and limits

`tests/test_boot_experience.py` checks exact PNG/QOI pixel equality with gradients
and independent random colours. Generator checks cover Wi-Fi before any download,
parallel start, the completion join, the first-frame/light ordering, and absence
of a branding wait. Background tests exercise NASA → painting → NASA, matching
sidecars and repair of a missing cached QOI. Asset-sync tests preserve the selected
runtime image in both formats. These checks are not a physical speed benchmark.

`tools/preview_boot.py --out /tmp/NEW_DIR` generates a headless SDL/LVGL preview from
the actual firmware boot widgets, logo and cached Inter fonts. The simulator
snapshot validates composition and text; it does not establish LCD timing.

Private measurements, failed attempts and test/build logs are retained under
`.panel-ci/boot-experience-20261025/`. No new paid model, STT or TTS tests were run.

## Initial local result (before the physical corrections above)

All 533 free local tests and ESPHome validation passed. Local CI
`.panel-ci/run-20261025-154841-329333/report.json` passed all six local stages,
including an actual ESP32-P4 firmware build, restart linkage and recovery-capability
checks. Candidate build ID: `f7afa349312c7d893ad1ccaa77b19977`.
The OTA artifact is 2,982,688 bytes versus 2,787,280 bytes for the installed orb
candidate (+195,408 bytes, about 191 KiB). Flash is 36.7% of an 8,126,464-byte slot;
static RAM is 151,810 bytes. The new logo and decoder are local, but more firmware
bytes can also increase bootloader/XIP work: measure the complete outcome.

The headless preview used the real LVGL renderer and snapshot API, with the actual
boot widgets and fonts. Its selected screenshot is [boot-preview.png](../boot-preview.png).
This passes composition review on the simulator. No physical gate, LCD fade,
cold-start time or post-change speed is claimed. No code was committed or pushed.

## Original measurement and install plan

1. Run the checked Jarvis deploy first (`sh tools/deploy_jarvis.sh`), including its
   local and HA-runtime tests. It must publish matching `bg.qoi`, `art.qoi` and
   `voice_bg.qoi` for the currently chosen picture before the firmware is uploaded.
   Normal service startup warm-up can make production requests.
2. Install only through `tools/panel ci --visual-baseline .panel-visual/reference-calendar-v4`.
   Keep the reviewed reference and all responsiveness/60-fps gates unchanged.
3. Retain boot milestones from status: `boot_ui_ms` (first software refresh),
   `boot_wifi_ms`, `boot_bg_ms` (first background decoded), `boot_backgrounds_ms`,
   `boot_orb_ms`, `boot_voice_client_ms` (Assist client connection), and existing
   `boot_ready_ms`. A zero means not observed; Assist connection is not proof that
   wake-word inference or an audible answer is ready.
4. Compare first-background and all-assets times with the same definition and
   repeated software boots. Keep slow samples. Require a useful repeatable gain,
   no loss of pixel fidelity, and the complete unchanged physical release gates.
   If QOI is slower on this P4/network, retain the parallelism and select the faster
   format based on those results. Do not declare QOI faster merely from host tests.
5. A cold power-on trial is separate. Neither an OTA reboot nor the software
   milestone includes the complete electrical power-on path. Claim no hardware
   minimum until measured; never shorten stability/watchdog gates to fake it.

Further candidates include a router-reserved static IP and local persistent image
storage. An address inside DHCP without a verified reservation risks conflicts.
Compiling the 4.1 MiB atlas into the current roughly 2.7 MiB firmware also consumes
most of an OTA slot and raises PSRAM/XIP costs. Neither trade-off is adopted
without a measured comparison. The current work does not change the router,
partition layout, C6 firmware or security checks.

## Selected logo and resumed home verification

Joel selected **3 · Neural Orb** from the blue round choices, then confirmed home
network access and asked to install/test/verify. The candidate now uses
`assets/branding/jarvis-neural-orb-v1.png`. Its exact ImageGen source/prompt is in
`assets/branding/jarvis-neural-orb-v1.json`. The PNG pixels are unchanged;
metadata alone was removed for ESPHome compatibility. The LVGL preview has been
updated. Physical results will be added after the checked installation.

## First physical attempt: verification stopped

The chosen Neural Orb firmware booted, but Tavla lacked its image after a socket
creation failure. The unchanged visual gate stopped the run and retained
quarantine. The 11.7-second readiness number from that attempt incorrectly included
a failed image and must not be counted as a successful speed result. See
[the socket-lifetime diagnosis and correction](../boot-image-socket-failure.md).
A corrected candidate is being checked locally before a bounded approved recovery.


## Subsequent boot experiment, October 25 evening

The later attempt to shorten boot further panicked and rolled back. Its
conservative recovery candidate replaces the deferred light fade with a direct
output write and waits for the ordinary completed dashboard refresh before
queuing the chime. It retains the working 600 ms C6 timing. It is not installed.
The evidence, uncertainty and required bounded recovery approval are recorded in
[the boot experiment](boot-fast-2026-10-25.md). Earlier measurements
above remain historical; no new optical or acoustic timing has been established.

The following correction also bounds nested status-formatter stack use. A second
panic was identified through FreeRTOS's stack-overflow hook. Target compiler
reports reduce the two nested formatter frames from 5168 to 928 bytes. The first
dashboard additionally waits for its background, clock and 17 initial data
states before uncovering, preventing staggered first text. The raw experiments
and the current verification boundary remain in the linked report.

The stack-corrected runtime subsequently passed the complete physical pipeline,
including normal reboot, 60 fps and the 120-second soak. The legacy and current
orb-v5 memory profiles are kept separately; the [profile record](performance-profile-v5-2026-10-25.md)
explains the measured pre-existing footprint and explicit reference selection.
The user's subsequent 220 ms backlight reveal is evaluated after the coherent
first dashboard, with saved brightness and alarm priority preserved. OTA remains
silent; normal-boot audio is a separate free check.

The final October 25–26 version is installed and fully verified. It retains the
early splash, complete 17-field first dashboard and a 220 ms backlight reveal
(221 ms in both normal hardware checks). Normal hardware boots requested the
chime exactly with the dashboard reveal. OTA and quiet hours remain silent.
Firmware timing is variable: the final OTA dashboard was 8.733 s; USB hardware
boots were 21.166 s and 12.712 s while waiting for HA's first data. These are not
physical power-to-light/acoustic measurements or a guaranteed boot bound.
The repeated hardware check verifies persisted 20% volume. Night start/end are
restored to 22:00/06:00 and the panel is left in Natt. Detailed raw locations,
failed/neutral experiments and unchanged release gates are in the linked report.

## Cache-first boot, October 26

The next verified installed firmware is `4abf7144eb733c149e2df3528a250134`.
Eight read-only weather/calendar fields can restore from a bounded local NVS
snapshot, always visibly marked old with a saved timestamp. Relative metro
countdowns, safety/door states and chat history are never restored as current.
A complete fresh weather/calendar/metro cohort replaces the stale presentation
synchronously. Real local-router NTP complements HA/retained RTC time.
Normal USB-reset first-view measurements were 8.525–8.710 s instead of the
retained 12.712/21.166 s controls, while HA's fresh data still arrived later.
The empty-cache first OTA was 8.773 s; no cold-cache gain or physical power-on
bound is claimed. The 220 ms fade and normal/quiet-hour sound policy remain.
All 572 local, 65 HA and full physical/visual/responsiveness/60 fps gates pass.
See [the cache report](boot-cache-2026-10-26.md) for raw locations,
mDNS-interrupted measurement, memory cost, write budget and exact scope.

The scheduled [private schedule omitted] wakeup intentionally left a 65% day volume, confirmed in HA
history. A dedicated final boot at temporary 20% was verified after reset, then
the original authorized 65% day preference was restored without playback. Natt
uses its normal 35% cap. Wakeup settings and [private schedule omitted] quiet hours are unchanged.

## Remove saved-data note and forced-black reveal, October 26

Joel explicitly requested hiding the orange saved-data note and reported a brief
black blink. That overrides the earlier orange-banner design. Final firmware
`b7d1bde085551606f6796b4890402131` removes the note/timestamp widget on every cache
path and removes the old PWM zero-brightness step. It exposes the prepared
dashboard while preserving backlight level, then fades only the splash logo/text
for 220 ms (243 ms measured software completion in the checked OTA boot).
The first dashboard refresh was 8.763 s in that software-reset sample.

Actual native LVGL frames, generated code and all 572 local/65 HA/full physical
release gates pass. Normal USB/power-on and optical camera measurements are not
claimed because USB was disconnected. See [the reveal report](boot-reveal-2026-10-26.md)
for scope, both candidates, raw results and restoration of main page/day volume.
