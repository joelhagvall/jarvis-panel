# Physical visual regression tests

`tools/panel visual check` navigates the real panel, captures exact RGB565 screenshots as PNG
and compares them against an explicitly recorded reference. It uses the existing authenticated
Wi-Fi interface. It does not flash firmware, call a model, speak, change brightness/volume,
toggle the microphone or choose another painting. It restores the initial dashboard, Hemmet
or Tavla view. Start with settings closed and voice/night mode idle. Policy v2 also
uses the bounded synthetic test callback for night, voice, OTA and fault feedback.
Those scenes require the new firmware protocol and a device lease.

## Commands

```sh
# One-time explicit reference creation from an inspected, known-good installed UI.
# Right after a flash, add --wait-ready 90: the panel counts as ready 65 s after boot, and an early record
# stopped with "Panel not ready" (2026-10-27). Music must be idle (no Spelas nu row).
tools/panel visual record --out .panel-visual/reference-moon-v9 --wait-ready 90

# Normal regression check. Prints the absolute path to its private HTML/JSON reports; a failure also prints
# `failed`: each failed scene's layout changes and changed pixels with their bounds.
tools/panel visual check
tools/panel visual check --out /tmp/panel-visual-run

# Save another physical capture, or compare saved captures without touching the device.
tools/panel visual capture --out /tmp/panel-candidate
tools/panel visual compare --candidate /tmp/panel-candidate --out /tmp/panel-diff

# Physical interactions, followed by bounded synthetic voice/fault/expiry checks.
tools/panel smoke --test-mode --out /tmp/panel-ui-run
```

`record`, `capture` and output folders must be new. A failed capture never creates
`reference.json`, and a check never changes the reference. Commands work from any directory
when invoked through the absolute `tools/panel` path. `--baseline /absolute/path` selects
another reference for `check`/`compare`; the default reference path is relative to the repo,
not the caller's current directory. Output defaults to a new private temporary directory.
`--url` or `HALLPANEL_DEBUG_URL` selects the local panel endpoint for live operations.

The checked `tools/flash_panel.sh` runs this test automatically after a successful upload if
the default reference exists. It waits up to 90 seconds for the boot cover to disappear and
the idle UI to be available. Visual failures exit nonzero. The uploaded firmware remains
installed for inspection; the test does not silently reflash or approve it. With no local
reference, installation explicitly reports `Visual regression NOT CHECKED`. An upload failure
stops before visual navigation.

## What is compared

The versioned [policy](../tests/visual/policy.json) declares 14 scenes:

| Scene | Layout checks | Pixel checks |
| --- | --- | --- |
| Dashboard | Card bounds, navigation controls, static labels | Captured for inspection; live artwork/data excluded |
| Settings over dashboard | Stable settings controls and labels | 201,355 pixels inside the rounded sheet |
| Hemmet | Card bounds, navigation controls, static labels | Captured for inspection; live artwork/data excluded |
| Settings over Hemmet | Stable settings controls and labels | 201,355 pixels inside the rounded sheet |
| Tavla | Image and full-screen return control bounds | Daily artwork is captured but excluded |
| Synthetic night | Page and full-screen return control bounds | Black background, live clock/reminder region masked |
| Synthetic listening, thinking, speaking | Page, visible orb, question/reply geometry and known sample text | Frozen first animation frame and static sample text on a controlled black backdrop |
| Synthetic OTA | Arc, page bounds and fixed labels | Static update screen on a controlled black backdrop |
| Synthetic Wi-Fi and HA disconnect | Page and expected offline feedback label | Daily artwork/data excluded |
| Synthetic API timeout | Voice geometry and expected error label | Frozen orb and known text on a controlled black backdrop |
| Synthetic missing image | Page and return control bounds | Black image area, live painting plaque masked |

Layout comparisons include widget type, position, dimensions, clickability, static text and
slider ranges. Live numeric values are excluded. Missing, shifted or clipped controls and
missing static labels fail. Explicit anchors also fail before recording if they are empty,
outside the 1024 × 600 framebuffer or clipped. UI dumps must be complete and belong to
the expected page.

