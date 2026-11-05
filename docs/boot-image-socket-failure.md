# Boot image socket failure: diagnosis and prepared correction

The first physical Neural Orb installation on 25 October 2026 was stopped by the
unchanged visual gate: `Missing widget screen/0` on Tavla. The firmware had a clean
software reset, passed boot identity/health, navigation and synthetic UI modes.
The failure and every original artifact remain in
`.panel-ci/run-20261025-161025-567117/`. Quarantine was retained throughout the
corrections and was resolved only after the two complete final runs below.

## Observed evidence

The boot log contains `Failed to create socket`, `ESP_ERR_HTTP_CONNECT`, and
`Could not download art` at firmware time approximately 9,527 ms. The exact socket
errno was not logged; this is not a measured EMFILE count. The configured lwIP
maximum is 10 sockets. Atlas fetching ran concurrently with online images.
The Tavla capture was black except for its title/credit, and its image widget was
absent from the visible tree. This was an actual missing image, not a golden-mask
problem. No reference has been changed.

The 11,707 ms `boot_ready_ms` from this boot is **invalid as an all-assets speed
result**: the old completion semantics advanced after failed downloads and set
`assets_loaded` anyway. Do not use it as evidence of a successful speed-up.

The installed ESPHome 2026.9.1 `OnlineImage::loop()` calls
`download_finished_callback_.call(false)` before `end_connection_()`. The generator
opened the next image from that callback without yielding. Consequently the
previous HTTP connection remained live while the next one was opened; adding an
atlas worker increased concurrent socket pressure. A host regression reproduces
`art` refusing to open with one available image socket under this exact callback
ordering. This validates the resource-lifetime bug; it does not count every real
socket on the P4 or prove the unlogged errno.

## Prepared correction

- Yield one scheduler turn (1 ms) before starting the next successful image, at
  boot and during later image refreshes. The preceding OnlineImage callback can
  return and close its response before another connection opens.
- Retry an initially failed image at most twice, with a 250 ms yield before each
  attempt. Error callbacks cannot recursively exhaust the task stack. After three
  failed attempts the chain advances to preserve the usable dashboard, but the
  all-assets health claim remains false.
- Track successful background images independently. `assets_loaded` requires all
  three valid images, the background-chain completion and a successful native
  orb installation. A failed atlas download/install cannot count as success.
- Leave unavailable timing milestones at zero. The 65-second release-health wait,
  visual references, throughput limits, watchdog and restart guard are unchanged.

## Free regression evidence

`tests/test_boot_experience.py` records both original failures before the correction:
callback nesting tries `art` while the first image still owns its socket, and the
old readiness expression accepts missing images. The corrected tests pass.
The readiness truth table covers all 64 combinations of prior-ready, chain done,
orb installed and the three image-valid bits. A separate test drives every initial
image request to failure and proves exactly three attempts per image, with yielding
and no unbounded recursion. Raw red/green logs are retained in the private
Neural Orb verification artifacts.

## Required physical verification

A checked candidate must pass full local CI before a bounded recovery plan is
prepared. The repository's existing recovery command requires explicit human
approval bound to that exact plan/artifact/quarantine. Keep quarantine until both
normal physical runs in that plan pass all unchanged gates; stop on any failure.
No repeat OTA is issued on the basis of this document alone.

A later ordinary background refresh restored the currently missing Tavla image,
verified with a fresh lossless capture and the restored NASA selection. It did not
exercise a boot or validate the corrected firmware. Quarantine remains intact.

## First authorized corrected installation

Build `326f64f44fa4749a8c50c6a569f6f941` booted cleanly with all images valid.
Its `boot_ready_ms` was 13,396, first UI render 486 ms, first background 9,493 ms,
and all backgrounds 13,340 ms. All 14 unchanged visual scenes passed, including
Tavla. The run stopped at the unchanged memory comparison: free PSRAM 18,258,884
bytes versus baseline 19,352,524, a drop of 1,093,640 bytes against an allowed
967,626.2. The failure is retained in `.panel-ci/run-20261025-171453-208778/`.
No second attempt under that plan was made. Responsiveness/60-fps gates were not
reached, so this is not a fully verified release or repeat boot result.

The most recent fully verified orb build had 18,396,332 bytes free at boot.
The new 320×320 RGB565 brand image adds 204,800 bytes of firmware rodata. ESP-IDF
XIP places this code/rodata in PSRAM too. The next correction embeds the identical
pixels as LVGL's existing native LZ4 image: 38,334 bytes including its header,
166,466 bytes fewer resident asset bytes. Actual P4 free memory must still pass
the unchanged baseline; this static saving is not substituted for measurement.

