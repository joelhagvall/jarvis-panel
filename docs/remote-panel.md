# Physical panel screenshots and touch

The production panel exposes an authenticated local interface on port 8080. It works over
Wi-Fi when USB is disconnected. Use the repository's CLI from any coding agent on this Mac:

```sh
.venv/bin/python tools/panel_remote.py status
.venv/bin/python tools/panel_remote.py screenshot -o /tmp/hallpanel.jpg
.venv/bin/python tools/panel_remote.py screenshot --lossless -o /tmp/hallpanel-exact.png
.venv/bin/python tools/panel_remote.py ui
.venv/bin/python tools/panel_remote.py tap-text Hemmet
.venv/bin/python tools/panel_remote.py tap 700 550
.venv/bin/python tools/panel_remote.py swipe 850 300 200 300 --duration 300
```

## Common agent entry point

Use `tools/panel` for new agent work. It locates the repository and `.venv` itself and works
from any current directory. The shared hallpanel skill points to this script for Claude Code,
Codex, Gemini and other agents. Code stays in this repository.

```sh
~/Documents/GitHub/hallpanel/tools/panel inspect
~/Documents/GitHub/hallpanel/tools/panel tap-text Hemmet
~/Documents/GitHub/hallpanel/tools/panel inspect
~/Documents/GitHub/hallpanel/tools/panel smoke --out /tmp/hallpanel-ui-check
```

`inspect` saves `screen.jpg`, `ui.json`, `status.json` and `snapshot.json` in a new private
folder and returns their absolute paths. Add `--lossless` for `screen.png`. The response
includes the active page and a consistency check across the reads. Inspect the returned
image with the agent's image viewer, choose the next action from the fresh image/widget
tree, then verify the resulting view. No manual setup, firmware flash or Joel's touch is
needed for ordinary UI work within the task he authorized.

`Panel request failed: [Errno 8] nodename nor servname provided` means `hallpanel.local` does not resolve: the panel is
off or off the network (it was switched off on 2026-10-30); `preflight` does not contact the network, so it still says ok.

When JPEG returns `503` with `JPEG encoder unavailable`, `screenshot` and `inspect` first use the exact
`/frame.rgb565` endpoint. A clip uses the same JPEG encoder and cannot repair that failure. The client saves
the actual `.png` path and reports `source: rgb565`, `lossless: true`; it never calls a JPEG capture lossless.
This was reproduced on an idle dashboard on 2026-11-02: raw transfer worked while both JPEG paths failed.
`tests/test_panel_remote.py` covers the scoped fallback, pixel colours and result metadata.

For other still-capture 503 errors (Spelas nu while the record turns), `screenshot` and `inspect` fall back to
one presented frame from `/clip`: JPEG quality, `"source": "clip"` and `"lossless": false` in the result.
2026-10-31 that fallback did not work on Spelas nu with the record turning: `inspect` and `screenshot` ended with
`Panel returned HTTP 503`, and `clip --seconds 1` returned `"frames": 0, "grabbed": 0`. Not diagnosed; until it
is, use a fresh lossless capture when it works, otherwise judge layout from `tools/panel ui` (widget coordinates).

## Reuse one session

`tools/panel session` accepts JSONL on stdin and flushes one JSON result per command on stdout.
The client reads its credential and resolves IPv4 once, then reuses its HTTP connection.
An agent can send one operation, read its result/image, and choose its next operation. For
a known scripted flow, batch operations with page guards:

```sh
tools/panel session <<'JSONL'
{"id":1,"op":"inspect","expect_page":"page_glass_v2"}
{"id":2,"op":"tap-text","text":"Hemmet","expect_page":"page_glass_v2"}
{"id":3,"op":"inspect","expect_page":"page_hemmet"}
{"id":4,"op":"tap-text","text":"Gå hemifrån","expect_page":"page_hemmet"}
JSONL
```

The last operation returns to the dashboard tab; it does not invoke a leaving-home action.
Supported operations are `status`, `ui`, `inspect` (`out`, `lossless`), `screenshot` (`out`,
`lossless`), `tap` (`x`, `y`, optional `duration`), `tap-text` (`text`) and `swipe` (`x1`, `y1`,
`x2`, `y2`, optional `duration`). `id` and `expect_page` are optional for every operation.
An input response includes the resulting page/status. Snapshot files are mode 600 and
directories mode 700. An existing nonempty snapshot directory is never overwritten.

