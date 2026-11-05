# Now playing on the hallway panel

The dashboard has a compact `Spelas nu` row below the clock while the panel plays
or pauses music. The idle layout stays unchanged. The row shows the channel/track,
source and presenter/artist, plus pause/resume and stop. The caption retains an
explicit paused state; playing without artist metadata uses Spelas nu. Tap elsewhere on the row for the
larger sheet, silent volume control, and previous/next when a queue supports them.
Stopping hides the row on Hem and the dashboard and empties Spelas nu at once, whatever plays (Joel, 2026-10-30);
the panel logs `music: stop: row cleared (panel|speaker)`. If Home Assistant cannot stop the speaker's music, the
row comes back with the error. Paused music stays visible so it can resume.

`styles/glass-v2/now_playing.py` owns the generated widgets, actions and board
wiring. `now_playing.h` owns the small bounded status parser and local state.
`media_artwork.py` prepares one current 288×288 cover asynchronously in HA (144 before the Musik page, 2026-10-28).
Radio uses the channel image already returned by SR's channel list. Only HTTPS
images from `static-cdn.sr.se` are accepted, without following redirects. A logical
Music Assistant player uses its own HA image helper/cache, scoped to this panel.
Image download/decoding never blocks the play command or HA state callback.
Compressed input is capped at 4 MiB and dimensions at 4096×4096 pixels. Pillow
resizes on an executor and produces QOI. A public opaque-key route serves only
that prepared current image; GET cannot choose/fetch a URL or trigger conversion.
No access token or original image URL is sent to the panel. Stop invalidates it.

HA forwards only changed attributes. A player's opaque cover key includes both the
prepared image and display/track identity so consecutive tracks sharing an album
cover still deliver a new image key after the title change. A station's logo key
leaves the song out: it stays the same while only song/programme text changes, and
the panel keeps the logo, dropping its cover only when the source changes. With the
song in the key the logo blinked away for about a second at every song (2026-10-27). Where it plays (panel or
Bluetooth speaker) is in the key, so moving the radio hands the panel a new key after it dropped its cover; with
the same key the logo stayed away after a move. The panel also keeps HA's last key and shows it again when its own
player goes idle under speaker music (`Artwork::restore`). The matching HA
regression guards this interoperability contract.