`tests/test_boot_logo.py` decodes the actual compiled header with real native LVGL
101 times under address/undefined-behavior sanitizers. Every decoded byte matches
ESPHome's original RGB565 encoding; no decoder allocations remain after closing
or LVGL shutdown. The generated payload also matched the prior firmware's
204,800-byte compiled array exactly. The compressed source is bound after LVGL
widget creation (priority 394), before first render/backlight (390). The existing
atlas install clears image-cache decode buffers. The logo keeps its size and
position, with no new network request or branding delay.

Joel explicitly authorized fixing this memory failure and another boot:
“Men lös problemet, lös det, snälla. Alltså fixa och sen gör en annan boot, jag
godkänner.” A new source-bound plan must retain the old failed attempt and all
unchanged gates. This authorizes the stated correction and controlled recovery;
it does not authorize baseline replacement or ignoring another failure.

## Repeat boot exposes oversized SDIO reads

The losslessly compressed-logo candidate `c6cbe3343829319d34a1963525ea31b0`
passed all 29 stages in `.panel-ci/run-20261025-173509-922472/`. Its boot milestone
was 13,515 ms, first UI 501 ms, and measured idle PSRAM 18,425,128 bytes. Tavla
matched the earlier saved NASA capture pixel for pixel. The production 60-fps
capacity gate and 120-second soak passed, as did every visual/responsiveness gate.

The second run, `.panel-ci/run-20261025-174205-564766/`, retained valid images and
passed memory/visual checks, but `boot_ready_ms` reached 25,242, failing the
unchanged boot comparison. Wi-Fi (7,717 ms), first background (9,609 ms) and orb
(9,699 ms) were prompt. The last background finished only at 25,186 ms. The 25-fps
diagnostic itself was normal: 24.93 redraws/s and 7.42 ms average rendering. This
is a boot regression, not measured degraded steady-state animation.

The actual boot log reports `H_SDIO_DRV: task still writing Rx data to queue!`
during the last image, then OnlineImage loop blocks of 666, 866, 5,498 and
5,026 ms. OnlineImage passes the configured chunk size to the blocking
`HttpContainerIDF::read()` / `esp_http_client_read()` path. The earlier successful
boot also had approximately 124 ms normal operations with 32 KiB chunks.
These observations establish SDIO backpressure/main-loop blocking; they do not
establish every internal coprocessor queue state.

The next candidate restores the previously used **8 KiB** read/buffer size on all
three streamed images. It retains parallel atlas loading, QOI, bounded image
retries, truthful readiness and the compressed brand pixels. The smaller chunks
also remove 73,728 retained buffer bytes. No driver tuning, timeout relaxation,
baseline replacement or unbounded retry is introduced. Another pair of full
source-bound physical runs must establish real repeat behavior; a configuration
check alone does not prove the hardware stall is fixed.

## Completed verification

The 8 KiB candidate `5d99217bc684baeab586f9250b8a2132` passed every one of 29 stages
in both `.panel-ci/run-20261025-180229-908445/` and
`.panel-ci/run-20261025-180916-003683/`. Distinct clean software boots reached
all-assets readiness after 13,627 and 13,545 ms. Both Tavla captures match the
saved NASA picture exactly. Neither boot log contains the prior SDIO error or
long OnlineImage operations. Memory passed its original baseline on both runs.
Both production 60-fps soaks lasted 120 seconds and passed without repeated frames,
jank, decoder errors or fallbacks. Responsiveness and all visual scenes passed.

`tools/panel recovery finish .panel-ci/small-read-recovery-20261025.json` succeeded
and archived the original quarantine in its resolved record before removing the
active guard. Automatic host restore remains disabled. The approved diagnosis
bytes are archived separately under `.panel-ci/three-fixes-20261025/` so this final
documentation update does not erase the source-bound approval evidence.

Full p50/p95/p99/max device, frame-pacing and host measurements are reported in
[boot-experience.md](boot-experience.md). The corrected flow passed 543 free local
tests and configuration/build checks. No failed sample, reference, limit or prior
attempt was replaced. No paid voice/model eval, commit or push was run.

After an approved installation, require a visible Tavla image, exact before/after
Tavla pixels when its source is unchanged, all three background dimensions valid,
all-assets readiness with no failed image, plus every normal visual, response and
60-fps stability gate. Only then report successful boot timing. The same approved
second boot gives a separate repeat timing and exercises shutdown of the corrected
firmware. This does not measure electrical power-on or optical fade smoothness.
