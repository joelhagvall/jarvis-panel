# Performance and flicker correction, 2026-10-24

Hardware and framework versions match [the previous report](performance-2026-10-23.md).

## Display

Joel reported flickering text around the brightness slider and a flickering orb after the
single-buffer DIRECT experiment. That experiment rendered into the buffer currently being
scanned by the LCD. The production default now uses two hardware framebuffers, LVGL DIRECT
rendering, and the driver's frame-completion interrupt to release the previous framebuffer.

Submitting a frame returns immediately. LVGL's buffer stays busy until the interrupt calls
`lv_display_flush_ready`. If the next redraw arrives before completion, a semaphore puts the
render task to sleep. Touch, audio and network work can continue between submission and the
next LVGL refresh. This replaces the earlier double-buffer experiment's synchronous wait.

The driver is a local ESPHome 2026.9.1 MIPI component with a configurable framebuffer count
and access to its existing panel handle and completion semaphore. It retains its original
licenses. The retained ESPHome auxiliary draw buffer is half of the screen rather than
100%; normal rendering uses the two hardware buffers. The auxiliary buffer remains allocated
in PSRAM for ESPHome's own paused-screen paths. A first attempt to request 5% was rounded up
to one eighth by ESPHome and allocated 150 KiB of internal RAM. A 33% request also selected
the internal-memory bucket (one quarter). ESPHome supports 1, 1/2, 1/4 and 1/8 sizes rather
than arbitrary fractions. Requesting 50% explicitly selects a half-screen PSRAM buffer and
preserves the previous internal-memory margin for audio/network work.

The earlier double-buffer experiment averaged about 16 ms per orb redraw because submission
waited for a display frame. The subsequent single-buffer experiment averaged 8.6 ms at 25 fps,
with dashboard entry up to 66.4 ms, but caused visible defects reported by Joel. CPU render
times for the new asynchronous implementation exclude time between submission and physical
presentation. They cannot establish touch-to-photon latency or absence of visible flicker.

## Jarvis and startup

- Calendar/home/API warm-up runs independently of the question lock. Calendar prefetch has its
  own reusable MCP session. A calendar write invalidates the snapshot generation, preventing
  an older background read from restoring stale context.
- OpenAI/Anthropic warm transports are independent of the foreground connection and are only
  handed over while the question lock is available.
- Streaming speech releases a complete sentence after 120 ms without more text, while preserving
  abbreviation and numeric-ending guards. At most two TTS jobs run concurrently. Cancellation
  closes the upstream stream and cancels outstanding synthesis.
- Atlas HTTP and RLE decoding run in a bounded ESP32 worker task. LVGL installation stays on the
  main thread. In the previous measured boot, download/unpack took 1,274 ms and all 143 frames
  were installed at 18.90 s, versus 20.25–20.31 s before this worker change.

These server/HA changes were deployed on October 23. The reusable calendar-prefetch session and
the metro correction below were deployed on October 24. HA did not need another restart.

## Metro ordering

The panel takes the closest four eligible departures across both directions, ordered by exact
expected time before rounding to displayed minutes. It no longer reserves two rows per direction.
Scheduled time is used when expected time is absent. Cancelled, inhibited, past and malformed
departures are excluded. Existing walking-time advice is preserved. The agent's home context
also lists departures chronologically.

Live SL verification at 06:22 on October 24 returned 5, 8, 12 and 15 minutes in panel row order.
Free regression tests cover unsorted input, multiple trains in the same direction, ordering
within a displayed minute, expected versus scheduled time, cancellation and malformed data.

## Verification

228 local tests and 18 tests in the existing Home Assistant container passed. ESPHome configuration
validation passed. No paid model evals were run.

Joel confirmed both the brightness-slider area and orb were free from flicker with asynchronous
double buffering. The 33% auxiliary-buffer intermediate build recorded a task-watchdog reset;
its retained report appeared on the following boot. The final 50% auxiliary-buffer configuration
restored 324–325 KiB free internal RAM. The following transparent-atlas boot had no crash report
or new crash in its 90-second log. This is a short verification, not a multi-hour soak test.

## Current painting and subtle voice-page glass

Joel then found that the voice page still used the fixed Starry Night background, including
the pixels baked into the opaque orb frames. The orb now uses the original RGBA artwork,
stored as native RGB565A8 planes in `orb_atlas_v2.rle` (HPA2). The RLE bounds checks and
per-frame delta reconstruction support both the opaque HPA1 and transparent HPA2 formats.
Native decoder tests verify RGB and alpha planes, transparent edges and one-shot endpoints.
The old atlas remains published for firmware rollback.

The voice page follows the selected current painting. A later request adds a very light
two-pixel frosted-glass blur and faint milky highlight, retaining the approved dark tint for
white text. Jarvis prepares and caches `voice_bg.png` when the chosen painting changes.
The panel fetches it in the same update chain as bg/art, including daily NASA updates, and
repaints the voice page. There is no per-frame glass filter or black overlay on the panel.
Regression tests cover image selection, republishing, reuse of cached glass and generated
update hooks. Tests keep generated caches in temporary folders.