The artwork worker downloads once per cover key into a bounded 288×288 RGB565 image for the Musik page's
record and averages its 144×144 thumbnail before publishing. `cover_small.h` attaches that immutable copy
for the rounded 40-pixel row thumbnail. A failed download leaves text controls usable without an automatic
retry loop. The fetch runs as soon as HA's key or the music arrives (`music_art_fetch`; the 500 ms interval only
retries). The first Spotify music after boot or a stop holds the row back until its cover is in, at most 4 s
(`hold_first_cover`), so title and cover appear together (Joel, 2026-10-29). The first source after boot or a stop
keeps a key that came before the text (`source_changed`: dropping it left the row without a cover and the record
black until the next song), and the black "nothing playing" record follows the real state, never the held view
(while the first song waited for its cover the held view turned the record black over the downloaded cover).
`music_ui.h` compares a move with the set style, not `lv_obj_get_x/width`: those are the last layout's coordinates,
so two renders in one frame (cover off, then on) left the row's text under its cover (2026-10-29). The first gate,
`tools/panel music-cover` (`tools/music_cover.py`), fails when a playing row shows no cover or text under it. A held song lets go on the download
as soon as the cover is downloaded (`cover_in`); waiting for the record too held every song on Hemmet the full 4 s
and about 4 s after next on Spelas nu. Source changes hide the row's old cover; the page keeps its old record turning until the new
atlas is ready. A superseded completion cannot replace the new track.
The performance contract checks exactly three boot/background downloads and a
separate on-demand `async_artwork` cover worker with bounded 4 KiB reads and no periodic downloads. This
replaces the disproved assumption that every online image is a boot download.
Music Jarvis starts in a voice turn ("spela Cocktails", "sätt på P3") opens the Spelas nu tab by itself (Joel,
2026-10-30; `styles/glass-v2/music_jump.h`, wired in `now_playing.jump_after_voice`, tests/test_music_jump.py): the
panel notes what plays when it starts listening, and music that starts or changes from then until 20 s after the
turn (a new Spotify track or another station, not a station's next song or a track that ran out) ends the turn on
Spelas nu instead of the page it came from (at once when it already plays as Jarvis stops speaking, without the
orb's 3 s settle), or jumps there from Gå hemifrån, Hemmet, Tavla, the weather or the
calendar page when it comes later. Never at night; wakeups and announcements never listen, so they never jump. The
boot log says `Jarvis started music: Spelas nu` each time.
A tap on the music row (Gå hemifrån, Hemmet) opens the Spelas nu tab (`music_open` -> `tab_go`); the sheet it
opened before, with its own 144 px record, is gone (Joel, 2026-10-28), so the notes about "the sheet" below are
history. A refused control shows `mp_error` under the page's controls. The Spotify chip names the speaker as Home
Assistant does ("JBL Charge 4"), "JBL-högtalaren" only without a name.
On the Spelas nu tab, Spotify and other tracks show their cover as a picture disc at 33 1/3 rpm.
`vinyl_motion.h` owns acceleration, braking and the arm. `vinyl_sampler.h` stores only pixels the cover can
change; fully opaque overlay pixels remain static. One lossless RGB565 atlas is about 7 MB of PSRAM. The second
exists only while a new cover builds: the worker allocates it for the new cover and frees the old one once the new
one turns (`claim_build_slot`/`release_atlas`; 2026-10-29, steady PSRAM free was 4.2 MB, 15 KB above the 4 MiB
health floor). Each of the 54 images is its own ~130 KB block (`chunks`, `frame_ptr`): one contiguous 7 MB block
failed after boot with 11 MB free, and the record froze 1.2 s at an album change (`vinyl-60-20261029/flow-1`).
**Faster cover changes (2026-10-30).** Next-track to the record turning the new cover: 2.52–3.02 s
(`vinyl-60-20261029` flow-7/8/9) → 1.00 and 1.17 s (`.panel-ci/cover-fast3-20261030-2`, `-4`, album changes;
`tools/panel vinyl-flow --explain REPORT` → `next_timeline`; `tools/cover_change.py --out NEW` runs several skips
and prints them, touching Spotify only when Jarvis has it or with `--take-over`), the handoff verdict passing. Three parts:
HA prepares the next Spotify track's cover from Soloist's queue (`get_queue` → `queue_changed`,
`spotify_state.upcoming`, `MediaArtwork.prefetch`) and publishes its future key as `artwork_next`; the panel
downloads it into async_artwork's free buffer (`prefetch`, `Ahead`), never while a cover builds, and a later
`artwork` with that key publishes the buffer at once (`Artwork ready ahead`; status `artwork.ahead_hits`): the
build started 0.42 s after the skip instead of 1.25–1.79. An unused cover ahead is kept until an update takes
or drops it: right after a skip HA names the track after next first, and replacing it threw the new track's
cover away. The record turns from every sixth image first (9, 20° apart, blended; `COARSE`), then every other,
then all: the first stage took 0.5–0.9 s, the whole atlas 2.8–3.4 s (was ~5 s).
The record also builds the next cover's first stage ahead (`source_ahead`, `ahead_batch`, from async_artwork's
`add_on_ahead_callback`), into the atlas slot not turning, while nothing else builds: ~1.2 MB of PSRAM while it
waits. The real source of the same picture (same `picture_identity`) adopts it and turns at once (`new cover
turns at once (built ahead)`); another picture drops it. Images are allocated one by one (`ensure_atlas`), so a
slot holding only those nine is never built into as if complete.
Measured with `tools/cover_change.py --runs 5 --take-over` (`.panel-ci/cover-ahead-build-20261030`): the record
turned the new cover 0.40–0.44 s after each skip, the same moment the panel learned of it (5 of 5 built ahead,
`artwork.ahead_hits` 5), whole atlas 2.2 s, handoff verdict passing; PSRAM lowest 6.1 MB, steady 8.5 MB free.
What is left is Soloist → HA → panel; the record itself no longer waits.
**A cover change fades (2026-10-31, Joel: "mer smooth").** At the switch the old atlas is kept for 18 images
(0.3 s): `fade_frame` draws the new cover packed (PPA, no expand), blends the old cover's sharp image at the same
angle over it in one PPA pass at a falling weight (`fade_weight`, ease-in-out), then expands; after that
`keep_behind` runs as it did at the switch (log `old cover fades out over 18 images`, status `fades`,
`fade_images`, `fade_cut`). No extra buffer. The builder ends a fade early (`end_fade`, `fade_cut`) before a new
cover claims the old slot or the odd images need its room; the ahead build waits for the fade; a "föregående"
during a fade adopts the fading cover. Measured with `tools/cover_change.py --flow --runs 4`
(`.panel-ci/fade-attempt-20261031/v2`): new cover 0.43–0.46 s after next (0.42 before), built ahead every time,
1 repeated frame at the switch in 3 of 4 runs (fade image 12–13 ms of worker time), whole fades 4 of 4, PSRAM
lowest 4.85 MB. A first version (`fade-v1.patch`) drew the old cover into a separate 166 kB image: the ahead build
then lost its room after every change (`not building the next cover ahead: PSRAM is short`), the new cover came
at 0.77–0.88 s, and fade images took up to 22 ms (2–6 repeated frames). `FLASH OK` and every gate passed for
that one too, so judge such a change by `cover_change.py --flow` before and after (`repeated`, `record_max_ms`,
`psram_min` on each line) and the log's `built ahead` lines, not the gates.
**The song change's own frame (2026-10-31).** Even before the fade the record repeated a frame at a song change:
`responsiveness.slowest` (the slowest render of the last 2 s with its areas, `bench/responsiveness.h`) showed one
16-21 ms render redrawing the record (288x288), the arm strip (64x236) and title, artist and album at once
(512x90). `panel_music_ui::text` now changes at most one visible label per refreshed frame (`Spread`, music_ui.h);
the others follow on the next REFR_READY. Shuffled liked songs, a new cover each skip
(`.panel-ci/switch-hitch-20261031/spread`): repeated frames per change 1, 1, 2, 1 (before, `before/`: 8, 3, 3),
the slowest render 15.8-17.2 ms with only the title's 512x62 beside the record and the arm. What is left is
that one frame: record + arm ~12 ms plus the title. Splitting the title into two clipped half-width labels
(so each frame redrew 256 px of it) was worse and reverted (`.panel-ci/switch-hitch-20261031/halves`): 4-9 repeated
frames and 27-31 ms renders of the whole column (512x351), as the nested size-to-content halves re-laid out the
column. A song whose title wraps differently also moves artist, album and progress: that frame redraws the
column whatever the spreading (`spread-again`, one 29.9 ms render). Test skips on music with a different cover per track, not
inside one album (Joel, 2026-10-31: Mylo Xyloto gave the same cover every skip); `cover_change.py` marks such a
run `cover_changed: false` and warns. Use Joel's liked songs shuffled, not a playlist picked for him (he did not
want the country list): on Jarvis `soloist ctl --ws 127.0.0.1:9431 play spotify:user:spotify-user:collection`, then
`shuffle on` (`spotify.py play` refuses that URI). Each line also carries `slowest_ms` and `slowest_rects`. A late hold 5-7 s after a skip (66-116 ms, and
`/status` silent for 1.7 s) came only in runs covering uptime ~301-310 s right after a flash and its gates
(`v2`, `halves`, `titlebox` run 4); runs later never had it and a plain restart showed nothing at 300 s
(`.panel-ci/boot-300s-20261031`); cause not found, not in normal use. `cover_change.py` prints `uptime_s` and
warns under 330 s: measure from ~6 min after a flash.
"Föregående" is as quick: the atlas left keeps its first stage as `behind` (chunks[2], `keep_behind`), async_artwork
keeps the cover left in a third buffer (`behind_`, status `artwork.behind_hits`), and HA's cache still holds
the previous cover, so the same picture again turns at once (`turns at once (built before)`); back and forth
both stay instant. The panel's own next button does not wait for HA at all: `take_ahead` turns the cover
built ahead on the press (`next cover turns before the skip is confirmed`), HA's confirmation (the same
picture) changes nothing, and a refused skip puts the shown cover back from `behind`. The log times every
step: `music: next sent`, `music: now playing: …`, the record's `turns …` line;
`tools/cover_change.py --out NEW --sequence next,next,previous,previous,next` reads them per press.
PSRAM: ~1.2 MB each for the record's ahead and behind, ~200 kB for async_artwork's third buffer.
Measured by the panel's log clock (`.panel-ci/cover-prev2-20261030`, `--wait 2.2`, next and previous alternating):
the panel's next button turned the cover built ahead 0 ms after the press (HA's new track 71 ms later);
previous, and next back to the track just left, turned from `behind` 92–128 ms after the press, ~25 ms after
HA's new track (67–102 ms: Soloist's skip and HA's push, outside the panel). `behind` is published by
swapping it with the shown buffer, never the free one a download ahead writes: through the free one a
previous press waited 270 ms behind the next cover's download. Spotify's previous restarts the song after
~3 s of it, so it changes the cover only within that. PSRAM lowest this boot 4.77 MB (floor 4 MiB), 8.4 MB
free between changes.
Since 2026-10-30 a new cover builds on its own task, `vinyl-build`, at the idle priority on core 0 beside the
record's worker, which preempts it for every image (before, the worker built it between its images and got about
30 % of its time: one change took 4,759 ms for 1,416 ms of sampling). Not core 1: LVGL draws Spelas nu there for
~15.5 of every 16.6 ms, and the half took 2,913 ms. The worker alone switches to a published half or whole atlas between images
(`publish`/`apply_publish`); a cover with no room for a second atlas is handed to the worker, which stops the
record before the builder draws over it (`claim_for_builder`). The log says `built beside the worker` at the half.
A new cover builds its 27 even images first (`build_image`), then turns from them (`active_step` 2, PPA blends
6.7 degrees apart) while the old atlas is freed and the odd images follow on the idle windows only (building
alongside those blends dropped frames); a change needs 1.5 atlases. Measured after next (`flow-7`/`flow-8`, 0 repeated
frames in every phase): HA key ~0.4 s, download 0.64–0.90 s, title and row cover then at once, the record turning the
new cover at 2.5–3.0 s, complete about 5 s (2026-10-29; since 2026-10-30 see the next paragraph); free PSRAM stays above 7.4 MB during the change. With two
full atlases at once the free PSRAM fell to 3.98 MB during a build and the responsiveness gate failed on its
4 MiB floor (2026-10-29, `.panel-ci` flash after the faster build); do not lower that floor.
Without room even for the blocks the new cover builds over the turning one, which holds still meanwhile (logged
`no room for a second atlas`). The boot-lifetime `psram_min_free` still includes that peak. Full speed
copies a sharp precomputed angle; the reversed half-turn stages each row forwards through the existing internal
tile to avoid backwards PSRAM cache misses (bit-identical native coverage includes an undersized-tile fallback).
Interpolated half-turns use the same internal tile for forward reads and in-place reversal; mixed half-turn
inputs are fetched forwards before reversing internally. Native tests cover odd counts, tile boundaries,
small-tile fallbacks and guard pixels without allocating another frame buffer.
Start/stop use PPA to interpolate neighbouring angles and avoid 3.33-degree holds. The packed result expands
backwards without overwriting unread data. Staging both rows in the internal tile remains the CPU fallback;
native tests prove exact pixels against the original CPU interpolation for the full turn and too-small-tile
fallback. Preferring that CPU path measured 14–24 ms sampling on the P4 and missed more boundaries, despite
avoiding PPA traffic, so hardware blending remains the default. No additional frame buffer is allocated.
Discarding a finished image offscreen does not release the output buffer LVGL still references; the native
buffer test exercises leaving and reopening the page as well as cancelled cover preparation.
`vinyl.sample_method` identifies sharp atlas copies (0), staged CPU interpolation (1), PPA fallback (2) or
direct CPU fallback (3). `gpu_blends/gpu_errors` report only the hardware fallback. `sample_us/sample_max_us` and
`arm_prepare_us/arm_prepare_max_us` split the worker's current and boot-maximum image/arm costs; they are
atomic snapshots formatted on the status worker, not extra synchronous UI queries.
The flow report labels worker/UI-arm costs as `*_status_samples_ms`: those distributions sample the last
completed job at each status read, whereas render and presentation distributions retain every ring entry.
Music controls retain their colors and pressed/disabled/focus feedback, but change state immediately.
The fixed source/byline line now shows `Pausar …`, `Startar …` or `Byter låt …` immediately after a press,
then a short confirmation only when both the HA service response and a fresh matching player state/position
arrive, in either order. A refusal or eight seconds without confirmation shows a bounded error instead.
The idle Spelas nu view has the same status line for Stop. Buttons keep their colors; metadata and controls
do not move. Stop can preempt an outstanding skip. Each dispatch captures its request ticket, so a late
response cannot finish, fail or roll back a newer command. State changes, refusals and timeouts are in the
panel's `music` log (`tools/panel logs`). Feedback updates only the small status labels and cancels their
queued old captions; it never redraws the metadata column just to acknowledge a press.
Refusal and timeout share local recovery: a still-playing speaker's row returns after an unconfirmed Stop,
and an unconfirmed speculative next cover returns to HA's current image. Neither path retries the command.
Urgent status reserves the visible-label budget for that frame. Metadata already admitted since the last
refresh yields back to its previous text and queues its newest value, so arrival order cannot put a large
title and the urgent caption into the same record frame. The native ESP32-path regression exercises both
orders and retains newer queued values. The first mixed run (`.panel-ci/ui-mixed-first-20261102/`) retained
four repeated record images despite a 60.15 images/s average; the largest presentation gap was 33.13 ms.
`music_buttons.h` detaches only shared style bindings whose sole property is a transition, leaving their
shared theme objects untouched. LVGL collects transitions from every attached style, so an empty local
descriptor cannot override a theme transition. A null descriptor crashes in `update_obj_state`, `tr->props`;
the generated-config regression rejects that call. The native binding test guards the retained feedback,
mixed-property styles, local styles, idempotence and other widgets' shared theme references.