The pixel mask excludes surrounding artwork, live percentages, slider fills/knobs, microphone
state/description and painting-selector text. The slider/control geometry is still checked.
Static icons, label pixels, colors and the settings background are compared. Differences
larger than eight levels in any RGB channel count as changed pixels; more than eight changed
pixels fail a scene. This allows one native RGB565 quantization step without accepting a missing
glyph. Pixel-tested scenes wait for a 250 ms quiet interval and are captured twice,
150 ms apart, to reject unstable references/candidates. The device's capture rate limiter
can extend that interval. Synthetic orb scenes stop the animation at its actual first
frame; the separate `animation` mode restarts the real orb loop for performance measurement.

Daily paintings, clock ticks, calendar, weather, train departures and home sensor updates do
not change the selected contract. Dashboard/Hemmet and daily Tavla artwork still have layout
checks, rather than full-frame pixel comparison. Dynamic text wrapping and the transient boot
overlay remain outside the golden comparison. Night mode here exercises its rendered view,
without changing the backlight, night settings or volume. Synthetic voice/fault modes exercise
the real LVGL objects with known values. They do not test wake-word inference, audio recording,
speaker output, actual Wi-Fi/HA transport loss, download error handling or real API timeouts.
No alarm state or hardware connection is changed. The hard device timer and lease release
restore modified visibility, sample text and the original page; a real voice/alarm interaction
takes precedence. The smoke suite also checks that a 1 s test expires without a restore call.

Framebuffer tests cannot detect optical LCD flicker, physical backlight output, sound or
touch-to-photon latency. Actual audio checks need a separate acoustic verification method.
The normal `performance_test` and bounded `animation` workload remain separate measurements.
The synthetic callback's C++ behavior is compiled and tested on the host with address and
undefined-behavior sanitizers. Host tests do not establish that a scene passed on hardware.

## Reports and failure handling

The command returns 0 on success and 1 on a regression or incomplete/error run. It writes:

- `report.html`: a local gallery with reference, current capture and magenta pixel differences.
- `report.json`: layout changes with old/new fields, pixel counts and changed-region bounds.
- `<scene>-diff.png`: changed pixels in magenta, checked current pixels dimmed, excluded pixels black.
- Per-scene PNG, UI tree and status snapshots for diagnosis.
- `error.json` when capture/validation fails after the output folder was created. Reference
  preflight failures print an error before device access or output-folder creation.

Review the report and actual images before changing code or accepting a new reference. A policy
hash mismatch or missing scene fails rather than silently masking more pixels. Contracts are
recomputed from the saved UI tree when comparing; an empty saved contract cannot produce a pass.
Synthetic reply text uses its fixed 30 px font even if a previous real reply selected a smaller fitted font.
The fixture restores the prior font only while it still owns both the text and injected font; real voice
preemption keeps its own font. `tests/test_panel_test_mode.py` exercises both cases without a model.

Incoming voice/night transitions stop the run instead of being used as a reference. If a real
interaction takes over, the runner leaves that view alone.

For an intentional design change, first inspect the reported differences and verify the new
layout. Record into a new directory, retain the old reference directory, then explicitly move
the reviewed new directory into `.panel-visual/reference/`. Never update the reference just
because a check is red. Policy changes also require a reviewed fresh reference.

References, screenshots and reports can contain private home/calendar data. `.panel-visual/`
is gitignored. Artifact folders are mode 700 and files mode 600. Keep them local; scripts and
the policy are the shared part of the harness. A fresh checkout needs a local reference.

## Verification on 2026-10-24

The first **v1** reference was captured from the installed opaque settings build. A second live run
passed all four scenes, with zero changed pixels in both settings views. The previous real
screenshot with text showing through the settings sheet was then substituted into a saved
candidate: the comparator failed with 4,684 changed pixels in `settings_dashboard`. The current
firmware and reference were not changed by that negative test.

