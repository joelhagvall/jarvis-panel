# Image and calendar colour

The current fast display path uses RGB565 throughout: online images, LVGL,
the two direct MIPI framebuffers and screenshot capture. Red and blue have
32 levels each; green has 64. Smooth dark gradients can therefore acquire
coloured contour bands. The 1024×600 resolution also limits fine detail.
The baked glass intentionally blurs and tints the background; Tavla uses
the image without that glass treatment.

This is a software format limit, not evidence of a faulty LCD. Waveshare's
[board support package](https://components.espressif.com/components/waveshare/esp32_p4_wifi6_touch_lcd_7b/versions/1.0.4/readme?language=en)
supports both RGB565 and RGB888. Switching this application to RGB888
requires coordinated changes to the direct buffers, LVGL, image decoder,
atlas composition and debug capture, followed by physical performance checks.
RGB888 buffers alone use 50% more memory/bandwidth than RGB565 at the same
dimensions and refresh rate. No such format switch is made here.

## Static image preparation

`server/panel_image.py` applies deterministic 8×8 ordered dithering on Jarvis
before publication. Output pixels already sit on the RGB565 grid, so the
panel does no extra image processing and allocates no extra framebuffers.
The fine fixed pattern reduces large colour bands; it cannot restore the
full source colour depth or resolution. It does not change live LVGL gradients
or previously quantized animation atlases.

All agents and humans can use the same entry point:

```sh
tools/panel image INPUT.png OUTPUT.png
.venv/bin/python -m unittest tests.test_panel_color
```

Verify a fresh, lossless dashboard capture against the exact published PNG
and its original, choosing a background-only rectangle from that capture:

```sh
tools/panel color-check --snapshot SNAPSHOT-DIR --source ORIGINAL.png \
  --prepared SERVED.png --roi x1,y1,x2,y2 --calendar panel.json --out report.json
```

`panel.json` is a private `/panel` response from Jarvis at capture time.
The optional calendar check samples the centre of each visible calendar bar
and compares it to the RGB565 representation of the supplied colour metadata.
It checks the current dashboard widget paths and stops if the layout changes.
The background check requires exact decoded pixels and mean error below one
RGB unit per channel after 8-pixel spatial averaging. It reports JSON with
exit 0 for pass and 1 for failure. It never operates the device or changes a
reference. This check is separate from generic CI visual comparisons, whose
live backgrounds are intentionally excluded from pixel goldens.

The CLI returns JSON and a normal process exit code. It processes local
files only and does not publish or operate the panel. Original assets stay
unchanged. `background.publish()` prepares dashboard, art and voice images,
including previously cached sources. Cache keys include source content and
the converter code. Up to 120 prepared images are retained. The publication
version changes so the panel fetches the new files. `server/stage.py` uses
the same converter for deployment defaults. No duplicate agent-specific path.

## Calendar palette

`server/calendar_palette.py` reads CalDAV's Apple `calendar-color` property,
using existing `~/.config/icloud-caldav/credentials.json` (or
`CALDAV_CREDENTIALS`). It discovers principal and calendar home with read-only
PROPFIND requests. Credentials are sent only to HTTPS iCloud hosts; redirects
are refused. Requests have 8-second timeouts and a 1 MiB response limit.

Refresh runs on its own daemon thread, once an hour after success and after
five minutes on failure. Panel requests and the alarm watcher never wait
for it. The last successful palette is retained in the private
`~/.local/share/hallpanel/calendar-palette.json` file, mode 600
(`JARVIS_CALENDAR_PALETTE` overrides the location). It contains names and
colours, no event details or credentials. Calendars and events are never edited.
Duplicate names with different colours are refused because agenda text
identifies calendars by name.

The server sends `title|HH:MM|calendar|#RRGGBB`. Firmware validates all six
hex digits. Missing or invalid metadata uses neutral grey, not a guessed
name-to-colour mapping. RGB565 still slightly quantizes the final displayed
colour. Firmware accepts old three-field messages; old firmware ignores the
fourth field until upgraded.

On 2026-10-24 the live iCloud palette was: Att göra `#34C759`, Jobb `#FFCC00`,
Annat `#8A928E`, UNI `#1399FC`, Träna `#882F00`, Firma `#CB30E0`
and Påminnelser ⚠️ `#B14BC9`. These are observations, not hardcoded settings.

## Verification limits

Compare the original served PNG with `tools/panel inspect --lossless`.
JPEG capture introduces separate compression artifacts. The framebuffer
establishes rendered pixels, not physical LCD calibration or viewing-angle
behaviour. Inspect before/after images and calendar bars on the actual
firmware; keep existing visual references and explain intentional differences.
Do not promote a failed candidate automatically.