`tools/panel music-load --out .panel-ci/NEW-DIRECTORY` runs a silent 18-second mixed workload on the real
panel: a moving record, new synthetic covers every three seconds, three metadata changes every 600 ms,
and pending/accepted/confirmed/refused/timed-out feedback. It uses the production record worker and label
budget without changing live music state, audio, settings or calling HA/models. The firmware's 26-second
deadline and ordinary lease/preemption restoration still apply. Raw render, display and actual record-source
scanout samples, host latency and observer costs stay in `report.json`; the verdict reuses vinyl-flow's
unchanged 59 images/s, 25 ms maximum and 18 ms p99 limits. Missing metadata/cover work or no new cover
publication fails too. Synthetic covers exercise atlas preparation, not network downloads or audio quality.
After the fixed motion phase, a separate phase runs 20 real dashboard/Hemmet taps while background cover
preparation continues. It retains fresh press/release samples and host latency and reuses the existing
absolute input limits; it does not compare this mixed workload with the idle baseline. The motion phase's
protocol and samples remain separate. Restoration also cancels injected labels still waiting in the queue,
without replacing later real metadata; the failed queued-restoration probe is kept in `.panel-ci/ui-restore-probe/`.
Unchanged HA resyncs cannot erase the fixture on a page change. A changed real track/state, album or cover
still preempts it. Every navigation sample requires the load to remain active; an ordinary idle tab test
cannot count as background-load coverage. The first priority run's motion passed, but its navigation load
stopped and the run correctly failed (`.panel-ci/ui-mixed-priority-20261102/`). Its repeat also retained a
fixture-preemption failure. These reports are not passing references.