15 free host tests cover removed/shifted text, transparency/color regressions, live-value masks,
missing/moved controls, slider ranges, malformed/stale references, output privacy, restoration
after a failed capture, idle checks and the post-flash gate's success/failure control flow.
At that earlier verification, all 260 local release tests and ESPHome configuration validation passed. No firmware upload or
paid model call was needed for this host-side harness change. Sanitized results are saved in
[the verification record](baseline/logs/2026-10-24_visual-regression.json).

Policy v2 deliberately invalidates that four-scene reference. Preserve it and record a reviewed
new reference into a new private directory after the new firmware is installed. CI stops on a
stale/missing reference. It never creates or promotes golden images automatically. Current
physical results belong in each CI run's private report; the historical v1 result above is
not evidence for the added scenes.

Policy v3 corrects a live calendar heading previously labeled static. The server
deliberately emits `IDAG` or `I MORGON`; midnight exposed the wrong assumption.
Only that declared label's text/content width varies, with three allowed values since 2026-11-02 (`KOMMANDE`
too: the server now emits it when today and tomorrow are empty, `home_status.calendar_later`; a new heading
without a policy value is caught before the flash by `tests/test_panel_visual.py` `CalendarHeadingPolicyTest`) and
maximum width 198 px. Its x/y/height/type/clickability/bounds and all other anchors
remain exact. Pixel regions, tolerances and changed-pixel limits are unchanged.

The default private reference is `.panel-visual/reference-panel-v19/` (2026-11-03, morning), recorded after the
memory flash (docs/memory.md). Against v18 the check stopped at `Clipped or empty layout anchor screen/4` in `night`:
since v17 the policy still anchored Natt's `screen/4`, which after `n_wake` is the reminder line (`n_rem`), not the
full-screen tap; the laundry reminder was cancelled at 22:05, so the line was empty (width 0). The anchor is now
`screen/5`, and `AnchorsNeverHideTest` fails any `screen/N` anchor that is a label not declared in `dynamic_text`.
Hemmet differed on layout alone, the known gap below (the cards back at `screen/4..7`). Every other difference was
live data (music stopped, the reminder gone, clock, weather, calendar); the capture was looked at before recording.

Before it, `.panel-visual/reference-panel-v18/` (2026-11-02, late evening): the calendar
card's heading may say `KOMMANDE` (the week's next events when today and tomorrow are empty), so the policy's
`screen/5/0` values gained it and the policy digest changed; the Jarvis card lost its small mic and "Säg hey Jarvis"
line, the SL card got a hidden disruption row (`sl_note_row`) at the end of its children, and Hemmet got the house
line (`h_house`) right after `h_status`, which moved every later Hemmet widget one index. Against v17 the check
stopped at `Invalid dynamic label screen/5/0` (the live heading was KOMMANDE). Dashboard and Hemmet were looked at
on the panel before the reference was recorded; music was playing, outside every anchor.
Known gap until the next flash (2026-11-02, late): in v18 the house line sat at Hemmet's `screen/4`, in front of the
four cards the policy anchors as `screen/4..7`, so the anchors pointed one widget off and Hemmet's check fails with
`Missing widget screen/4` whenever the line is hidden (almost always: it only shows a provider outage over home).
The source now puts `h_house` last on the page (`gen.py`, guarded by `AnchorsNeverHideTest` in
`tests/test_panel_visual.py`); it is not flashed on its own because Joel cannot see it. At the next flash Hemmet's
check against v18 fails on layout alone (the cards back at `screen/4..7`): inspect and record v19 then.

Before it, `.panel-visual/reference-panel-v17/` (2026-11-02, evening): Natt got the next
wakeup ("Väckning 08:45", `n_wake`), Hemmet the quick timers and the Jarvis card Härnäst. Against v16 the check
failed only in `night`, on layout alone: `n_wake` came before the full-screen tap, so the tap moved from `screen/4`
to `screen/5` and the reminder took its old index. Everything else that differs between the two folders' pixels is
live data (clock, weather, departures, the red mic while the microphone was off), outside every scene's anchors.
The capture was looked at before the reference was recorded. Night's wakeup line is live text and is not anchored.

