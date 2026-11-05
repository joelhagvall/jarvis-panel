# Performance on the real panel, 2026-10-23

Waveshare ESP32-P4 7B rev 3.2, ESPHome 2026.9.1, ESP-IDF 5.5.5, LVGL 9.5.0.
CPU remains at 360 MHz, PSRAM at 200 MHz. No overclock or framework patch.

## Hardware measurements

Final firmware including the volume correction: **3,578,206 bytes, 44.0% of the app partition**,
static internal RAM 141,608 bytes. The render measurements below used the same display code in the
3,578,126-byte build; the final change adds only the volume preference policy.

| Measurement | Earlier measurement | Final RGB565/RLE build |
| --- | --- | --- |
| Touch polling interval (configuration, not physical touch latency) | 50 ms | 10 ms |
| Orb, actual redraws/s | 12.5 | 25.0 median in the listening animation |
| Orb, mean render time per two-second sample, median of samples | 10–11 ms | 7.1 ms |
| Orb, worst frame including entry onto the page | 221 ms | 55.1 ms |
| Dashboard, worst measured frame | 256 ms during the manual voice/touch session | 70.7 ms in the automatic page test |
| Hemmet, page-entry render | Previously up to 218 ms | 68.6–69.4 ms |
| Tavla, page-entry render | Previously up to 239 ms | 53.1–53.4 ms |
| Natt, page-entry render | Previously up to 139 ms | 29.0–29.5 ms |
| Painting visible after restart | 12.5–12.7 s | 12.55–12.61 s over two boots |
| Orb assets ready | Last of 65 image requests **started** at 39.6–41.9 s; completion was not logged at INFO | All 143 frames installed at **20.25–20.31 s** |
| Atlas download and RLE unpack | Not applicable | 3,287–3,330 ms, then incremental delta reconstruction |
| Free PSRAM at the end of the render test | 17,346 KiB | 10,573 KiB, unchanged across the 30-second test |
| Free internal RAM, minimum during render test | 294 KiB in the manual session | 324 KiB |

The before/after workloads differ: the original includes fades and real speech/touch, while the final
automatic run visits five pages three times for two seconds each, without speech. This proves faster
navigation rendering and the higher orb rate in that test. It does not establish physical touch-to-photon
p95, 60 fps, or long-term behaviour under radio playback. A static page's 0.5 redraws/s in this test means
one redraw on entry; it is not the display's refresh rate.

Joel confirmed that the Hemmet tab changes immediately and feels faster. He also confirmed that
"Hey Jarvis, när går nästa tunnelbana?" receives a quick, complete spoken answer without stuttering.
The requested final volume is 20%, verified again after the final reboot. At 123 seconds uptime,
Home Assistant reported 10,827,888 bytes free PSRAM, 332,812 bytes free internal RAM, 48 ms loop time,
and the media player idle at 0.2 volume. Its first startup diagnostic window had reported a 3,325 ms
maximum loop time; startup/network activity is not covered by the steady render figures above.

Raw evidence: [final boot with saved volume](logs/2026-10-23_2315_boot.log),
[render-build boot](logs/2026-10-23_2302_boot.log),
[final render test](logs/2026-10-23_2302_performance-rle.log),
[original manual measurement](logs/2026-10-23_2036_measure.log).

## What changed

- `board.py` sets GT911 polling to 10 ms and removes full-screen page fades on hardware. The simulator
  can still use its design transitions. A native `esphome.hallpanel_performance_test` action runs the
  visual-only benchmark on demand, restores the original page, and stops if a voice run starts.
- `orbs/atlas.py` interpolates the existing opaque frames to 64 listening, 16 morph, 48 speaking and
  15 settling frames, preserving their durations. The thinking state uses every third listening frame.
- The fixed voice background is compiled in. Only the two changing paintings and one
  `orb_atlas_v1.rle` file are fetched from Home Assistant, instead of 65 separate PNG files.
- The atlas stores native little-endian RGB565 words, XOR differences between successive frames,
  and bounded 16-bit PackBits runs/literals. `atlas_loader.h` checks the header, dimensions, content
  length and output bounds, and has a 30-second overall deadline. `atlas_codec.h` limits expansion
  per call. Delta reconstruction yields between frames. Failed loads keep the compiled static orb.