Final physical verification (2026-11-02, build `631889398232d2f38c7e2da195950636`): both
`.panel-ci/ui-mixed-final-20261102/report.json` and `ui-mixed-final-repeat-20261102/report.json` passed.
The fixed motion windows presented 2,163 distinct record images in total, 60.372–60.375 images/s,
zero repeats and a 16.583 ms maximum presentation interval (before: four repeats, maximum 33.133 ms).
Each separate 20-tap navigation phase retained active background work and requested two new covers.
Across those 40 taps, panel press→redraw p50/p95/p99/max was 5.551/6.612/7.600/7.600 ms;
release→redraw was 32.896/35.808/38.245/38.245 ms. Host command latency was separately
69.508/81.949/86.837/86.837 ms, including the intentional 60 ms press. Mixed-load status HTTP latency
was 33.948/69.135/105.808/106.819 ms; UI-task observer cost was 0.569/0.964/1.567/1.753 ms.
With 40 taps, p99 is the observed maximum, not a population-tail estimate.
The independent fixed responsiveness workload also passed: release p50/p95/p99/max
29.973/31.841/32.511/32.511 ms (before 31.460/33.397/37.436/37.436 ms); these live-content
runs do not isolate a causal navigation speedup. All standard flash gates and the 120-second orb soak passed.
The new pending label was verified in the actual widget tree at (456,108), 512×21; the separate moving-record
JPEG preview returned the already documented HTTP 503 capture limitation, so no new pixel-quality claim is made.
Day volume 65%, screen light 35% and night light 15% were read back unchanged; the dashboard was restored.
Run the separate fixed responsiveness/throughput and release gates as usual. `tests/test_music_feedback.py`
guards confirmation order, stale responses, Stop preemption and ownership-safe fixture restoration;
`tests/test_panel_music_load.py` prevents a high average from hiding a missed record frame or absent load.
`/status` also exposes the last
joined `vinyl.render_rects/areas/pixels/largest` and `arm_work_us`; snapshots occur on the UI owner and JSON
formatting remains on the existing status worker. These separate moving-arm work from display wait time.
The worker builds the other atlas in bounded four-row batches,
visiting 32 px columns across each batch so diagonal source pixels remain in cache. Its fixed-point row
origin is unchanged; native tests compare every prepared frame with the original complete-row algorithm,
including partially opaque overlays. Display requests are serviced first on every iteration.
With a live clock and an existing atlas, preparation
uses only the submitted frame's idle window. Four-row batches leave 2.5 ms before the next boundary; a
single row can use the later tail until 0.5 ms before it. Otherwise it sleeps
one RTOS tick and rechecks; it never delays the record timer or the ready-image handoff. Clockless/first-atlas
paths retain bounded progress. A stopped page bypasses the display-idle wait because it has no frame
submissions; next while paused must finish without a later play. Native tests cover those budget states,
the real stopped-page selection and cancellation during a one-row batch. Single-row foreground batches
starved a new cover past the eight-second next window; four rows amortise setup while retaining the same
display-idle window and boundary margin.
It never
changes the active atlas. Its priority is 2 only while Spelas nu is visible and 0 otherwise, below the orb
prefetch worker at 1; background album changes must not interrupt the voice animation. New source requests swap immutable input buffers between batches; only a complete
current generation can be published. Metadata waits until the corresponding image is actually shown.
The record clock and ready-image handoff continue while another widget has a submitted frame in flight.
Motion advances once per physical boundary. Three output images form a queue (2026-10-29): one LVGL shows, one
ready for the next boundary, one the worker draws, each job naming the boundary that takes it and its motion
predicted to there. With the opaque arm the worker runs two frames ahead. Before, it had one frame: a start/stop
job (PPA blend 5–12 ms plus a moving arm up to 9 ms, while LVGL copies the previous image) sometimes finished after
the flip, LVGL then began mid-frame and missed the next one (`flow-30`/`flow-31`: one 33 ms interval per pause or
play, `vsync_latency` 15 ms then a 13.8 ms render with no flush wait). A boundary takes only its own or an overdue
image (a late one between boundaries, without a second motion step); older ready images are dropped and jobs from
before the page last opened are stale. Slot states move FREE->WORK->READY->SHOWN; only the worker leaves WORK.
The main task queues up to two jobs per tick and the worker draws the oldest first: while the record turns, LVGL's
wait for the flip plus its render fill the frame and the vinyl tick runs about once per frame, so one request per
tick never let the queue run ahead (`taken_spare` stayed 0 in `flow-2`). `/status` `vinyl.taken_boundary`,
`taken_late` and `taken_spare` count images taken on their boundary tick, later in the frame, and with another
finished image already waiting. With two finished images queued the atlas build may use the whole frame
(`atlas_budget(..., spare)`): after next the new record turned at 4.6 s instead of 7.3 s (build 2.6 s instead of
5.4 s; `vinyl-60-20261029/flow-5`, still 0 repeated frames). The rest is HA (~0.3 s, the 300 px Spotify cover) and
the panel's download of the 142 KB QOI (~0.67 s at boot; 1240 ms after next while the record turned, with the
artwork worker at priority 0 (sharing slices with core 0's idle task) and 1 KiB reads; now 4 KiB reads into a PSRAM buffer at
priority 1 while Spelas nu shows the record and 0 otherwise (`set_foreground` via `Disc::on_visible`): at 2 the
record dropped frames during the download (`flow-6`); at 1 always, a song change during the soak gate made the voice
orb's prefetch fall back). Title and row cover change as
soon as the cover is downloaded (`cover_in`, Joel 2026-10-29); the record follows when its atlas turns.
The outside-arm strip has a pool of four (`RightArm::POOL`): an unchanged rounded arm shares the strip already
painted, a moved arm paints a strip no shown or queued image uses. The main-task arm path keeps one frame of
lookahead because its worker masks are shared. `test_vinyl_buffers.py` runs the queue for ten boundaries, stale
and overdue images, an offscreen discard and a moved arm.
LVGL still waits for its framebuffer before rendering; waiting in the record timer too froze the disc for
about 132 ms during the button press transition. `test_vinyl_buffers.py` exercises a pending frame explicitly.
The moving arm uses local line coordinates so each line invalidates its tight bounding box.
On the P4 its five strokes use a pair of 23 KiB bounded A8 cache sets (`vinyl_line.h`) in PSRAM. The existing record worker
prepares the next arm from the same predicted motion as the record. The main task copies finished pixels and
coverage into its own set before requesting another job; LVGL never references buffers the worker can change.
The worker also blends the five strokes into the opaque record output in their widget stacking order. Only
the arm pixels to the right of the record remain A8 widgets; the full-motion native sweep guards that every
outside pixel lies there. This lets the record keep LVGL's opaque cover/copy path even while the arm moves.
Each output reuse restores its static surround before painting, so an old arm cannot leave a trail there.
The native tests cover RGB565 alpha arithmetic, stacking, clipped descriptors and static-pixel restoration.
LVGL's `lv_draw_buf_init` requires `data_size >= stride * height` even for a left-cropped A8 view. Its
descriptor therefore declares the actual remaining owned allocation after the crop. Declaring only the
visible payload made LVGL reject the outside-arm image. The full-motion sweep checks the remaining capacity,
and the handoff test enforces the real decoder's minimum plus the allocation's exact end.
Unchanged endpoints reuse masks. Native tests sweep a 101×101 lift/groove grid, check rounded caps, overflow
and independent UI/worker ownership, and compare alpha against the original rasterizer. Squared distances reject fully transparent pixels and
accept fully opaque interiors; only the one-pixel antialiased fringe computes a square root.
Conservative per-row capsule bounds skip the empty parts of diagonal masks after one bulk clear; native
alpha comparisons include those skipped pixels and the rounded caps.
Allocation failure frees the worker cache and retains the main-task path.
Moving the arm suppresses only its automatic damage while updating the widgets, after flushing unrelated
pending layouts. `vinyl_damage.h` then invalidates the opaque 288 px record separately and subtracts that square
from the old/new arm pixels. `ArmDamage` marks bounded 16 px tiles only where A8 coverage can change a pixel,
then merges neighbouring tiles into disjoint rectangles. When all outside pixels lie to the right, a bounded
single strip replaces repeated widget-tree traversal for those small groups. Unexpected geometry keeps sparse
subtraction, and neither path overlaps the record. Coverage is prepared with each mask and merged as tiny
bitsets; rescanning old/new PSRAM mask rectangles took up to 5.5 ms on the UI task. Identical rounded arm geometry
does no work. Earlier coarse regions caused large PPA row-window cache flushes; the compact strip stays
outside the record and uses the opaque middle plinth pieces. Its cost is retained in the real flow timings.
LVGL otherwise joins overlapping
boxes and repaints the background/plinth behind the record during braking. The record's even coordinates preserve
the split under the panel's 2 px rounding. Allocation fallback retains normal automatic damage. The native test
covers every old/new visible arm pixel, forbids overlaps into the disc and keeps area count below LVGL's ring limit.

The plinth's middle left/right PNGs are verified fully opaque and use RGB565 without an alpha plane, so LVGL
can start their small redraws at the image's opaque cover instead of repainting and blending from the page root.
Top/bottom retain their transparent rounded corners. Native asset/config checks guard this distinction.
Deferring the entire LVGL refresh while a display flip was pending reduced the measured wait but worsened
real presentations; restricting refresh starts to a short vsync window made it worse again. Those experiments
are retained under `.panel-ci/vinyl-60-20261028/flow-{2,3,4}` and were removed. Keep the driver’s buffer safety
wait and measure presentations; lower wait time alone is not a smoothness result.
Network reads, QOI decoding, the picture identity hash and 144 px thumbnail preparation run on `components/async_artwork`'s
priority-0 worker. Two immutable 288 px + 144 px buffer pairs prevent decoding into images LVGL still uses.
Only publication and the existing music callbacks run on the main task. The configured media-art origin/path,
32-hex key, 288×288 QOI header, 414742-byte cap, eight-byte terminator and request deadline are checked.
The feeder withholds the last eight bytes because the upstream QOI decoder reports the whole input chunk
consumed even when the final pixels precede the terminator; the native test keeps that marker intact.
`[artwork]` logs completion/failure and elapsed time in the existing ESPHome log (for example
`tools/panel crash capture --out PRIVATE_FILE`); `/status` reports `artwork.worker_us/publish_us/errors`.
No secrets or URL keys are logged. Unchanged metadata text, geometry and opacity are not set again; pending
command start/acknowledgement no longer redraws the metadata column. `tests/test_async_artwork.py` guards
bounds, decoder/published buffer ownership, terminator preservation and no-op widget updates.

Measure with `tools/panel vinyl-flow --out NEW_DIRECTORY` while Spotify already plays. It wakes Natt when
needed, opens Spelas nu, records ten seconds of steady rotation, pauses, resumes, skips once, then restores
Natt. It preserves volume. This changes the real song once.
The tool resolves enabled controls from their icons and shared parent in a fresh widget tree before each
action, outside its timed window. Wrapped titles can move the row. Ambiguous or disabled controls fail before
input; a timeout is retained even if restoration also loses the connection, and input is never replayed.
The report records `album_changed`: another song from the same album does not exercise new-atlas preparation. Use `--require-album-change` when that is the
case under test; it requires both different album text and a newer displayed record generation during the
next window. Metadata alone is not a finished cover. It fails the diagnosis rather than claiming an atlas
change that never reached the UI. `--next-only` runs just
the eight-second next phase. A screenshot of Spelas nu while the record turns answered 503 "panel busy or capture timed out" on 2026-10-29
(the capture needs a moment with no flip pending; copying the scanned buffer instead coincided with two candidate
aborts and was reverted, restart-crash.md). Since 2026-10-30 `screenshot` and `inspect` then fall back to one `/clip` frame
(JPEG, `"source": "clip"`, `"lossless": false`), so Spelas nu can be captured while it plays; check the record through `/status` `vinyl`
(`speed`, rising `frames`, `generation`) instead. `tools/panel vinyl-flow --explain REPORT` reads a saved report without the panel and
lists each missed moving boundary with the worker costs, LVGL phases and queue counters (`taken_*`) around it,
plus `next_timeline`. `--queue spotify:track:ID` queues another album's track on Soloist first and `--log` records
the ESPHome log beside the run (`OUT/esphome.log`, its `[artwork]`/`[vinyl]` timing lines in the report):
`tools/panel vinyl-flow --require-60 --require-album-change --log --queue spotify:track:3cxRaVmDRFflWFgDg9TkX5 --out NEW`. Protocol v2 stamps the record's monotonic image serial into each submitted
framebuffer. `vinyl_presentation` counts new record images at the physical display interrupt; a button redraw
with the same serial is a repeated record, and intentional paused/hidden intervals are excluded at the source.
`--require-60` retains the same absolute limits: at least 59 presentations/s, p99 ≤18 ms and max ≤25 ms for
both UI presentations and actual new record presentations. One skipped ~33 ms boundary fails even with a
good average. The original UI-handoff timings and v1 verdict remain in the report as diagnostics; they can
vary inside a scanout period and are not substituted for actual record presentation timing. V1/V2 source
cadence distributions are different measurements; compare their UI presentations and handoff diagnostics
under the same policy, and use v2's unchanged absolute limits for actual record scanout. No baseline changed.
It compares adjacent samples whose record speeds are both nonzero; intentional paused time is excluded,
not slow samples during motion. `active_motion` retains that window and its presentation distribution. Validate a fresh output directory before any separate queue manipulation. The private report holds all raw status,
presentation and HTTP timings plus UI snapshots; compare the same protocol before/after. The pause summary
uses its first second; the first resume presentation interval crosses intentional idle and is excluded once.
Other slow samples are retained. `vinyl.interval_ms` measures actual record-image handoffs, not every redraw;
`presentation` measures scanout boundaries for the whole UI. Neither establishes optical smoothness.
The worker reports preparation time and `vinyl.worker_us`; `[vinyl]` logs source readiness, start/stop and
images shown when leaving. A paused record intentionally stops producing images.

The original investigation and pre-change measurement stay in `.panel-ci/vinyl-audit-20261028-2203-valid/`
and `.panel-ci/vinyl-smooth-20261028/flow-before/`. Do not compare an entire pause's low redraw average to
steady rotation or count buttons/arm redraws as new record images. `tests/test_vinyl_buffers.py` checks that
preparing/cancelling a cover cannot overwrite the active sampler; `tests/test_vinyl_sampler.py` bounds pixel
error against the original bilinear renderer under ASan/UBSan; `tests/test_vinyl_flow.py` keeps slow samples.
The turntable is always there (Joel, 2026-10-28): radio plays a black record
with the station's logo as its label, nothing playing shows a plain black record with the arm up. On the Spelas nu page the record lies on a walnut turntable (Joel's
choice P1; `turntable.py` draws `assets/turntable_p1.png` and the page record's still layer
`vinyl_overlay_288_p1.png` with the plinth around the circle, so the record's square image is opaque; the plinth
itself is four pieces around that square (`turntable_p1_{top,bottom,left,right}.png`), none of it under the record:
with one plinth picture under it LVGL still blended it every frame, 14.4 ms against 6.5, though the record covers
it, so do not rely on LVGL's cover check for anything drawn 60 times a second; the panel shows 16-bit colour, so
fine shading in a generated picture rounds away (the plinth's grain at alpha 16-28 spanned two RGB565 steps and
did not show): check a new picture's detail after `>> 3` / `>> 2`, draw it strong enough, and dither it onto the
5/6/5 levels in its generator (`turntable.dither_rgb565`; ESPHome dithers only 1-bit images); `flip_record` keeps that surround upright on the upside-down half turn). The tonearm is
Joel's T4: matte black with a lighter rim, a silver ring at the pivot, a counterweight, a white cartridge; lifted it
lies straight down from the pivot onto a rest below it, off the record. On the Spelas nu page radio shows a walnut radio
instead (Joel's choice B1; `radio_cabinet.py` draws `assets/radio_b1.png`, `radio_dial.h` maps the station to its
Stockholm FM frequency): the station's logo and "FM 99,3" in the dial window, and a red needle that glides there in
0.7 s when the station changes; DIREKT stands where Spotify's time line is. A new song's cover
replaces the old one when it is downloaded; until then the record and the row keep the last cover
(`Artwork::shown`), so nothing blinks at a song change; another source or a stop drops it.
A tap on a cover's record shows it as a sleeve instead (Joel, 2026-10-29; the turntable stays the default): the cover straight at 288 px (`mp_sleeve_image`, the same `np_art`) with a black record's strip out of it on the right (`sleeve.py` draws `assets/sleeve_record.png`, opaque on the page's colour). The sleeve starts at the plinth's left edge (x 29) and the record ends at its right edge (x 436), so the outer 23 px of the record's cream paper label show beside the sleeve, as on a real LP pulled out of its sleeve (Joel, 2026-10-29). A tap on the sleeve brings the turntable back. The record and arm are hidden (`page.hide`) while the sleeve shows, so nothing is drawn per frame. The page's full-screen tap decides by where it landed (`music_sleeve.h`), so nothing clickable lies over the turning record; radio, nothing playing and a cover not yet in keep the old tap (back). Leaving the page (`LV_EVENT_SCREEN_UNLOADED`) puts the turntable back; a song change on Spotify drops the cover for about 1.3 s, so the sleeve keeps showing for up to 4 s without one (`GRACE_MS`; resetting it, then hiding it, both brought the turntable back in between, 2026-10-29) and then shows the turntable, logging `Spelas nu: no cover for 4 s, turntable`. To check it on the panel, show the sleeve, wait past the song's end and read `tools/panel logs`: a `Page record left` line after `Spelas nu: sleeve` without a tap means the turntable showed in between; a screenshot works while the sleeve is up (the record is not drawn); the choice stays until a tap or leaving the page. A generated picture's flat page colour must be LVGL's truncated RGB565 value (`sleeve.PAGE`), not dithered: dithering rounds 0x141826 to a 16/24 mix that showed as a lighter box round the record; the log says `Spelas nu: sleeve` / `turntable`. `sketch/vinyl-modes.html` holds the mock-up it was chosen from.
The still layer (`assets/vinyl_overlay_<size>.png`, made by `vinyl_overlay.py`: grooves, sheen, rim, spindle
and the colour outside the circle) is blended into each output image by the worker. LVGL copies one opaque
image without a clip mask. The tonearm lifts in 0.4 s; pause stops the record in 1 s; play starts it in 0.6 s.
The thumbnail stays square. After changing anything drawn over the record, measure the real page with music
playing: the ordinary animation release gates cover the listening orb, not every music transition.

The Spelas nu tab (between Hemmet and Tavla; first called Musik, renamed since it shows radio too) takes the whole
screen in the card's colour, no painting around it (Joel's layout B, 2026-10-28): the record left; right of it the
source, the song, the artist, the album and its year, a time line (Spotify: Home Assistant's `progress` attribute,
"position ms|duration ms|1 while playing", which the panel moves on by itself between updates; the time
gone on the left, the time left on the right) and the controls;
Ljud ut top right (Inställningar's list for the radio; for Spotify a plain "JBL-högtalaren", since Soloist always
plays on the speaker); the volume across the bottom, the sheet's value. The volume slider keeps its drags (`gesture_bubble: false`; a drag once
swiped to another tab). The quiet speaker left of it and the loud one right of the percentage step one 5 % stop
down or up (`mp_vol_down`/`mp_vol_up` -> `musicpage_step` -> `panel_music::volume_step`, then the release's commit;
Joel, 2026-10-29). With nothing on the speaker it is the panel's own volume and saves the day volume, so a test
that drags it puts it back by dragging it again (a Home Assistant `volume_set` leaves the saved day volume as the
drag left it). A tap beside the controls goes back; swipes
move along the tabs. With nothing playing a short hint takes the column's place. The five tabs share the baked
bar's unchanged 448 px with widths from their labels (`gen.TAB_WIDTHS`).

