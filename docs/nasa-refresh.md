# Early NASA refresh and clean follow-up turns

Joel requested this on 25 October 2026: fetch APOD earlier, fix the missing Tavla
image, and remove the previous answer while Jarvis handles a follow-up.

## NASA scheduling

`hallpanel-apod.timer` attempts at minutes 10, 25, 40 and 55 in
`America/New_York`, with one-second scheduling accuracy. The first attempt of a
new New York day is 00:10. That is 05:10 in Stockholm during the October 2026 DST
transition gap, and normally 06:10. The timer follows the named time zone rather
than a fixed offset.

The service runs `apod.py --scheduled`. It requests the explicit New York date,
rejects an older returned date as not-yet-published, and retries on the next tick.
After publishing a valid current image, later ticks make no API request. A genuine
current-day video also completes the day's checks while retaining the last image.
An invalid/tiny image or HTTP failure remains pending. The private cache's
`poll.json` reserves each API attempt before HTTP and caps scheduled attempts at
40 per New York day, leaving headroom under the DEMO_KEY's nominal 50/day budget.
A new date resets that budget. Manual runs remain available for explicit repair
or republishing and do not silently inherit the scheduled completed-day skip.

NASA's publication metadata for October 22–24 showed 00:05 Eastern time; October
25 showed 04:19:48 Eastern (09:19:48 Stockholm). These metadata are evidence of
variable publication times, not a guaranteed first-availability timestamp.

Publishing goes through the existing selected-background logic, preserving a
chosen painting. The existing `bg_version` attribute tells the panel to refresh
after publication; its debounce is three seconds after that attribute arrives.
The old fixed 07:45 firmware fetch has been removed. Actual display time still
includes baking and the HA state update; no exact instantaneous-delivery claim.

## Tavla's Earth view and explanation

Joel asked for this on 29 October 2026: more NASA data on Tavla, the explanation in English, EPIC and asteroids.

- **Explanation.** `apod.py` keeps NASA's explanation in `apod.json` (`explanation`, plain text, cut after a whole
  sentence past 1200 characters). `/panel`'s `art_text` carries it only while NASA is the chosen background.
  A tap on Tavla's plaque opens it in Inter 500 16 (a plaque font with Latin-1/Extended-A); another tap closes it.
  A day published before this field existed gets it on the next manual `apod.py` run (one API request).
- **Earth.** `server/earth.py`, run by `hallpanel-earth.timer` every three hours (00/3:35 Stockholm), takes EPIC's
  newest day (no key), picks the picture whose centre is nearest 15° E (Europe), crops the disc to 480x480 and
  publishes `earth.qoi`/`earth.png` (RGB565-dithered by `panel_image.py`) and `earth.json` in HA's www/hallpanel.
  An unchanged picture is not fetched again. Log: `journalctl --user-unit hallpanel-earth`, one line per run.
- **Asteroids.** The same run asks NeoWs (api.nasa.gov, `NASA_API_KEY` or DEMO_KEY) once per Stockholm day, at most
  four tries. DEMO_KEY answered `x-ratelimit-limit: 10` an hour on 2026-10-29, per IP, shared with the Mac. The three
  closest passes go to `earth.json` as "name · N månavstånd · ~size" (mean of NASA's diameter range).
- **Panel.** `tavla_earth.py`: the **Jorden** button (hidden until `earth_version` is known) swaps the picture and
  plaque for the Earth, its plaque (date, EPIC time in Stockholm, distance) and the asteroid card. Leaving Tavla puts
  the picture back and closes the text. The Earth picture costs 0.46 MB PSRAM (10.7 MB free, lowest 7.1 MB before it).
  It is not in the boot chain: `earth_fetch` waits for the backgrounds, then 20 s, and downloads only when
  `earth_version` differs from what is loaded; the button fetches at once if it is not in yet. One download at a
  time (`earth_busy`); the panel logs `Earth picture: downloading` / `in`, and `Tavla: Jorden` / `dagens bild`.
- Free tests: `tests/test_tavla_earth.py` (fake EPIC/NeoWs, once-a-day and kept-on-failure, the panel fields, the
  page's tap layering) and `tests/test_panel_performance.py` (no Earth download at boot).

## Voice follow-ups

Every listening turn clears the voice page's old `reply_lbl` and `reply_text`,
hides old cards, and shows the listening orb. The same reset is used by the wake
word and by automatic follow-ups. New transcribed text can appear while the
answer area remains empty. A new response/progress message can then fill it.
Repeated delivery of an identical `last_card` attribute does not redraw an old
summary/card after this reset. The card timestamp remains part of that identity,
so a new turn can intentionally display otherwise identical content.

Conversation context on Jarvis is preserved. Clearing the visual reply does not
call the backend clear-history action.

## Verification

Free APOD tests cover stale dates, completed-day no-network behavior for an image
and video, the persisted retry budget, explicit-date list selection and the two
DST offsets. Existing tests still run real baking and image publication against a
fake NASA response. The actual Jarvis `systemd-analyze calendar` parser accepted
the schedule before deployment.

`tests/test_voice_turn_ui.py` runs the generated listening/STT actions and card
callback against real native LVGL with address/undefined-behavior sanitizers.
It starts with an old answer, clears it, replays the old HA card attribute, checks
that thinking stays empty, displays a new reply, and clears that again for a
second follow-up. It starts no microphone, speech service or model request.

The Tavla download-lifetime correction and its original failed physical evidence
are in [boot-image-socket-failure.md](boot-image-socket-failure.md). The expanded
firmware candidate needs a new source-bound recovery plan; the earlier plan's
artifact does not contain these NASA/voice changes. The existing quarantine and
approval requirement remain in force until the checked recovery completes.

## Live result before firmware recovery

The checked Jarvis deployment passed 542 local tests, 65 HA-runtime tests and
ESPHome configuration validation. The installed timer is active with the named
New York schedule, and its service runs `apod.py --scheduled`. The persisted poll
state records today's image as complete after one attempt. A second service run
left that state unchanged, confirming the completed-day no-request path live.
Home Assistant was not restarted.

A single bounded background-selection reload restored the missing Tavla image
on the currently installed firmware. NASA was restored as the selected background,
and a fresh lossless physical capture shows the picture and title. The visible
tree now contains `screen/0`; its reported type is `object`, so an initial helper
assertion requiring type `image` was too strict. Screenshot inspection confirms
the recovery. This ordinary refresh does not prove the boot fix, remove firmware
quarantine or install the clean-follow-up behavior. Evidence is retained privately
under `.panel-ci/three-fixes-20261025/`.

The authorized panel installation subsequently installed both fixes and passed all
14 visual scenes. It stopped at the unchanged PSRAM comparison before the later
responsiveness/60-fps gates. The next candidate compresses only the compiled boot
logo losslessly to recover resident memory; see the updated boot diagnosis.

## Final installed result

Firmware `5d99217bc684baeab586f9250b8a2132` is installed with the image-lifetime fix,
truthful readiness, clean follow-ups, losslessly compressed static logo and 8 KiB
image reads. It passed all 29 stages on two clean software boots. Every visual
scene, memory, responsiveness and the two 120-second production 60-fps soaks
passed. Both Tavla captures match the saved NASA picture exactly. Recovery finished
through the existing command and active quarantine is resolved. Detailed timings
and all earlier failed attempts remain in
[boot-experience.md](boot-experience.md) and
[boot-image-socket-failure.md](boot-image-socket-failure.md).

NASA's earlier schedule remains installed on Jarvis. The native LVGL follow-up
test verifies clearing old text/cards and suppressing repeated old cards across
two turns. No live paid speech/model test was started to validate this behavior.