- `orb_atlas.h` installs LVGL frame descriptors into that one PSRAM allocation. Playback does not
  decode, scale, allocate, or copy entire image frames. Morph and settle retain one-shot playback.
- A PNG atlas was also tested and rejected for production: even after lossless RGB565 precision
  reduction and delta encoding, its per-pixel decoder kept startup around 60 seconds. The final
  RLE path is substantially faster despite transferring more bytes than the delta PNG.

DMA2D was already enabled in ESPHome's MIPI driver. PPA rendering remains disabled because the installed
ESPHome version explicitly disables it pending upstream fixes. No unsafe PPA override was added.

## Voice

`server/quick_reply.py` answers exact, simple Swedish questions about the clock, metro departures,
weather for today/tomorrow, and today's calendar without a model. It uses existing source code/caches,
skips cancelled/past trains, preserves the calendar's silent-topics rule, accepts a calendar snapshot
only up to 30 seconds old, and declines unknown/all-day calendar formats. Compound requests, follow-ups,
English, incomplete forecasts, disruptions and ambiguous phrases continue through the existing agent.
The normal server still records both sides in history.

Measured HTTP round trips on Jarvis after deployment, with cached weather:

| Question | HTTP response | Direct-handler time | Model cost |
| --- | --- | --- | --- |
| Vad är klockan? | 33 ms | <1 ms | 0 |
| När går nästa tunnelbana? | 177 ms | 156 ms | 0 |
| Vad blir vädret imorgon? | 18 ms | 1 ms | 0 |

These timings end at the text response. STT and TTS are additional and must not be compared directly
with the original 4.8–5.0 seconds from recognition to audible sound.
The metadata for Joel's subsequent real microphone question recorded the direct handler at **118 ms**
to both first text and complete reply. No conversation text was exported for that metadata check.

The speech endpoint now defaults to 0.8 seconds of silence. `number.jarvis_talpaus` adjusts it from
0.3 to 2.0 seconds and restores it after HA restarts. Recording still supports up to 60 seconds. The
setting is sampled once per turn. TTS logs time until its first audio chunk is ready.

The physical test also exposed a volume preference bug: leaving the night screen restored the old day
volume, and returning to it capped that stale value to 35%. The user's volume is now a restored global,
and manual changes update it. Automatic changes are matched against their deferred callbacks, so the
night cap does not overwrite the preference. Startup waits for the old media restore callbacks before
applying it. The ready sound uses the preference too, rather than forcing 70%. `bench/volume_state.h`
contains this policy and its native C++ regression covers startup, queued caps, manual changes and NaN.

## Verification and trade-offs

68 local tests passed, including a native C++ harness exercising the firmware decoder with one-byte
fragments, long runs, literal boundaries, truncated inputs, zero-length tokens, oversized runs and
trailing bytes. Another 16 tests passed inside the real Home Assistant container, covering streaming,
speech segmentation, adjustable pause and invalid setting values. Hardware and SDL builds passed;
SDL screenshots confirmed the native RLE atlas renders correctly.

`compare` flags the intended increase in firmware size and decrease in free PSRAM. It reports improved
rendering, no crash increase, and no startup-time regression outside tolerance. The fixed voice
background adds about 1.2 MB to firmware; denser animation uses more PSRAM. The final build still has
56% of its app slot and roughly 10.8 MB of PSRAM free. The 30-second render test showed no memory loss;
this is not a multi-hour soak test. No commits or pushes were made for this work.

## Immediate brightness dragging (23:24)

Skärmljus now previews each slider value locally with a zero-length transition, without publishing
or saving intermediate values. Release commits the day brightness without the previous 300 ms fade.
Night brightness keeps its existing preview behaviour. The board configuration validates, the
targeted generated-board check passes, and the hardware build/upload succeeded (3,578,606 bytes flash,
141,680 bytes static RAM). Joel confirmed the light follows the finger before release on the real
panel. Home Assistant reconnected; speaker volume remained at 20%.