No music metadata, stream URLs or state are persisted in the device's flash.
The native state trigger reacts to music PLAYING/PAUSED/IDLE. ANNOUNCING preserves
existing music but never creates a music session; Jarvis's answers, chimes,
reminders and wakeups do not appear as music. The widgets close when navigating
away or entering settings/voice through the existing settings-close action.

`ha/custom_components/jarvis_agent/media.py` publishes
`sensor.jarvis_uppspelning` and the bounded `wire` attribute. It listens to HA
player/service events rather than polling or making model/API calls. ESPHome's
HA player reports no title, so `radio.py` includes the channel and Sveriges Radio
in standard `play_media.extra.metadata`. The sensor retains these as pending
request metadata, but only actual playing/paused state makes the row visible.
Stop/idle clears old source metadata; starting unrelated local music cannot bring
back an old P3 label. A restart while music already plays can show a generic title
until the next metadata-bearing request, rather than inventing a channel name.
`radio_metadata.py` polls SR's official programme/playlist endpoints in the
background about every 30 seconds while playing. Playback never waits for these
requests. The row shows the current song and the sheet shows its artist; when SR
has no current song it shows the full programme heading, including a separate
presenter subtitle (for example `P3 med Juan Havana`). A presenter already in the
title is not appended again. Artist metadata remains the song artist during music.
The source
remains the station name, and the channel logo is retained. Programme/description
are also available as `radio_programme`/`radio_description` HA attributes.
Only timestamped current entries are displayed. `previoussong` is never presented
as live; expired songs fall back to the programme, then channel. Responses are
bounded at 256 KiB each; cached documents expire after 90 seconds on failure.
Pause freezes the visible metadata and cancels polling; resume fetches again.
Stop/source changes cancel polling and discard the station's old data.

