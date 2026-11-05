# Board baseline

Measured on the real panel (Waveshare ESP32-P4 7B, chip rev 3.2), so later changes can be compared with it.
`boot.tsv` has one row per measured boot, `bench.tsv` one per render and memory run, `voice.tsv` one per question; `logs/` keeps the raw logs
(Wi-Fi name and secrets removed).

Measure a new row with the `hallpanel` skill (board on USB; the panel restarts):

```sh
~/Documents/GitHub/agent-skills/hallpanel/scripts/panel.sh baseline "what changed"
```

and for rendering and memory (flashes a benchmark build for a few minutes, then the normal one back):

```sh
~/Documents/GitHub/agent-skills/hallpanel/scripts/panel.sh bench 200 "what changed"
```

and for voice and every page, with Joel talking and tapping while it records:

```sh
~/Documents/GitHub/agent-skills/hallpanel/scripts/panel.sh measure 360 "what changed"
```

`baseline` records 90 s of the serial log from power-on, saves it to `logs/`, and appends the timeline to `boot.tsv`
(`timeline.py` in the skill reads the log). Seconds count from when the port opened, which restarts the board.

## 2026-10-23, the first firmware that works on the board

Commit `f19997e` plus the evening's uncommitted fixes (I2S full duplex, mirrored touch, backlight gamma 1,
Wi-Fi before images, boot cover). ESPHome 2026.9.1, ESP-IDF 5.5.5.

| What | Value |
| --- | --- |
| Firmware | 2 119 818 bytes, 26.1 % of the 8 MB app partition; static RAM 20.9 % (136 788 of 655 360 bytes) |
| setup() starts | 1.5 s |
| setup() done | 4.7 s; the ESP32-C6 (Wi-Fi) link takes most of 1.8–4.5 s |
| Wi-Fi connected | 8.7 s with `fast_connect` (11.7 s with a scan first) |
| Home Assistant connected | 9.8–36.2 s over three boots: it is Home Assistant that reconnects, on its own schedule |
| Wake word listening | 10.2 s |
| Painting shown | 12.5–12.7 s (bg.png, 424 kB PNG, about 3.8 s to download and decode); about 43 s before the boot fixes |
| All 65 images started | 39.7–41.7 s; 3.2 MB of PNG in all, orb frames about 0.32 s each |
| Boot counted good | 61.4 s (ESPHome's fixed 60 s after boot) |
| Crashes, `[E]` lines | 0 |

Three boots of the same firmware (20:14–20:17) differed by under 0.3 s in everything but Home Assistant's
reconnect, so a change of 0.5 s or more in `boot.tsv` is real.

### Rendering and memory (`bench`, 20:20)

The `--bench` build with the voice on: the orb's flow replays every 20 s, the wake word listens, nothing is said.
99 samples over 3 min 17 s.

| What | Value | Target (`docs/measurements.md`) |
| --- | --- | --- |
| Orb page | 12.5 redraws/s median (up to 24.5), 11 ms a redraw | at least 12/s: met |
| Dashboard during page fades | 9 redraws/s, 40 ms median, worst 231 ms | each frame under ~50 ms: the worst frames miss it |
| Worst single redraw anywhere | 237 ms, while the images were still coming in | |
| PSRAM free | 22.2 MB at start, 17.3 MB with every image in (images take about 4.9 MB) | stable |
| PSRAM over the last minute | fell 3 kB | no leak |
| Internal RAM free | 280–311 kB | |

### Voice and every page (`measure`, 20:36)

Joel asked four questions and tapped through every page, with the `--bench` build and the voice logs at DEBUG.
`voice.tsv` has one row per question. "Recognised" is when Home Assistant has the text (its Jarvis pipeline
sends no end-of-speech event), so the time it waits for silence and the speech to text come before it.

| Question | How | Wake/tap → chime | Recognised → first sound | Reply played | End → wake word listens |
| --- | --- | --- | --- | --- | --- |
| Vad är klockan? | wake word | 0.69 s | 0.88 s (Home Assistant answers itself) | 2.0 s | 0.05 s |
| Vad blir vädret imorgon? (said 1.5 s after the last answer) | wake word | 0.23 s | 4.79 s | 12.5 s | 0.04 s |
| När går nästa tunnelbana? (tapped away while he spoke) | wake word | 0.70 s | 5.02 s | 2.0 s, cut by the tap | 0.02 s |
| Vad har jag i kalendern idag? | mic button | 0.46 s | 4.85 s | 13.1 s | 0.05 s |

- A question for Jarvis's agent takes 4.8–5.0 s from recognised to first sound; one Home Assistant answers
  itself 0.9 s. That gap is the agent on Jarvis, not the panel.
- The wake word listens again 0.02–0.05 s after a reply, and "Hey Jarvis" 1.5 s after an answer worked.
- A tap while he spoke stopped him in 24 ms.

Render while tapping through the pages (worst frames are the page fades):

| Page | Render median | Worst frame |
| --- | --- | --- |
| Gå hemifrån (dashboard) | 13 ms | 256 ms |
| Hemmet | 36 ms | 218 ms |
| Natt | 56 ms | 139 ms |
| Tavla | 77 ms | 239 ms |
| Voice page (orb) | 10 ms at 12.5 redraws/s | 221 ms |

PSRAM stayed at 17.3 MB free (lowest 16.3 MB while the replies streamed), internal RAM at least 294 kB free.

Not measured yet, and the next things to get a number for:

- From the moment Joel stops talking to the first sound: needs an end-of-speech mark the pipeline does not send.
- Whether the sound stutters while the radio plays. (With the orb animating it did not: Joel heard no break in the 20:36 session.)

Ideas the numbers point at: the page fades are the slowest frames (a full-screen blend over the painting);
a shorter fade, a move animation or none would help. The orb frames take 30 s of the boot one at a time; fewer, smaller or
parallel downloads, or JPEG for the opaque frames, would shorten it. bg.png takes about 4 s; a JPEG or a
smaller PNG would show the painting sooner.

## Logs

| File | What it shows |
| --- | --- |
| `2026-10-23_1830_crash-loop-before-fix.log` | The boot crash loop: the image chain's `on_error`s nesting until a stack protection fault, then safe mode |
| `2026-10-23_1904_boot-serial-info.log` | A whole healthy boot at INFO, 90 s, with the boot cover and fast_connect |
| `2026-10-23_1939_boot-serial-debug-full-duplex.log` | A boot at DEBUG with the final audio setup (microphones primary, speaker secondary) |
| `2026-10-23_1938_announce-network-debug.log` | An announcement playing over the network log, no `Parent bus is busy` |
| `2026-10-23_2020_bench-orb-demo-voice.log` | The bench run above |
| `2026-10-23_2036_measure.log` | The voice and page session above |
| `2026-10-23_2014_boot.log` and the two after it | The three boots of the final firmware, measured by `baseline` |
| `2026-10-23_1946_touch-mirrored-before-fix.log` | Touches with LVGL's hit test, before the axes were mirrored |