Before it, `.panel-visual/reference-reply-roll-v16/` (2026-11-01, evening): the voice page's
reply sits in the clipped `reply_box` so a long reply rolls with the voice, and your sentence has two lines
(`jarvis_orb.py`, `reply_fit.h`). Against v15 the check failed only in the four voice scenes, on layout alone:
`you_lbl` 29->58 px high, `screen/7` a 820x194 box at the reply's old place with the label inside it at the same
x/y; 0 changed pixels in every scene. The capture was looked at before the reference was recorded.

Before it, `.panel-visual/reference-koppla-om-v15/` (2026-11-01, afternoon): Inställningar's
Ljud ut row got the round Koppla om button left of its list (`gen.py` `reconnect_button`, docs/bluetooth-speaker.md).
Against v14 the check failed only in `settings_dashboard` and `settings_hemmet`: the note 300->264 px wide, the new
button at `top/1/0/6/3` (x 537, 40 px) with the list one index later, and 1 272 changed pixels inside the button
(x 537-577, y 437-477). The capture was looked at before the reference was recorded.

Before it, `.panel-visual/reference-ai-v14/` (2026-11-01): recorded from the installed
build after the Jarvis card got an invisible tap area on its heading (it opens the AI page, `ai_page.py`). Checked
against v13 first: the only difference was `screen/7/1/0 'Rensa' -> screen/7/2/0`, the button's label one index
later because the tap area comes before it; 0 changed pixels in every scene.
Put a new widget after its existing siblings when the order does not matter: the tree paths of everything after it
shift and the gate fails on an unchanged screen (this one did).

Before it, `.panel-visual/reference-tavla-nasa-v13/` (2026-10-30): v12's pixels and UI,
rebound unchanged to policy v6. Policy v6 adds `dynamic_width` for Tavla's plaque (`screen/2` in `tavla` and
`missing_image`): it is as wide as the day's NASA title, so after a new picture the gate failed with
`screen/2 width 231->292` on an unchanged layout. Its x, y, height, type and clickability stay exact, and it may be
at most 968 px wide.

Before it, `.panel-visual/reference-tavla-nasa-v12/` (2026-10-29, late afternoon): Tavla got
the Jorden button (top right) and NASA's explanation and Earth view (`tavla_earth.py`), and the page's back tap now
lies under the plaque, so `screen/1` and `screen/2` swapped. Against v11 the check failed only on Tavla and
missing_image: that layout swap, and 309 changed pixels inside the button's label (x 929-980, y 41-53). Between the
references the bar band differs only in the plaque's credit, now "… · NASA APOD" (live data); the rest is live data.

Before it, `.panel-visual/reference-larm-v11/` (2026-10-29, afternoon): Hemmet's Larm card
gives its six lines a fixed width of 184 px with `...` (`gen.DOT_TEXT_W`), so a long door line stops at the card's
right padding instead of its edge. Against v10 the check failed only on Hemmet, with two layout changes: the
static labels Brandvarnaren and Vattenlarmet went from 92 and 79 px wide to 184 px. Every pixel-compared scene had
0 changed pixels. `visual-diff` between the references differs only in live data: today's NASA background behind
the glass scenes and the clock in Natt. Recorded with music idle.

Before it, `.panel-visual/reference-moon-v10/` (2026-10-29): v9's reference PNGs are byte-for-byte
unchanged. At night SL's right-aligned status changes from `3 min att gå` (80 px) to `Inga avgångar` (96 px).
Policy v5 validates only those two values, height/y/type/clickability, bounds, maximum width 120 px and the exact
right edge (486 px). A missing label, unknown text or a one-pixel anchor shift still fails. This is independent
of the unchanged pixel limits. `tools/panel visual rebind --source OLD_REFERENCE --out NEW_REFERENCE` explicitly
revalidates the old UI against a reviewed selector policy and copies its PNGs unchanged; `rebind.json` retains
all PNG hashes and the source, and tests verify the old reference is not touched. Never rebind an unreviewed
UI change or replace expected pixels with a failed candidate.