If an HA Music Assistant player reports `active_queue` equal to the physical
panel's entity, its current title/artist and feature flags supply the row and
queue-level controls. Another room's player cannot match. The source is labelled
Spotify only if its reported media URI identifies Spotify; otherwise it says
Musik. This supports the later Spotify installation without pretending it is
already active. A music source without a queue has no previous/next controls.

Controls use `jarvis_agent.media_control` with only toggle, stop, next or previous.
The target is resolved server-side and cannot be supplied by the panel/model.
Pause/resume use explicit commands against the latest state. The physical stop
first sends native STOP with `announcement=false`, so stopping audio works even
if HA is disconnected and never stops Jarvis's voice or alarms. A separate HA
stop targets the matching MA queue even if the local stream is already idle.
Thus local idle cannot accidentally redirect a queued stop to just the raw player.
When HA is unavailable the device can stop its local stream; confirming a remote
queue stop still requires HA. Normal controls wait for observed state, and errors
open the sheet with a visible message. A dropped HA response releases the pending
button lock after eight seconds (including device-clock wraparound), without
replaying the action. No control is automatically retried.

The music volume slider uses the existing saved day-volume setting and player
volume, with the same five-percent detents. It deliberately does not reuse the
settings `vol_set` script, which plays a listening chime. Opening the music sheet
reads the actual current volume. Standard night caps and later user/HA changes
retain their existing behaviour.