The transparent atlas has 143 frames, 20,763,600 decoded bytes and a 4,478,112-byte transfer.
It costs 6,921,200 more bytes than the opaque atlas, while removing the fixed background
from firmware saves 1,228,272 bytes of flash. Before cached glass, the transparent version
used 2,349,980 bytes flash and 138,494 bytes static RAM, with 4,256 KiB free PSRAM and 325 KiB
free internal RAM. Atlas loading/unpacking took 1,984 ms and all frames were installed at
18.51 s. The background appeared at 12.22 s. Joel confirmed that the voice page showed the
correct current painting.

The first transparent version still drew a full-screen tint layer. Its two-second orb samples
were around 24 fps and 17.5 ms per redraw including page entry. Preparing the glass on Jarvis
also removes that repeated tint blend. Final cached-glass measurements follow below.

The first cached-glass run reduced orb redraw time to 11.8–11.9 ms, with page-entry frames
around 49 ms rather than 93–96 ms. It held 2,989–2,990 KiB free PSRAM and 327 KiB free internal
RAM. The third PNG fetch moved atlas completion to 21.47 s; the painting still appeared at
12.63 s. No crash or new error appeared in the 90-second boot log.

Joel requested clearer text after this preview. The listening line now uses 22 px semibold
full-white text. The voice callback previously restored 60% opacity on each new question;
it now preserves full opacity. A regression checks both the label and that callback. The
final cached background is uniformly frosted and tinted, with no rectangular pane or frame:
Joel rejected the outlined text panel in the preview. The larger, full-white label improves
contrast without putting another object behind the text. Cached backgrounds have a style
version in their key and the published background version, so changes are fetched without a reboot.

The final-readable-text boot exposed one `ISR event queue overflow` in the speaker while
the ready chime overlapped PNG loading. The driver recovered by restarting its speaker task.
The ready chime now waits for `assets_loaded` and an idle voice assistant (up to 45 seconds),
then checks the conditions again. It skips the chime if still busy. Boot-cover removal and
the early painting are unaffected. A generated-board regression covers the wait and guard.

## Installed result before memory reduction

| Measurement | Final result |
| --- | --- |
| Firmware / static RAM | 2,350,828 / 139,142 bytes |
| Painting visible | 12.56 s from serial reset |
| All 143 transparent frames installed / ready sound | 21.68 s / 21.68 s |
| Atlas HTTP + RLE unpack worker | 2,007 ms |
| Orb redraws, median of three two-second samples | 24.5/s (24.5–25.0/s) |
| Orb mean redraw time, median of samples | 12.1 ms |
| Orb worst frame, including page entry | 51.1 ms |
| Dashboard / Hemmet worst entry render | 64.9 / 64.6 ms |
| Tavla / Natt worst entry render | 47.1 / 21.9 ms |
| Free PSRAM throughout the 30-second page test | 2,989 KiB; no decline |
| Lowest PSRAM since boot, including sound/asset transients | 1,960 KiB |
| Minimum free internal RAM during page test | 323 KiB |
| Crashes / error lines in final 90-second boot | 0 / 0 |

The corrected ready chime played after the atlas was installed on two consecutive boots,
without another speaker queue-overflow error. A subsequent live check at 185 seconds uptime
reported 3,061,624 bytes free PSRAM, 331,076 bytes free internal RAM, a 34 ms loop maximum
in the current diagnostic window, and volume still 0.2 with playback idle.

The published voice background was verified byte-for-byte against the cached derivative of
the current painting, using style `voice-v3` without a frame. The panel fetched all three
background images after publication. A live production `/panel` query returned departures
in 4, 8, 12 and 16 minutes, in that order.

The baseline comparator flags the intentional increase in firmware size versus the original
2.1 MB builds and the lower PSRAM headroom. Dense transparent animation and the current glass
backdrop consume more memory. Internal RAM, navigation rendering and orb redraw rate compare
favourably; the current CPU redraw average is 2.1 ms above the old 12.5 fps orb's 10 ms average.
The transparent cached-glass version is about one third cheaper per redraw than the earlier
transparent version that blended its tint every frame. These are CPU render measurements,
not physical touch-to-photon timings. There was no paid live-model timing run or multi-hour soak.

Evidence: [final boot](logs/2026-10-24_0701_boot.log),
[final page test](logs/2026-10-24_0701_final-performance.log),
[opaque asynchronous display test](logs/2026-10-24_0631_performance-double-psram-aux.log),
[transparent version before cached glass](logs/2026-10-24_0639_performance-alpha-live-background.log).


## Lossless animation memory reduction

Joel requested more memory headroom while preserving the accepted responsiveness and flicker
fix. The raw transparent atlas retained 20,763,600 bytes for 143 frames. HPA3 instead retains
4,305,012 bytes of independent lossless LZ4 frames, including its index and packet headers.
The 16,458,588-byte reduction does not alter frame count, resolution, RGB565 pixels, alpha,
interpolation or animation timing. The asynchronous double framebuffers and PSRAM auxiliary
buffer remain unchanged.

