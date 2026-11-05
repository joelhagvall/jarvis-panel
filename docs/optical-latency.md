# External contact-to-visible-response measurement

Status: method and analyser prepared; no optical measurement has been made. Joel has no external
measurement equipment available for this task. Software `/status`, screen captures and `/clip` are
complementary diagnostics and cannot supply finger-to-photon latency.

Use an original external high-speed recording with finger/contact and the responding screen area visible
in the same frame. Confirm the actual capture rate from the camera settings/original metadata. A 240 fps
capture samples every 4.167 ms; a slow-motion file playing at 30 fps is not thereby a 30 fps capture.
Do not analyse a retimed, interpolated, stabilised, frame-dropped or screen-recorded copy as physical evidence.

An iPhone slow-motion clip saved to Files or iCloud Drive is a 30 fps container with normal-speed ramps at both
ends; its metadata does not state the capture rate. Find the rate from the panel itself: the turntable redraws at
60 Hz, so in the slow part the record changes every second frame at 120 fps capture (every fourth at 240 fps).
The first recording (2026-11-02, `slowmo-hallpanel.MOV`, iPhone 15 Pro) was 120 fps, a 4x slowdown, with a repeated
frame every 7-9 frames where the camera dropped one. The panel clock then dated the end against `tools/panel logs`.
Its two bracketed tab releases took 17-75 and 8-100 ms to the new page, while the finger rested on the tab
0.4-0.6 s before release with no visible press feedback; tabs now light up on press (gen.py `tabs`).

Place the camera steadily, close enough to see contact, with sufficient light to avoid long exposures.
Keep the contact and responding area near the same image row to reduce rolling-shutter skew. Do not claim
that this calibrates the remaining skew or exposure. Visible finger contact only estimates physical contact;
exact capacitive triggering would need a synchronised electrical/contact trigger and photodiode.

Record at least 30 taps per condition/action. Keep all taps, including misses and long waits. Separate
press feedback from release-triggered page changes: for the latter, annotate the release boundary as the
contact bracket and name the action `release_to_page`. Do not mix press and release populations.
Start with ordinary idle tabs. A later loaded recording can use real playback/normal updates with settings
saved and preserved. The synthetic remote-test lease gives a real finger priority and can stop its load;
do not call such an interrupted synthetic recording a loaded optical test.

Create an empty annotation file (the command deliberately adds no example measurements):

```sh
tools/panel optical --template /private/tmp/panel-contact.csv
```

For each tap, enter a unique `sample`, `condition`, `action` and `status`. For a completed response, bracket
both onsets: `contact_before` is the last frame definitely before contact, `contact_after` the first definitely
after; `response_before` is the last unchanged frame, `response_after` the first visibly changed frame.
Use wider brackets if contact is obscured. Annotate at native source-frame resolution and retain the source video.
For `status=no_response`, leave response fields empty and enter the last observed `observation_end` frame.

Analyse the actual local recording and annotations:

```sh
tools/panel optical --annotations /private/tmp/panel-contact.csv --capture-fps 240 \
  --video /absolute/path/to/original-video.mov --out /private/tmp/panel-optical-report.json
```

The analyser hashes the video for provenance; it does not verify capture rate or recognise contact itself.
It computes each latency interval as `(response_before − contact_after)` through
`(response_after − contact_before)`, divided by capture fps, in ms. The midpoint is only an estimate.
It retains negative/overlapping intervals for review. No-response taps remain censored outcomes with lower
bounds, so completed-only p50/p95/p99/max must never be reported as the whole population's performance.
With fewer than 100 completed taps, p99 is normally the observed maximum. Compare the same camera,
capture rate, framing, contact method, action and workload, and publish uncertainty bounds with any estimate.