Free tests in `tests/test_now_playing.py` compile and exercise the actual native
parser and generated board controls. `tests/test_media_ha.py` runs with real HA
types and checks announcements, pending metadata, stale source removal, queue
targeting, service failures and unsupported skip actions. `test_radio.py` verifies
the channel metadata in the existing service call. These tests make no paid
calls and establish no acoustic result.

Install HA changes only through the checked Jarvis deployment. Install firmware
through the checked physical CI/flash workflow, preserving existing visual and
performance gates. The hidden idle widgets do not require replacing the golden
reference. The current orb-v5 artwork uses the already-reviewed idle and animation
profiles documented in [performance-profile-v5](baseline/performance-profile-v5-2026-10-25.md):
pass `--performance-baseline .panel-ci/baselines/performance-orb-v5.json` and
`--animation-baseline .panel-ci/baselines/animation-orb-v5.json` explicitly.
Before restart/audible tests save current page/volume and provenance. Joel corrected
the older test convention: 20% is for night mode, not a forced daytime test volume.
Keep his chosen daytime volume, and restore only still-owned test changes. For the actual UI test,
start an explicitly selected radio channel, inspect the new row, open the sheet,
pause/resume, stop, and verify the HA player becomes idle and the row disappears.
Keep raw snapshots/timings private in `.panel-ci`.

