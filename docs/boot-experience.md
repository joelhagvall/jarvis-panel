# Boot behavior and verification

Use `tools/panel status` for the running identity. A version or successful run
recorded in a document is dated evidence, not live telemetry. The previous long
chronology is retained in [the historical record](baseline/boot-experience-history-2026-10-26.md).

## Current design

- Draw the compiled brand frame before blocking Wi-Fi setup and then enable
  saved day brightness. No uninitialized framebuffer is deliberately lit.
- The direct-display install (~2.6 s, during the splash) must never clear the frame buffers: the
  splash is already in the scanned-out one. Clearing both showed a black frame for ~62 ms, the
  "black blink" in the middle of the logo that Joel saw until 2026-10-26; removing it was
  confirmed by eye on a USB restart. The splash colour is the logo's own background (RGB565), so
  no darker box frames the orb and "Jarvis" (tests/test_boot_experience.py checks both).
- Prepare the dashboard under the cover. A bounded local cache may supply only
  weather/calendar presentation. Never publish cached HA states, seed the clock
  from a saved timestamp or restore old departures, safety flags or conversation.
- Real router time/HA/RTC supplies the clock. Unknown time remains a placeholder.
  Fresh weather/calendar/metro values replace the saved cohort together.
- Joel removed the orange saved-data note. Do not reintroduce that older design
  from the historical report. Cache limits/freshness and saved-calendar heading
  remain; no orange timestamp or expiry warning widget is generated.
- Keep backlight level fixed. Replace the opaque splash with the ready dashboard
  in one refresh; never leave Jarvis/Startar foreground over the visible cards.
  No zero-brightness step, alpha overlay or delayed hide. Alarms bypass data waits;
  the ready marker still waits for an exposed refresh. The former foreground fade
  passed a no-black-frame test but visibly superimposed branding on the dashboard.
- Startup sound respects software-restart, quiet-hour and active-audio/alarm
  guards. Test volume is temporary 20%, checked from actual state. A scheduled
  wake may legitimately select 65%; preserve its day preference when restoring.

## Canonical evidence and regressions

| Area | Record | Regression |
| --- | --- | --- |
| Nested status stack / early frame | [Boot analysis](baseline/boot-fast-2026-10-25.md) | tests/test_panel_status_format.py |
| Cache bounds, age, real-state cohort and write budget | [Cache analysis](baseline/boot-cache-2026-10-26.md) | tests/test_boot_cache.py |
| No forced-black gap / exposed-frame marker / alarm bypass | [Reveal analysis](baseline/boot-reveal-2026-10-26.md) | tests/test_boot_render_fence.py, tests/test_boot_experience.py |
| Wake volume and restored night boundaries | [Wakeups](wakeups.md) | tests/test_volume_state.py, tests/test_wakeup_ha.py |
| Wi-Fi setup/association/IP milestones, DHCP without the ARP wait | [Wi-Fi boot analysis](baseline/wifi-boot-2026-10-26.md) | tests/test_wifi_boot.py |
| Background in 0.6 s after Wi-Fi: direct RGB565 QOI decode | [Background decode](baseline/wifi-boot-2026-10-26.md#background-decode-not-the-network), components/runtime_image/README.md | tests/test_qoi_rgb565.py |
| Time before `millis()` 0 (~1.3 s, not in any `boot_*_ms`), no PSRAM memory test | [Before millis()](baseline/wifi-boot-2026-10-26.md#before-millis), `tools/panel boot-wall` | tests/test_panel_performance.py |

Full release commands and immutable input/reference rules live in
[autonomous-ci.md](autonomous-ci.md). Record normal USB/power-on and OTA timing
separately, retain all failed/slow samples and observer overhead, and do not infer
physical flicker, finger-to-photon timing or audibility from software/PNG alone.
A missing USB cable is a missing check, not a device failure or a normal-boot pass.
For a newly found mistake, follow [harness maintenance](harness-maintenance.md)
and update the existing owner rather than appending another parallel handbook.