The first error stops the session with exit code 1; later queued operations are not run.
Errors include the HTTP status/type when applicable and `input_may_have_run`. Input is
never resent after an ambiguous disconnect. A read can safely reconnect once if the
keep-alive socket has expired. Image reads can retry the capture rate limit.

For a Python script, reuse the same implementation directly:

```python
from panel_remote import PanelClient  # tools/ on sys.path, or a script saved alongside it

with PanelClient(persistent=True) as panel:
    before = panel.inspect()
    panel.tap_text("Hemmet")
    after = panel.inspect()
```

Do not leave a daemon, stream or poller running after the task. Restore settings changed
by tests. Preserve Joel's selected day volume; do not force 20% for ordinary tests.
Voice/model calls are separate from UI verification.

The common entry point was verified from `/tmp`, outside the repository. In one live
comparison, `status → ui → status` took 1,639 ms as three CLI processes and 539 ms in one
session (3.04× faster). This measures orchestration overhead, not panel rendering speed.
A five-command session with two complete inspections, a Hemmet tab click and dashboard
restoration passed in 4.14 seconds. The shared skill passed validation and its Claude Code,
Codex and Gemini links were verified against the canonical `agent-skills/hallpanel` folder.
245 free local tests and the physical smoke test passed after this harness integration.

Read the image with the agent's local image viewer. `ui` returns visible labels, clickable
objects, screen coordinates and slider values. Match those against the screenshot before
input. `tap-text` requires an unambiguous visible label inside a clickable object. Coordinates
use the displayed image: origin at the upper-left, x=0–1023 and y=0–599. Hardware touch's
mirroring is already accounted for; do not mirror virtual coordinates again.
Inställningar opens from the cog, the last entry in the tab bar (`tap-id top/0/1/5`); the round button
right of the bar (`top/0/2`) is the microphone, so never take it for the cog. Its sheet is `top/1`.

Set `--url http://<panel-address>:8080` before the subcommand if mDNS is unavailable.
The CLI reads its dedicated token from the ignored, mode-600 `styles/glass-v2/secrets.yaml`.
Do not print that file, put the credential in a command line, or reuse the HA API/OTA key.
For a fresh setup add a random 32-byte hex `panel_debug_token` to that private secrets file
before compiling `styles/glass-v2/panel.yaml`. Without a token the standalone board does not
start the interface. No router/firewall port is opened and no cloud/MCP service is needed.

## Capture and input semantics

The driver remembers the last submitted hardware framebuffer. A screenshot job runs on
ESPHome's main task, waits for its frame-completion acknowledgement, then copies the stable
1024×600 RGB565 buffer. LVGL cannot render concurrently with this copy. It includes the
active page, top layer and system layer, including the animation and shared tab bar.
It does not re-render a separate snapshot or read ESPHome's auxiliary half-screen buffer.

The HTTP task performs JPEG encoding with the ESP32-P4 hardware encoder. Capture/encoding
buffers are allocated for the request and released after the response. There is no continuous
stream or permanent screenshot buffer. Capture requests are capped at two per second. The
lossless route transfers the exact little-endian RGB565 frame and the CLI saves it as PNG.
JPEG is quality 90, with YUV444 chroma sampling for text clarity.

Virtual taps and drags use a separate event-driven LVGL pointer. LVGL performs hit testing,
press, drag, release, click and gesture processing. This exercises the actual widget callbacks,
including brightness preview while dragging. A real finger has priority and aborts a virtual
gesture. Timed-out HTTP requests retain shared ownership of their jobs until the main task
finishes or cancels them; timeout cannot free a buffer still in use.