## Diagnosed reference mismatch during installation

The installation at `.panel-ci/run-20261026-113008-660870/` passed 614 local tests,
configuration, normal OTA/software boot, physical smoke, synthetic modes and all
14 visual scenes. It then stopped against the default October 24 memory reference,
which predates the production orb-v5 assets. The original failed comparison,
quarantine, firmware and timings remain unchanged. This is not a complete release
pass, and remaining physical tests must follow the existing recovery procedure.

Before this change, the saved actual panel status had 16,862,804 free PSRAM bytes
and 271,012 free internal bytes. The post-install idle sample has minimum
16,831,820 and 266,312 respectively: differences of 30,984 and 4,700 bytes, rather
than the 2.5 MB difference claimed by comparing to the older artwork profile.
The pre-install status is retained in `.panel-ci/now-playing-20261026/before-status.json`.

The compatible profile was already measured and reviewed on October 25, before
this work. An offline check of the same retained idle samples against
`performance-orb-v5.json` passes its unchanged limits. This diagnoses the wrong
profile selection; it does not replace a failed reference or establish the
remaining animation/responsiveness/soak results. See
`.panel-ci/now-playing-20261026/compatible-reference-check.json`.

The installed runtime has a positive same-boot identity, guarded restart linkage,
clean crash log and physical smoke. A recovery plan can therefore bind one further
installation of the identical runtime plus all ordinary QA gates, using the two
existing orb-v5 profile files. The [recovery runbook](restart-crash.md) uses
Joel's standing authorization of October 26 and seals each checked plan
automatically. No additional confirmation is required. Quarantine remains until
the ordinary gates establish a complete release verification.

## Isolated host status delay after recovery

The single recovery installation passed its guarded software boot, crash logs,
physical smoke, all fourteen visual scenes, memory and animation comparisons.
It then failed only the host `status_roundtrip_ms` comparison: p95 292.526 ms
against the unchanged 120.850 ms limit. Device release-to-redraw p95 was
45.647 ms and animation jank was zero. The failed run is retained at
`.panel-ci/run-20261026-115258-054804`; it remains failed.

Joel authorized a fixed two-case diagnostic without restart or audio. The same
20-tab/10-second-animation protocol passed without the native log subscriber
(status RTT p50 22.431, p95 62.464, p99/max 64.350 ms), and with that subscriber
(p50 24.334, p95 67.505, p99/max 73.139 ms). Both cases used the identical boot,
restored the page and preserved all samples in
`.panel-ci/now-playing-20261026/transport-ab`. This does not establish the cause
of the earlier delay or prove a release pass; it does not implicate log collection.

The harness permits one complete verification on that same healthy installed
boot, with no build, upload, restart or publication. It requires the sealed
single-attempt recovery lineage, unchanged runtime/artifacts, the isolated host
failure and both complete same-boot A/B reports under unchanged policy. It
atomically consumes a continuation before physical QA; any failure or interruption
keeps quarantine and prevents another continuation. All normal physical gates
run again. Original failures and slow samples remain intact.

## Completed physical verification, October 26

`.panel-ci/run-20261026-121641-638737/report.json` passed every ordinary physical
QA gate on the same installed boot, without another upload/restart: 618 free
local tests and configuration validation, physical smoke, synthetic modes,
14 unchanged visual scenes, idle/animation comparisons, responsiveness, production
60 fps capacity, the 120-second soak and clean runtime logs. The prior HA/server
deployment passed 73 real HA candidate tests. The original failures remain failed
and retained; the bounded same-boot verification archived and resolved quarantine.
Release-to-redraw p50/p95/p99/max was 49.832/55.091/55.095/55.095 ms; host status
RTT was 21.784/66.037/72.082/72.082 ms. Animation jank was zero.

Real P3 playback then verified the compact row and larger modal, disabled radio
skip buttons, physical pause (HA paused + play icon), physical resume (HA playing
+ pause icon) and physical stop (HA idle; both row and modal hidden). Voice stayed
idle and the selected volume remained 65%. Playback was stopped and the original
Gå hemifrån page preserved. Evidence is in
`.panel-ci/now-playing-20261026/radio-ui-verification.json` and the adjacent
screenshots, JSONL input/results and HA state snapshots. This records the text-only release before the subsequent artwork update.


## Artwork and live-radio verification, October 26

Firmware `.panel-ci/run-20261026-125518-572639/report.json` passed the complete
physical gates after correcting thumbnail hit testing, including unchanged
visual/performance references, production 60 fps capacity, 120-second soak and
normal guarded software boot. The preceding artwork run passed too; its real
UI test then discovered the clipping object's default clickable flag. The
source correction makes both clipping container and image nonclickable. A real
tap at the freshly inspected thumbnail centre subsequently opened the sheet.

The later HA/server-only metadata deployment passed 622 local and 80 HA tests.
Real SR data displayed `P3 med` and `Juan Havana`, with the actual P3 logo in
both views. Physical pause, resume and stop passed; stop hid both views, left
music/voice idle and preserved 65% volume and the original page. Screenshots,
HA states and JSONL input/results are retained under
`.panel-ci/artwork-20261026/final-*`; `final-verification.json` links the firmware
report. Live Spotify album art remains untested because Spotify is not enabled.

The first 12-second diagnostic for live SR text timed out, and its failure is
retained. A later read of the same playback confirmed current programme metadata
and artwork. This does not establish a 12-second metadata guarantee: initial
playback shows the station immediately; a failed SR request retries on the bounded
30-second cadence. The stream never waits for that lookup. The retained before
responsiveness measurement also failed only host status RTT; physical release
p95 was 52.107 ms with zero animation jank. Both firmware QA runs passed the
unchanged responsiveness gate. No slow samples or references were replaced.


## Presenter/artist visibility in the compact row

The October 26 live screenshot reproduced `P3 med` without its available
`Juan Havana` subtitle: the subtitle reached the existing wire artist field and
large sheet, but the compact caption only displayed station/playback state.
`State::caption()` now places presenter/artist beside the source in the compact
row. The title remains on its own line. Native state tests cover programme,
Spotify artist, paused-with-artist and missing-artist cases.

The accompanying SR reply contained a previous song but no current song. That
remains a programme/presenter display, rather than relabelling an old track as
current. Current song metadata continues to replace title/artist when available.