Before it, `.panel-visual/reference-moon-v9/` (2026-10-28, late evening): Hemmet's card
"Tvätt och sol" became "Tvätt, sol och måne" with a third row, Månen (`home_status.moon_line`), and Dagsljus moved
10 px up; the policy's Hemmet labels follow. Against v8 (`tools/panel visual-diff OLD NEW`) no scene changed in the
bar band; Hemmet differed only in its two right-hand cards (the moon row, and live air and door values), the
other scenes in live data (clock, calendar, weather). Recorded with `hallpanel-soloist` stopped, as below.

Before it, `.panel-visual/reference-spelas-nu-v8/` (2026-10-28, evening): the tab bar got a
fifth tab, Spelas nu, between Hemmet and Tavla, with widths from the labels in the same baked 448 px
(`gen.TAB_WIDTHS`), so the policy's bar anchors gained `top/0/1/5` and the label "Spelas nu". Against v7 (`tools/panel visual-diff OLD NEW`) only the bar
band (y 505-590) changed structurally on the dashboard, Hemmet and Inställningar; the rest was live data (clock,
weather, calendar, SL), and Tavla and the voice scenes were identical. The tools treat `page_musik` as an idle page,
and leaving Tavla to the right now passes Spelas nu (`panel_ui_test.bar_page`); the first recording stopped there
as "busy".

Before it, `.panel-visual/reference-jarvis-line-v7-hemmet/` (2026-10-28): Hemmet lost its
summary line under the status (it repeated the cards) and got the Spelas nu row there instead. Its card anchors in
`tests/visual/policy.json` moved from screen/5-8 to screen/4-7, since paths count the page's children; only
Hemmet's summary band (y 188-214) changed, other differences were live data inside the masks. Record with music
idle: Spotify's paused row still shows, so stop `hallpanel-soloist` first and start it again afterwards.

Before it, `.panel-visual/reference-jarvis-line-v6/` (2026-10-28): the voice page's orb became
the Jarvis line (atlas v8). Against v5 only the four voice scenes changed, every one inside the orb's box
(x 422-602, y 115-295) with no layout change; all other scenes passed unchanged.

Before it, `.panel-visual/reference-ljud-ut-v5/` (2026-10-27). Inställningar got a Ljud ut
row, Bakgrund and Ljud ut became lists, and the rows moved 7-47 px up (10 px between rows), so it was recorded fresh
after that firmware was installed. Every other scene matched v4: identical contracts, or 0 changed masked pixels.
The lists' frames are pinned; only their text is masked. Music must be idle when recording: the Spelas nu row
belongs to no reference, and the first attempt, with radio on the Bluetooth speaker, stopped with HTTP 409.

The previous reference, `.panel-visual/reference-calendar-v4/`, is an
explicit derivation from the original `.panel-visual/reference/`, which remains
unchanged. Every PNG and source UI file is byte-identical to the original. Its
manifest records provenance and recomputes the declared layout contracts. This
corrects the live-data assumption without recording replacement pixels. The real
midnight failure and earlier CI reports remain archived. Missing references stop
the checked flash script before upload.

Policy v4 also corrects the settings pixel profile to exclude one pixel of outer
antialiasing. The modal's rounded fringe blends live background text; 11 changed
edge pixels exposed this at midnight. The opaque interior [193,52,831,500], radius
27, retains 199219 checked pixels per settings sheet, versus 201355. The original
v2 and derived v3 references remain unchanged. V4 again derives their byte-identical
original PNG/UI files and records provenance. All static text/controls, layout
checks, dynamic exclusions, tolerance 8 and maximum 8 changed pixels remain.
The regression suite checks changes just inside that opaque profile still fail.