Virtual taps do not count as touch in `status`: `physical_touch_idle_ms` counts only a finger. To tell
whether Joel or a test pressed something, compare it with `uptime_ms` and the moment of the change
(HA's logbook gives the second). On 2026-10-27 music paused 24 s after a flash's restart, mid-gates;
`physical_touch_idle_ms` 243 s at `uptime_ms` 267 s showed a finger, not a gate.

This observes the firmware's presented pixels. It cannot measure physical backlight output,
speaker sound, LCD damage or optical flicker. A screenshot cannot prove those are correct.

## HTTP interface

Every route requires a signed request; the dedicated token itself never crosses the network (since
2026-10-27; before that a plain `Bearer` token went over Wi-Fi in every request, so anyone who knew the Wi-Fi
password and captured one request could control the panel). The client sends
`Authorization: HPv1 ts=<unix ms>,nonce=<16 hex>,sig=<64 hex>`, where `sig` is HMAC-SHA256 with the token over
`HPv1`, method, path, `ts`, nonce, the `X-Panel-Lease` value and the body, joined by newlines
(`components/panel_debug/protocol.h`, `tools/panel_remote.py` `sign`). The panel accepts a timestamp within two
minutes of its SNTP clock and each nonce once; until SNTP has set the clock after boot it answers `503`
`clock not synchronized`. Refusals carry `"auth":"hpv1"`. A captured request cannot be replayed or altered,
but responses (screenshots, widget text) are still unencrypted on the LAN.

Every leased request (`/ui`, `/tap`, …) holds the device lease for 300 s. A bare `PanelClient().ui()` in a one-off
script never releases it, so for five minutes every other client gets `409 Conflict` (2026-11-03: three such
probes kept `visual capture` waiting). Use `with PanelClient() as client:` or `tools/panel session`; a refused
first acquire is retried on the next call (`test_refused_lease_acquire_is_retried_not_renewed`).

Clients fall back to the old `Bearer` header only when a panel refuses a signature without that marker (older
firmware) and no panel at that address has ever accepted a signature. The first signed success pins the address
in `.panel-ci/panel-auth.json` (`HALLPANEL_AUTH_PIN` overrides the path); after that the token is never sent,
even to something that answers like old firmware. A deliberate downgrade to pre-2026-10-27 firmware needs
`HALLPANEL_LEGACY_AUTH=1` or removing the pin. One client follows a flash in either direction once per request.
The scheduled jobs on Jarvis run their own copy of these tools: install it with
`sh tools/install_jarvis_harness.sh` before flashing firmware that changes this protocol.
`tests/test_panel_remote.py` checks Python-made signatures in the C++ verifier under ASan/UBSan, replay, every
signed field, the pin and the fallback rules.

Every secret in `styles/glass-v2/secrets.yaml` (Wi-Fi, API key, OTA password, this token) is compiled into the
firmware image in plaintext, and ESPHome's OTA does not encrypt the image: each Wi-Fi flash carries them over
the air, so prefer USB when the cable is in. Rotating them (done 2026-10-26, all but Wi-Fi):

1. Flash the code change first with `sh tools/flash_panel.sh /dev/cu.usbmodemNNN`; its pre-upload lease uses
   the current token, so a rotation cannot go through it.
2. Rewrite the values with ruamel and `yaml.width = 4096` (a wrapped 64-character value is a second line that
   a masked listing will print), mode 600. Never display the file, even masked line by line.
3. Under `.venv/bin/python tools/panel_lock.py run --lock workspace`: `tools/check.py`, `esphome compile panel.yaml`,
   `esphome upload panel.yaml --device /dev/cu.usbmodemNNN`, then `tools/panel gates --url http://<panel IP>:8080`
   (`hallpanel.local` may not resolve right after the restart; `tools/panel gates --resolve-url` before the
   upload prints the address). A USB upload kept
   the saved settings (day volume 0.65, brightness, night times) on 2026-10-26.
4. Jarvis: write the new token into the current harness release's `styles/glass-v2/secrets.yaml` over SSH
   stdin and delete it from older releases.
5. Home Assistant starts an ESPHome `reauth` flow on the new API key: find it with websocket
   `config_entries/flow/progress` (run inside the HA container, which has aiohttp) and POST
   `{"noise_psk": <key>}` to `/api/config/config_entries/flow/<flow_id>` on Jarvis. Expect `reauth_successful`.
6. Check: old token signed → 401, new → 200; HA entities available; Jarvis health job reads the panel.

Responses use `Cache-Control:
no-store`; the CLI does not follow redirects or use environment HTTP proxies. Captures are
saved with mode 600 because the display can contain private calendar or reply text.

| Method | Route | Result |
| --- | --- | --- |
| GET | `/status` | Page, uptime, memory and capture count |
| GET | `/ui` | Visible widget tree, coordinates, label text and slider values |
| GET | `/screenshot` | Hardware-encoded JPEG |
| GET | `/frame.rgb565` | Exact 1,228,800-byte little-endian RGB565 frame |
| POST | `/tap` | Form fields `x`, `y`, `duration_ms` (40–500 ms) |
| POST | `/swipe` | Form fields `x1`, `y1`, `x2`, `y2`, `duration_ms` (100–2000 ms) |
| GET | `/logs` | Log ring of this boot and the previous one (no lease) |
| GET | `/crash` | Reset reason, ESPHome's crash record, the abort text, the previous boot's last 60 lines (no lease) |
| POST | `/tasks` | `window_ms` 500–5000: CPU share per task and core, stack margin, heap (no lease) |
| POST | `/clip` | `duration_ms` 100–3000, `x`, `y`, `w`, `h`, `scale` 1/2, `quality` 30–95: presented frames |
| POST | `/redraw` | `duration_ms` 200–10000: per 16 px cell, how many frames LVGL drew it |
| GET | `/touches` | The physical touchscreen's last 2048 reads while pressed, and each release |
| POST | `/replay` | Body `t,x,y,p;…` (ms from start, ≤ 30 s, starts pressed, ends released) |
| GET/POST | `/settings` | Read, or set `key`+`value`: `day_volume`, `brightness`, `night_light` (%), `night_start`, `night_end` (HH:MM) |

Capture headers include `X-Width`, `X-Height`, `X-Frame-Sequence` and `X-Capture-Copy-Us`.
Unknown/duplicate form fields, out-of-range coordinates and invalid durations are rejected.
Input requests are never automatically retried: a timeout does not prove no touch occurred.

## Diagnostics

`/status` exposes `mic_enabled` (selected switch state) separately from `wake_running` (ESPHome's local
`MicroWakeWord::is_running()` flag, including starting/stopping). A selected-on indicator alone does not prove
the listener restarted. `tools/panel logs` includes `mic_state` changes with enabled/wake/assistant flags,
sampled once a second and logged only when they change; no microphone audio is recorded. See
[the mic switch lifecycle](voice-plan.md) and `tests/test_mic_resume.py`. A stable running flag is not an
acoustic wake-word recognition test.

Added 2026-10-29 (`components/panel_debug/diagnostics.cpp`, `clip.cpp`; host side `tools/panel_diag.py`; free
checks in `tests/test_panel_diagnostics.py`). Every command writes private files only.

- `tools/panel logs [--previous]`: every ESPHome log line (INFO and up, as configured) with uptime and wall clock,
  in a 16 kB ring in LP RAM (`RTC_NOINIT`), about 150–250 lines. The ring survives a software restart, an OTA
  restart, a panic and a watchdog, not a power cut. At boot the previous ring is copied to PSRAM, so `--previous`
  shows how the last boot ended without USB or Home Assistant. Lines are cleaned (colour codes, control bytes,
  broken UTF-8); `dropped_lines` counts lines lost when two tasks logged at once.
- `tools/panel crash-report [--elf ELF]`: reset reason, ESPHome's own crash record (pc, backtrace, both cores),
  the text `panic_abort` got (`assert failed: …`, which the network log never shows; kept by a linker wrap,
  `-Wl,--wrap=panic_abort`) and the previous boot's last 60 lines. Addresses are decoded with addr2line only when
  the local ELF is the build that runs; `tools/flash_panel.sh` keeps each build's ELF in `.panel-ci/elf/<build
  id>.elf` (newest 12) and crash-report uses the running build's, so a rolled-back build's crash decodes too.
  `tools/panel logs --older` keeps the boot before the previous one, so the first crash of a chain survives. This is no full core dump: the flash has no coredump partition, and the
  partition table can only change over USB.
- `tools/panel tasks [--seconds 2]`: a ~1 kHz hardware timer samples which task each core runs, only while
  measuring. Shares are statistical (±1 sample/ms) and an interrupt counts as the task it interrupted. Stack
  margin is FreeRTOS's high-water mark in bytes (`CONFIG_FREERTOS_USE_TRACE_FACILITY`, run-time stats stay off).
  Heap: internal, DMA and PSRAM free, minimum and largest block. Read the margin against the stack's size:
  IDF's `ipc0`/`ipc1` have 1024 B stacks (`CONFIG_ESP_IPC_TASK_STACK_SIZE`), so ~570/710 B free is 44 %/30 %
  used, normal (checked 2026-10-29), not a near-overflow.
- `tools/panel clip --out DIR [--seconds 2] [--region X Y W H] [--scale 1|2]`: every newly presented frame of
  the region, copied by the PPA at LVGL's refresh start (before it draws over the presented buffer) into one of
  four slots and JPEG-encoded on the HTTP task, then sent at once. The folder holds the JPEGs, `clip.json`
  (submission sequence, presentation time from the frame interrupt, grab cost per frame) and `clip.mp4` with the
  real frame timing. `missed_presentations` counts frames presented between two grabs; `busy_drops` frames
  skipped because the encoder was behind. It shows rendered pixels, not panel flicker or optical latency, and
  it costs the panel memory bandwidth while it runs. Measured 2026-10-29 on the 60 fps orb test: the whole
  screen at scale 2 took ~12 ms per frame (the PPA reads all 1.2 MB of PSRAM; asynchronous or not, the next
  refresh waits for it) and halved the rate to 30 fps; the orb's region `--region 320 112 384 368` at scale 2
  took ~2.5 ms and kept every 16.6 ms frame (121 in 2 s, none missed). The result's `observer_note` says when
  a clip slowed the panel. `--test-mode MODE` (clip and redraw) starts a synthetic state in the same lease;
  a separate `tools/panel test-mode` call is restored as soon as it exits. The PPA output must sit on whole L1/L2 cache lines (the first build sent none).
  Up to 4 MB, keeping 4 MB of PSRAM free.
- `tools/panel redraw --out DIR [--seconds 3]`: counts LVGL's joined invalid areas at each render start in a
  16 px grid and paints it over a lossless screenshot (`redraw.png`: blue drawn now and then, red every frame).
  It measures drawing work, not changed pixels, and draws nothing on the panel.
- `tools/panel touches [--out FILE] [--last-seconds 60]` and `tools/panel replay FILE [--since-ms --until-ms]`:
  the physical touchscreen's read callback is wrapped, so the panel keeps the last 2048 samples LVGL got while
  a finger was down (coordinates and time only). After "I swiped around, anything wrong?" run
  `tools/panel touches --summary --last-seconds 120`: gestures, every warning/error and each touch-to-frame
  latency the log shows in that window, in one answer. Replay starts at the first whole press in the window and plays
  the samples through the virtual pointer at their own pace; a real finger aborts it.
- Night times go as literal `HH:MM`: the panel's form parser does not percent-decode, so the client keeps `:`
  unencoded (`form_body`, tested).
- `tools/panel health [--json]`: one read-only pass over every sign worth watching, each with its limit:
  restart reason, crash record, smallest stack margin (≥ 256 B), internal/DMA/PSRAM lowest since boot
  (≥ 32/24/2048 kB) and the boot's low point, median render work (≤ 16 ms), main-loop stalls ≥ 150 ms after the
  first minute, Wi-Fi (≥ −70 dBm) and `audio_gap` lines. Exit 1 when one is over its limit. The limits are
  judgement from this panel's measurements (`HEALTH_LIMITS` in tools/panel_diag.py). Mac only on purpose:
  Joel wants no new Home Assistant or phone notifications from it (2026-10-29).
- `tools/panel memory`: internal, DMA and PSRAM free now, lowest since boot and largest block in kB, plus the
  boot's low point, in one answer.
- `tools/panel boot-heap [--timeline] [--restarts N]` (after the first 40 s) and `tools/panel restart`: a 5 ms sampler records the boot's
  internal and DMA-capable RAM low point, when it happened and a 50 ms timeline; `restart` is a clean software
  restart (like after an OTA) that waits for the new boot. `--timeline` prints the RAM every 200 ms beside the
  log's Wi-Fi, download, orb and sound events; `--restarts N` (≤ 5) restarts and lists each boot's low point. Every 5 s for the
  first minute, then every 10 min, the log also has an `Internal RAM:` line. On 2026-10-29 this found the boot
  crashes: each image download held up to a 64 kB TCP window of received data in the hosted SDIO's internal
  DMA buffers (low point 3.8-8.8 kB internal, 1.2 kB DMA, aborts in operator new and I2S). board.py now sets
  `CONFIG_LWIP_TCP_WND_DEFAULT` to 16 kB: three boots then bottomed at 115-119 kB internal, 101-105 kB DMA, and
  the images started ~0.2 s later (`tests/test_panel_diagnostics.py` keeps the window small).
- `tools/panel settings [KEY VALUE]`: Joel's saved levels as numbers. Setting one does what the slider does
  minus the chime; at night only the night level reaches the screen and the speaker keeps its night cap. Read
  the value first and restore it after a test (AGENTS.md).

## Free tests

`tools/panel visual check` compares the real UI against an explicitly recorded private reference.
It checks stable widget geometry and exact settings pixels, masks live values and produces
a local before/after/diff gallery. It also runs after checked firmware uploads when a reference
exists. Coverage, commands and reference rules are in [visual-regression.md](visual-regression.md).

Run the repeatable physical smoke test explicitly:

```sh
.venv/bin/python tools/panel_ui_test.py --live --brightness-drag --out /tmp/hallpanel-ui-check
```

This is a free device test. It checks denied authentication and invalid input, opens Hemmet,
inspects the settings overlay, captures JPEG and exact PNG, tests a real swipe, takes repeated
captures and checks that PSRAM returns to its prior level. `--brightness-drag` changes screen
brightness by seven percentage points and restores it. It restores the original dashboard,
Hemmet or Tavla page. Run while the panel is idle; the test rejects voice/night pages. A new
private output directory holds its images and JSON report. It does not start a voice/model call.

To see whether a panel control reaches Home Assistant, run `tools/panel ha-calls --seconds 20` and use the
control meanwhile: it prints each jarvis_agent/media_player/esphome action as it arrives (read only). Silence
means the panel did not send it or HA refused it before running (a response-only service does that).

To see the speaking wave the panel draws itself, run `tools/panel wave` (2026-10-28): the test mode
`speaking_live` shows the voice page with the real director and live-drawn wave, driven by a synthetic
voice instead of the microphone, with no sound and no model. It captures the line's box about twice a second
for 12 s (`--seconds`), restores the page and writes frames, `wave.gif`, `sheet.png` and `status.json`
(the worker's last render time and held wave frames) to a new private folder. `--measure` takes no captures
and times the drawn wave instead (see responsiveness.md). It cannot show how the wave follows Jarvis's real voice.

To inspect the listening orb without starting voice, use the existing Home Assistant action
`esphome.hallpanel_performance_test`. Its 30-second page tour shows the orb three times in
two-second windows. Poll `status` to find `page_orb` and take one image per window when
comparing animation frames. It restores the original view and stops if real voice starts.
From the Mac (2026-10-28), with Home Assistant's token file named in Jarvis's server.env:
`sh tools/jarvis_ssh.sh user@jarvis.local 'f=$(grep -h "^JARVIS_HOME_TOKEN_FILE=" ~/.config/hallpanel/server.env | cut -d= -f2-); f=${f/#\~/$HOME}; curl -s -X POST -H "Authorization: Bearer $(cat "$f")" -H "Content-Type: application/json" -d "{}" http://127.0.0.1:8123/api/services/esphome/hallpanel_performance_test'`, then loop `tools/panel status` and take
`tools/panel screenshot --lossless -o FILE` while `page` is `page_orb`. `tools/panel test-mode animation_60` does not show the orb page.
For one still of the orb page, `tools/panel clip --out NEW --seconds 1.5 --scale 1 --test-mode listening` is enough
(2026-10-30: one frame of the line orb and "Jag lyssnar…", no voice, the page restored afterwards).

The installed version's free host tests and physical verification are recorded in
[the device report](baseline/remote-ui-2026-10-24.md).

`tests/test_panel_remote.py` checks the native authentication/gesture parser under ASan/UBSan,
50,000 malformed requests, pixel order, private capture files, credential redirects, coordinate
bounds, label targeting and the rule against retrying input. Full release checks also validate
the generated board and retain its asynchronous double buffering.

On macOS the CLI resolves `.local` HTTP endpoints through IPv4, avoiding a measured five-second
wait for a missing IPv6 mDNS record. This keeps the Host header and still rejects redirects.

The approach was checked against [dcgrove's screenshot component](https://github.com/dcgrove/esphome-lvgl-screenshot),
[LVGL 9.5 snapshots](https://lvgl.io/docs/open/9.5/main-modules/draw/snapshot) and the
[ESP32-P4 JPEG API](https://docs.espressif.com/projects/esp-idf/en/v5.5.5/esp32p4/api-reference/peripherals/jpeg.html).
This implementation uses our actual asynchronous double-buffer backend and ESP-IDF JPEG
encoder rather than the older LVGL buffer API or a software RGB888 conversion.

## "No route to host" from tools/panel

When `tools/panel` says `[Errno 65] No route to host` but `curl -m 5 http://hallpanel.local:8080/status` answers (401 is fine), macOS Local Network privacy blocks every Python started by the app that runs the agent (seen 2026-11-01 under Cursor; curl, ping and ssh are exempt). Joel turns that app on in System Settings > Privacy & Security > Local Network; nothing in the repo can grant it. `panel_remote.local_network_hint` prints this hint (tests/test_panel_local_network.py).