Each packet uses LVGL's native compressed-image header. Its bundled LZ4 decoder expands the
current 145,200-byte frame for rendering. The decoded-image cache is explicitly disabled,
so cycling through animations cannot accumulate a second full raw atlas. A bounded worker
fetches the compressed package without allocating the raw atlas or blocking the main loop.
Package size, index order/alignment, packet bounds, codec and raw size are validated before
LVGL sees the descriptors. Legacy HPA1/HPA2 assets remain available to older firmware.

New free tests decompress all 143 packets and compare their full RGB and alpha bytes with
the existing frames. Native C++ parser tests run with AddressSanitizer and UndefinedBehaviorSanitizer
and reject truncated packages, invalid offsets, wrong codecs and invalid frame sizes. Host
decoding checks establish exact pixels, not device performance; installed measurements follow.

The first device candidate downloaded the package but did not draw the orb because ESPHome's
LVGL defaults disable `LV_BIN_DECODER_RAM_LOAD`. Joel saw the missing animation and the page
test logged the corresponding decoder error. This intermediate sample is retained in the
boot table and is not accepted as a performance result. The corrected candidate enables the
native RAM decoder explicitly alongside internal LZ4. A compile-time guard and generated-board
regression now require all three options.

With the native decoder working under the default size profile (`-Os`), two page tests held
19,242 KiB free PSRAM with no decline and 324 KiB free internal RAM. The six orb samples had
a median of 24.15 redraws/s and 17.9 ms CPU rendering time, versus 24.5/s and 12.1 ms with
the full raw atlas. Joel confirmed the orb was visible. These results show that memory was
recovered, but native decoding added CPU work. The final comparison therefore also tests
ESP-IDF's supported performance optimization profile (`CONFIG_COMPILER_OPTIMIZATION_PERF`,
`-O2`) rather than the default size profile. This does not change CPU/PSRAM clocks, display
buffering, frame contents or animation timing.


### Installed memory result

The final installed build uses the native compressed atlas and ESP-IDF's performance profile.
All 230 local tests and ESPHome validation passed. The unchanged HA code had also passed its
18 candidate tests during asset deployment. The final strengthened native parser test passed
separately. No paid model calls were used. Joel confirmed the final animation is smooth and
has no visible flicker.

| Measurement | Before memory change | Final installed build |
| --- | --- | --- |
| Firmware / static RAM | 2,350,828 / 139,142 bytes | 2,658,484 / 145,002 bytes |
| Retained orb package | 20,763,600 raw bytes | 4,305,012 compressed bytes |
| Free PSRAM during page tests | 2,989 KiB (3.06 MB) | 18,950–18,951 KiB (19.41 MB) |
| Lowest free PSRAM since boot | 1,960 KiB | 17,925 KiB |
| Minimum free internal RAM during page tests | 323 KiB | 317 KiB |
| Orb redraw rate | 24.5/s median, three samples | 24.2/s median, six samples (24.0–24.9/s) |
| Orb CPU render time | 12.1 ms | 17.7 ms |
| Orb worst CPU frame including entry | 51.1 ms | 56.9 ms |
| Dashboard / Hemmet worst entry render | 64.9 / 64.6 ms | 66.2 / 62.4 ms |
| Tavla / Natt worst entry render | 47.1 / 21.9 ms | 46.5 / 22.2 ms |
| Painting visible | 12.56 s | 12.37 s |
| All animation frames installed / ready chime | 21.68 s | 19.48 s / 19.48 s |
| Atlas download | 2,007 ms including raw expansion | 1,589 ms; no full-atlas expansion |
| Final 90-second boot crashes / errors | 0 / 0 | 0 / 0 |

This recovers approximately 16.34 MB of live PSRAM headroom after allowing for the larger
performance-profile firmware. A live reading at 123 seconds uptime showed 19,405,740 bytes
free PSRAM, 324,792 bytes free internal RAM, a 43 ms loop maximum in that diagnostic window,
and playback idle at volume 0.2. Two page-test runs had a 1 KiB range in free PSRAM, with no
progressive decline. This is a short device verification, not a multi-hour soak test.

The `-O2` experiment did not materially reduce the added orb decoding cost: 17.7 ms versus
17.9 ms under `-Os`. The supported performance profile remains installed, with about 300 KiB
more firmware and 6 KiB more static RAM than the compressed size-profile build. The visible
animation remains around its existing 25 fps target. CPU time per redraw is higher than the
raw-atlas build; it is not a CPU-rendering speed improvement. Navigation render measurements
remain in the same range, and physical touch-to-photon latency was not measured.

The baseline comparator flags firmware growth and the higher orb CPU render average. It
reports improved PSRAM headroom, comparable redraw rate and no boot-time/crash regression in
its tracked fields. The final boot's HA API connected at 14.05 s rather than the prior 9.19 s;
painting visibility and asset completion were earlier. One boot cannot establish whether
that connection delay is systematic. No further audio/model test was requested; volume was
verified rather than raised.

Evidence: [final boot](logs/2026-10-24_0734_boot.log),
[final two page tests](logs/2026-10-24_0734_packed-performance.log),
[working compressed size-profile comparison](logs/2026-10-24_0727_packed-size-performance.log).
