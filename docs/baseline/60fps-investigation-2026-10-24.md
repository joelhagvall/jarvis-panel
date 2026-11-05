# 60 fps and touch investigation, 2026-10-24

The fresh pre-change responsiveness run passed against the accepted prefetch reference:
`/tmp/hallpanel-60-before/responsiveness.json`. All timings remain software measurements.

## Bottlenecks and first controlled candidate

The existing listening timeline has 64 images over 2600 ms; changing timer resolution
alone cannot produce a new authored image every 16.67 ms. Display timing is configured
as 52 MHz / ((1024 + 160 + 10 + 160) * (600 + 23 + 1 + 12)), approximately 60.385 Hz.
This calculated scan rate is not an optical measurement.

The first candidate adds independent raw rings for layout/buffer synchronization,
render work, display-flush waits and flush submission. It uses a 1 ms main/refresh/
animation period, 5 ms GT911 polling and a 5 ms LVGL input-read period. The remote
pointer already reads synchronously, so reduced physical scan periods cannot be
claimed as a measured improvement to GT911 latency without real physical input.

`animation_60` is a bounded diagnostic that runs the same images faster. It restores
the original duration on exit, lease expiry and fixture cleanup. It is a throughput
stress test, not a production animation at the original pace. `tools/panel throughput`
retains raw samples and reports whether 58+ redraws/s, p95 <=20 ms, max <=33.4 ms and
at most 1% intervals over 25 ms were observed. The existing v2 workload and policy stay
unchanged and remain the release gate.

## Preserved failed gate and bounded recovery diagnosis

`.panel-ci/run-20261024-191321-223979/` passed local checks, build, boot/identity/log
review, physical navigation, synthetic modes and all 14 visual scenes. It stopped at
the unchanged screenshot performance gate. Its first 140,707-byte JPEG took
9148.3 ms: 111.79 ms to headers, 9036.37 ms for the body and 26,855 microseconds for
the framebuffer copy. Four subsequent captures took 133.9–175.0 ms with copies of
26,281–27,122 microseconds. The 4-second ceiling and original measured reference
remain unchanged; the failed report and quarantine are preserved.

A passive twelve-capture probe on the same firmware/boot, without navigation or
setting changes, measured median 150.189 ms and p95/max 1425.878 ms. Evidence is
`/tmp/hallpanel-60-transport/probe.json`. Passive phase telemetry is in the adjacent
`status.json`; its mixed scene history includes render work near 75 ms at p95 and
flush submission below 0.6 ms. This history is diagnostic, not a fresh fixed workload.
It supports investigating rendering for navigation, while the failed JPEG was a body
delivery stall. It does not identify the AP, Wi-Fi radio or SDIO as the physical cause.

The bounded recovery candidate keeps TCP_NODELAY for small JSON control/status replies,
and allows header coalescing for JPEG/raw bulk captures. Previously all accepted
sockets forced TCP_NODELAY. This is a targeted transport experiment; only repeated
unchanged physical gates can establish acceptance. No socket/window limit, maximum,
slow sample or baseline is removed. There is no automatic input retry.

The same candidate tests internal-RAM staging for large contiguous opaque RGB565
copies. LVGL currently performs the image copy row by row between PSRAM regions.
A fixed 16 KiB internal buffer groups reads before writes. Only contiguous full-width
opaque copies use the hook; crops, strided images, alpha masks and allocation failure
retain LVGL's native path. No PPA fill path, screen clock, displayed pixels or display
buffer ownership changes. The actual hook is compiled in an ASan/UBSan regression
covering all 4096 source/destination alignment pairs and sentinel bounds.

Recovery is limited to two installations of the same frozen locally checked candidate.
Every normal physical, visual, memory, responsiveness, screenshot and log gate remains
mandatory. A failed or incomplete attempt stops the plan. Joel's explicit session-wide
authorization applies; no additional approval question is needed. Firmware timing and
the 60-update/s stress test can be assessed after both complete passes resolve quarantine.

## First recovery result and control-response correction

The first recovery plan stopped on its first attempt and was not reused.
`.panel-ci/run-20261024-193143-640830/` passed screenshots (p95/max 334.8 ms),
visual, memory, animation and device input limits. It failed status HTTP p95:
518.879 ms versus the preserved 117.563 ms baseline and unchanged 50 ms margin.
The slowest response took 971.517 ms, almost entirely before headers. Main-task
snapshot p95 was 348 microseconds, worker p95 37.944 ms and radio p95 30.563 ms.
The accepted animation remained 25.01 redraws/s, p95 interval 41.535 ms and no
interval over 50 ms. Injected press median was 8.654 ms; release p95 was 83.399 ms.
These failed-run timings remain diagnostic and are not promoted to a baseline.

Reading the pinned IDF 5.5.5 `httpd_resp_send` implementation confirmed that
each extra response header is written as four separate send calls, followed by
another CRLF and the body. The next bounded candidate preserves IDF's exact
HTTP formatting, buffers only JSON control responses on the HTTP worker, and
sends the resulting header/body together with TCP_NODELAY. JPEG/raw captures
retain their existing streaming path with coalescing. The shared per-server
buffer pointer is used only on its own serial HTTP task; request input is not
retried. A native regression verifies exact wire bytes across partial writes,
EINTR and disconnects. Status also exposes UI queue time separately from
snapshot execution, worker time and host HTTP phases.

A new frozen local build and a new two-attempt plan are required. The previous
plan's failed ledger, all limits, references and golden images remain intact.

## Stable transport verification and first 60-update/s observation

Both complete recovery runs passed on build `0f57f2f9e71433a5406f290aef7e1ccb`:
`.panel-ci/run-20261024-194754-577794/` and
`.panel-ci/run-20261024-195209-699388/`. The plan was resolved after both passes.
Status HTTP p95 was 142.685 and 134.243 ms. UI queue p95 was 72.497 and 75.883 ms,
separate from sub-millisecond snapshot work and the approximately 30 ms radio RPC.
The first run retained a 427.996 ms status maximum; the second maximum was 140.256 ms.
Neither maximum was discarded. Normal animation p95 cadence was 41.888 and 41.690 ms;
there were no intervals over 50 ms. Touch p95 remained approximately 18 ms for press
and 79–82 ms for release; the earlier improved median was not consistent between runs.

The subsequent bounded stress workload did **not** meet 60 fps. It observed 39.898
redraws/s, cadence p50/p95/p99/max 25.941 / 33.394 / 33.814 / 48.567 ms, and 50.995%
of intervals over the stress test's 25 ms threshold. Refresh-work p95 was 31.519 ms,
including render p95 19.076 ms and display-flush wait p95 12.997 ms. Prefetch hit rate
was only 48.883% (197 hits and 206 fallbacks), with zero decode errors. Raw evidence:
`/tmp/hallpanel-60-stress-first/throughput.json`. These are distinct from the normal
25 fps workload; none of the release thresholds were changed.

The next candidate predicts two upcoming images in its existing three buffers.
One packed atomic request carries both indices, avoiding an inconsistent pair while
the main task advances. A skipped source image can therefore hit the second prediction
instead of decoding synchronously. Ownership still prevents overwriting a pinned image;
memory residency, exact pixels, original animation durations and native miss decoding
stay unchanged. An independent source-change counter distinguishes a genuinely new
animation image from another redraw of the same image. Stress acceptance now also
requires at least 58 observed source changes/s; redraw counts alone cannot meet it.

Normal CI now collects the same 60-update/s capacity diagnostic after the unchanged
responsiveness gate. Incomplete collection or failed restoration stops CI; an unmet
experimental throughput target remains explicit and is not described as a 60 fps pass.
No failed throughput result becomes an accepted performance baseline.

A fresh unchanged pre-install run retained a 176.160 ms status p95, failing the
existing 167.563 ms relative ceiling. Its raw data is
`/tmp/hallpanel-two-ahead-before/responsiveness.json`. A diagnostic 4 KiB receive-window
run of the same workload passed with p95 127.036 ms and max 175.585 ms, but the shared
16 KiB default is retained; one smaller-window run does not establish a robust fix.

The new candidate samples RSSI at most once/s on the HTTP task and exposes its age
as `wifi_rssi_age_ms`. UI state, timing rings and UI queue delay still get a fresh
snapshot on every request. `status_radio_us` measures actual RPC work for that request
and is zero when no RPC occurs. Failed queries stay unavailable and disconnected
state clears the cache. The regression tests cover expiry, failure and clock wrap.

## Proven capacity and original-speed 60 fps candidate

`.panel-ci/run-20261024-200506-745874/` passed all normal gates and the separate
capacity target on build `d51f003eb4d3212dfe6cf25254d4d932`. The stress test observed
60.378 redraws/s and 59.487 actual source changes/s. Cadence p50/p95/p99/max was
16.549 / 17.644 / 17.997 / 18.386 ms across 604 intervals, with none over 25 ms.
Prefetch had 605 hits, zero fallbacks and zero decode errors. Render p95 was
13.047 ms and display wait p95 fell to 2.920 ms. Fixed-workload press/release
p95 was 18.005 / 79.101 ms; status HTTP p95 was 110.755 ms. This establishes
capacity with an accelerated timeline, not the final animation quality.

The next candidate retains the original durations (2600 / 850 / 650 / 2000 / 600 ms)
and creates 156 / 51 / 39 / 120 / 36 virtual images for listen, think, morph,
speak and settle. It interpolates RGB565 channels and the separate A8 plane on
core 0. Four output slots plus two cached original inputs occupy 871,200 bytes;
descriptor/recipe tables also use PSRAM. Only the original compressed 143-frame
atlas is downloaded. Endpoints are exact, and a miss computes the requested image
synchronously instead of substituting another image. Three future indices fit
in one atomic request. Allocation or decode failures remain errors.

The native diagnostic compiles the actual installer, decoder and timeline switch.
It passed 1619 byte-for-byte comparisons against native LVGL source pixels and an
independent interpolation oracle, plus duration and native/production restoration
assertions under ASan/UBSan. Evidence: `.panel-ci/virtual-60-native.json`.
Physical timing, memory and all visual gates must still pass before acceptance.

The existing v2 benchmark explicitly selects the original 25 fps image lists,
preserving its measured workload and reference. Golden scenes freeze original
images too. Cleanup restores the production image lists. The distinct production
measurement is `orb-60-virtual-10s-v1`; CI now refuses a firmware declaring a
60 fps production target unless its actual source-change and cadence target passes.

## Virtual timeline failure and concrete correction

The first virtual candidate stopped at its mandatory production target in
`.panel-ci/run-20261024-202517-035449/`. All previous gates passed, but the capacity
test reported 30.191 redraws/s, zero source changes, zero prefetch hits and 302
synchronous fallbacks. The target failure and quarantine are retained.

The pinned LVGL 9.4 `lv_animimg_t.pic_count` is **int8_t**, while its public getter
returns uint8_t. A 156-image sequence therefore became negative internally; the
standard animation callback clamped to a wrong index. Byte-by-byte decoder tests
did not exercise the live timer and had not caught that bridge error.

The correction keeps the internal count <=127 and uses public `lv_animimg_get_anim`,
`lv_anim_set_exec_cb` and `lv_anim_set_values` to address the complete timeline.
The original callback and image counts are restored for the native benchmark.
`source_count` supplies the actual virtual count to diagnostic duration calculation.
The native regression now also runs actual LVGL timers over multiple cycles,
checks that every displayed source belongs to the correct virtual sequence and
observes >140 distinct source descriptors. It saw 155 distinct sources and still
passed all 1619 exact image comparisons. The correction is recorded in
`.panel-ci/virtual-60-animation-native.json` with source hashes.

The pre-install run on the previously verified two-ahead firmware also retained a
sporadic 136.949 ms interval and status HTTP p95 318.524 ms, in
`/tmp/hallpanel-virtual-before/responsiveness.json`. Refresh work itself peaked at
20.796 ms; that observation is not claimed as caused or fixed by virtual images.

## Bounded AXI-GDMA copy experiment for navigation and capture overhead

The recovery candidate also tests IDF's AXI async-memcpy driver for sufficiently
large aligned RAM copies. It uses 16-byte bursts, a one-transaction backlog and
at most 256 KiB per transfer, waiting for completion before buffer reuse. This
preserves the existing two off-screen/visible buffers and their scanout ownership.
Full-width RGB565 images, compatible full-width buffer synchronization and immutable
presented-frame captures can use the path. Other regions retain native copying.
The fixed internal staging buffer is reduced from 16 to 8 KiB.

Driver completion is semaphore-driven. An enqueue error disables acceleration and
the caller can safely copy normally; reported DMA errors fail responsiveness.
Counters expose ready state, completed copies, bytes, elapsed work and staging use.
Runtime log review now also rejects LCD underrun and frame-presentation errors,
which framebuffer screenshots cannot establish on their own. The native chunk
regression verifies bounds, alignment, exact bytes and fail-stop after a partial
transfer; physical DMA/cache behavior must still pass every hardware/visual gate.

This is a new source-bound, two-install recovery candidate. The old failed plan
and artifacts are preserved. No timing ceiling, memory tolerance, screenshot
baseline or golden reference is relaxed or replaced.

## Measured interpolation bottleneck and SRAM experiment

The corrected live timeline ran in `.panel-ci/run-20261024-205326-380345/`.
All existing physical gates passed, but the mandatory production target failed:
20.1625 actual source changes/s, interval p95/max 51.126 / 53.528 ms and 100% of
intervals over 25 ms. Prefetch had zero hits and 201 synchronous fallbacks.
Sampled worker preparation cost was 15.244–28.749 ms. Render p95 reached 44.081 ms.
The worker could not finish interpolated PSRAM images before they were needed;
fallback work then compounded the problem. The failed attempt stops its plan.

The next candidate separates color and alpha streams, combines independent red/blue
integer lanes with exact rounding, and tests a 6 KiB internal staging workspace.
The original, linear and staged methods are compared byte-for-byte. At startup,
core 0 measures all three in three round-robin repetitions against actual decoded
source images in PSRAM. All nine raw durations are exposed as `orb_probe_0_us`
through `orb_probe_8_us`; selection uses the lowest median, and a mismatch is an
error. The worker owns its workspace; synchronous fallback gets private scratch.
No LVGL reader can access the unpublished probe outputs. Allocation failure keeps
the exact linear implementation available. The optional native LVGL diagnostic
again passed 1619 exact image comparisons and saw 155 live virtual source images
in `.panel-ci/virtual-60-staged-native.json`.

The same failed hardware run reported zero DMA errors, but 59 large CPU staging
copies. The pinned IDF GDMA driver requires burst alignment for RX, while TX starts
at byte alignment unless external-memory encryption is enabled. The conservative
helper now accepts 4-byte source alignment on unencrypted P4 and retains 16-byte
destination/length alignment. Encrypted external memory keeps the SOC's 16-byte
source restriction. Regression checks include source+4 acceptance and rejection
of source+1, invalid alignment and a partially failed bounded transfer. The actual
P4 translation unit passes the pinned cross-compiler syntax check.

The SRAM candidate also stopped at its mandatory 60 fps gate in
`.panel-ci/run-20261024-211347-484368/`. The device selected staged interpolation;
the nine original/linear/staged timings were 21190/15243/13746,
17523/17449/11994 and 13689/11992/11284 microseconds. Preparation including raw
decompression still sampled 11.473–26.464 ms (median 15.784 ms). Production source
changes stayed at 20.060/s, with zero prefetch hits. All existing gates passed.
The DMA alignment correction eliminated CPU staging copies, with zero DMA errors,
and reduced navigation release p95 from 82.086 to 76.270 ms. Press p95 was 17.909 ms.
Status HTTP p95 was 104.501 ms. These improvements do not satisfy production 60 fps.

Adjacent raw listening images actually have about 77.6–80.1% identical two-pixel
color words and 65.8–79.6% identical four-byte alpha words in the sampled pairs.
The next exact method copies identical words directly and mixes four A8 channels
in independent 16-bit integer lanes. This avoids most unnecessary per-channel
arithmetic, with unchanged rounding and full source images. The worker also stops
preparing the rest of a stale prediction list when a newer visible frame requests
different predictions. A currently preparing image is always completed before
publication; slot ownership and synchronous exact fallback stay unchanged.
The free regression checks all 257 weights, equal and different words, unaligned
input/output offsets, short alpha tails and chunk boundaries with guard bytes.

The sparse-arithmetic candidate in `.panel-ci/run-20261024-212404-388191/` passed
all older gates but retained only 1 hit and 202 fallbacks in its production test.
It produced 20.108 actual source changes/s. The boot probe selected direct linear
mixing, whose median was 11.127 ms versus 15.243 ms in the preceding experiment;
the worker nevertheless completed 566 predictions in the observation window.

This suggests a cold-start scheduling trap as well as preparation cost: a virtual
miss synchronously reconstructs two raw images, then renders. The next callback
arrives roughly three virtual frames later, but the worker starts with frame+1
and only completes frame+3 around its deadline. The next candidate predicts its
first needed future index from the measured synchronous miss duration plus one
16.67 ms render interval (rounded, bounded to 1–8). Cache hits retain ordinary
next-three prediction. It always renders the actual requested source on a miss;
prediction never substitutes another image or skips telemetry. `orb_prefetch_miss_lead_max`
records its largest estimate. Last raw-unpack and interpolation costs are exposed
separately as diagnostics, alongside total preparation time. These are last-worker
observations, not complete per-frame distributions or an optical timing claim.

The first local build of the lead correction stopped on P4's uint32_t/unsigned
type mismatch in `std::min`; it was not installed. Explicit uint32_t template
arguments fix the contract. A pinned-P4 compiler syntax check now covers the
actual prefetch header before the full rebuild.

Disassembling the preceding P4 `interpolate_colors` revealed another concrete
cost: a four-byte memcpy with unknown alignment expanded into byte loads, stack
stores, stack reloads and byte stores even for identical words. The next candidate
checks every input/output address before an aligned pair loop. Aligned memcpy
uses `__builtin_assume_aligned` while preserving C++ aliasing rules. Unaligned
buffers and short tails retain the byte implementation. The pinned P4 compiler's
new assembly uses actual word loads/stores for that loop. All 257 weights, offset
alignments and guard bytes remain covered, and native LVGL comparison remains
mandatory. This is an instruction-path correction, not a relaxed visual target.

The word/lead correction reached 51.844 actual source changes/s and 52.230
redraws/s in `.panel-ci/run-20261024-213937-420740/`, with 511 hits and 16
fallbacks (96.964%). It still failed the unchanged production target. Cadence
p95/max was 30.861/48.121 ms, with 13.308% over 25 ms. Render median/p95 was
14.419/15.721 ms, but display wait p95 was 13.785 ms. Those combined costs miss
the next presentation deadline. Sampled worker mixing median was 12.028 ms
under load; a newly needed raw input added up to 13.000 ms. The lead estimate
reached three frames, and all older physical gates passed. This is a real gain
from the 20 fps trap, not an accepted 60 fps result.

## Cached sparse pairs within the original memory budget

The next method bulk-copies the first RGB565A8 image and patches only changed
four-byte words. A bounded worker-owned list caches offsets and second-image words
for the raw pair; each pair serves multiple virtual weights. The first-image value
comes from the newly copied destination, avoiding another cached field. The list
holds 16,384 eight-byte records (131,072 bytes). All 143 adjacent raw pairs in the
current atlas need at most 12,868 records; an overflow always falls back to the
exact linear implementation rather than truncating changes.

The output pool returns to three slots with two future predictions, already proven
by the earlier raw 60 fps capacity run. Four output slots would exceed the unchanged
relative memory gate after adding the list. Three outputs, two original input
images and the list total 857,072 PSRAM bytes, 14,128 fewer than the preceding
candidate. A three-slot pool must never prepare three future images while the
current image is pinned: the third would evict a nearer needed image. This
candidate therefore submits only two future indices. Ownership rules stay intact.

The boot experiment now measures four methods in three round-robin repetitions
and retains all twelve timings. Its diagnostic ranking uses total elapsed time, including
the sparse method's initial list build and its two reused-pair calls. Every output
is compared against the original before its probe slot is reused. The caller
snapshots allocation needs once so a concurrent startup method selection cannot
make its private fallback workspace too small. The native tests cover cache reuse,
pair/size invalidation, capacity overflow, all weights, alignments and guard bytes.
The actual prefetch header also passes the pinned P4 compiler's syntax check.
Production is deliberately fixed to sparse mixing for this experiment; startup
cache noise must not switch the live method between installations. The fastest
startup method is reported separately. Allocation failure retains exact linear
mixing, and any pixel mismatch is an error. A local-only build was interrupted
before installation to make this choice deterministic.

The first sparse hardware candidate stopped at the production target in
`.panel-ci/run-20261024-215636-872188/`: 25.276 source changes/s, 82 hits and
171 fallbacks (32.411%). Warm sparse startup calls were 5.086 and 5.997 ms,
but cold misses returned under load. The smaller pool exposed a replacement flaw:
after skipped animation indices, FIFO cursor order need not match frame age.
Preparing the second future image could evict the first already-ready future image.
A targeted three-slot regression recreates that exact ordering (ready 10, old 8,
pinned 9) and requires preparing 11 to retain 10. Reservation now protects both
members of the active prediction request. It refuses to overwrite a pinned or
nearer ready image if no eligible slot remains; the next request retries preparation,
while any actual UI miss still computes the correct pixels synchronously.

## Evidence retention correction

The pre-existing `prune_runs` deleted old completed directories beyond its hot
history budget, including failed CI runs. Several earlier physical CI raw files
referenced above were therefore automatically lost during this investigation;
their numbers remain documented, but those summaries cannot recreate raw samples.
Independent `/tmp` before measurements and the recent runs still exist. This
corrects earlier statements that every failed CI artifact had been retained.

Remaining completed runs are now archived under `.panel-ci/history/` with stable
symlinks at their original paths. The runner now uses the same behavior permanently
instead of deleting raw measurements. The active run, quarantine evidence,
baselines and locks remain protected. A regression retains explicitly slow samples
through repeated pruning and checks that archived evidence remains readable at
both the original and history paths. History storage is retained, not bounded by
the hot-run budget. The current candidate includes this host correction.

## Controlled method comparison and numeric scheduling trace

The protected sparse candidate passed the older gates but failed production at
23.030 source changes/s, 52 hits and 181 fallbacks in
`.panel-ci/run-20261024-221141-768778/`. The reservation regression is fixed, but
that alone does not establish the cause of the physical misses. Its preceding
preflight stopped before any installation because automatic night mode had begun
at 22:00; a guarded normal touch woke it without changing night times. Night mode
must be restored when this session's testing finishes.

The next firmware supports explicitly bounded `animation_60_linear` and
`animation_60_staged` fixtures alongside the mandatory production fixture.
Both retain production durations, source lists, background and allocated memory.
Every exit restores the previous method, duration, timeline and page. Busy/voice
guards and 30-second expiry remain. `tools/panel ci --compare-orb-methods` records
both A/B measurements before the independent mandatory production gate; neither
diagnostic can make a failed production result pass. `tools/panel throughput
--method linear|staged` is also available outside quarantine.

`orb_prefetch_trace` keeps sixteen main-task decoder events with a monotonic total.
Each compact numeric event contains `[at_us, source_index, hit, fallback_us,
prediction_1, prediction_2, slot_0, slot_1, slot_2]`. Slots encode `index*8+state`;
states 0/1/2/3 are EMPTY/WRITING/READY/READING, and 4 denotes a changing snapshot.
Index 1023 means no frame. Slot state is sampled around its index; it is diagnostic
concurrent state, not a transactionally frozen worker. Status captures an immutable
copy on the UI task and formats it on the HTTP worker. The host retains each trace
snapshot and its total in the private raw report; older events can roll out of
this short diagnostic ring. The independent 128-entry timing rings still enforce
complete fresh timing collection, including slow samples.

The native decoder diagnostic now checks all three selectable methods against
the independent oracle (4857 exact image comparisons), plus the live timeline.
The generated callback regression verifies method/duration/page restoration even
when switching between the A/B fixtures. The pinned P4 header syntax check passes.

## Measured A/B result and bounded PPA rectangle experiment

`.panel-ci/run-20261024-222821-806880/` measured all three methods with identical
timelines, painting and buffer residency. Direct/staged/sparse production reached
34.579 / 30.746 / 24.509 source changes/s, with 61.272 / 51.923 / 29.150% prefetch
hits. All failed the unchanged 60 fps target. The trace commonly shows the actual
requested image still WRITING; some hit-to-next-request jumps exceed the two-frame
prediction window because rendering plus display waits takes multiple intervals.
These are controlled measurements, not inferred from startup-only rankings.

The next candidate fixes production to the best live method (direct) and retains
its three-frame request across worker sleeps. LVGL decoder close notifies the worker
when a pinned source becomes reusable. The third future image can then be prepared
after release, while the other two READY images remain protected. No fourth output
buffer or greater resident pixel budget is required. A new UI request supersedes
old pending predictions; already-published or pinned images are not recomputed.

The main rendering path also tests the P4 PPA SRM engine for bounded 1:1 RGB565
rectangle copying. It accelerates compatible opaque background crops and direct
buffer synchronization. The existing transparency, masks, opacity, clipping and
RGB565A8 compositing retain their software semantics. This is a single client,
one pending operation and 16-byte bursts; LVGL's global experimental PPA renderer
is not enabled. No display clock, scanout ownership or double-buffer contract changes.

Every rectangle is checked against source/destination dimensions and allocated
destination size. The image hook accepts only a pointer matching the active full
display buffer and reconstructs the original source origin from LVGL's relative
source/destination areas. Unsupported layouts retain native copying. Before IDF
invalidates its entire destination row window, the hook writes back those CPU rows,
including unchanged neighboring pixels. It waits for completion before reuse.
Output address, size and row stride use the pinned SDK's configured L1/L2 cache-line
alignment. The driver's alignment helper is private, so no private helper is called.
Encrypted external memory retains native copying because SRM does not support it.

The unused 8 KiB CPU staging allocation is omitted when hardware copying is ready;
an unsupported region can use LVGL's native implementation directly. PPA calls,
bytes, elapsed time and errors are exposed separately from AXI-DMA. Any reported
PPA error fails responsiveness, throughput acceptance and final health. Geometry
regressions cover clipping, untouched neighboring pixels, invalid bounds and integer
overflow. The actual P4 translation units and native pixel/ownership checks pass;
physical pixels, memory and timing must still pass every gate.

Offline precomputed-frame alternatives were also measured without changing assets:
XOR/LZ4, dictionary LZ4 and XOR/zlib estimates require about 8.66–10.47 MB including
all original reference images and 402 new images, versus the current 4.305 MB atlas.
They exceed the unchanged memory budget and were rejected. Private measurements
are `/private/tmp/hallpanel-{keyed,dictionary,zlib}-size.json`.

## Near-60 PPA result, terminal duplicate and tap transitions

`.panel-ci/run-20261024-225156-580228/` passed every older gate, with exact golden
pixels and zero PPA/DMA errors. Production reached 58.477 source changes/s and
58.881 redraws/s; cadence p50/p95/p99/max was 16.558/18.946/29.010/43.641 ms.
The 2.377% jank and maximum still failed the original 60 fps target. Linear/staged
A/B runs likewise reached 58.380/58.577 source changes/s with roughly 99% hits.
Fixed 25 fps release press/release p95 was 18.431/74.369 ms, and HTTP p95 111.035 ms.
PPA completed thousands of bounded copies with no pixel-copy errors.

The event trace locates four of production's five measured fallbacks at an exact
cycle defect: image 298 was drawn once successfully, then requested again even
though its source had not changed. Its slot had already been reused for future
145, while 143/144 were READY. The synchronous duplicate then cost 21–22 ms and
made the next cycle skip its first frames. LVGL executes terminal value `count`,
which clamps to the last image already shown. The production callback now compares
`lv_image_get_src` before changing it; equal sources do not invalidate pixels again.
All production states use this callback, while the fixed native benchmark restores
its original callbacks and workload. No duration, frame list or threshold changes.
An actual-LVGL regression clears invalidations, calls both the terminal and last
values again, and observes zero new invalidation events. It also retains all 4857
exact decoder comparisons and live-timeline checks.

The same candidate tried instant taps in the preview generator after finding its
250 ms transitions. That hypothesis was wrong for the physical board: `board.py`
already forces every physical page action to `NONE`, and ESPHome calls
`lv_screen_load` directly in that case. The generated firmware confirmed it; the
release redraw stayed around 74 ms. There was no stale generated UI input. The
preview-only experiment is reverted, preserving its existing gestures/animation.
This corrects the earlier commentary. Physical navigation time is rendering cost.

## Native-equivalent transparent-pixel fast path

`.panel-ci/run-20261024-231409-083185/` passed both A/B 60 fps targets: direct and
staged source changes were 59.980/59.978 per second, with 100% measured cache hits.
Production measured 59.573/s with zero fallbacks, but 1.349% of intervals exceeded
25 ms (limit 1%). Its cadence p50/p95/p99/max was 16.497/19.017/26.709/28.440 ms.
This remains a failed release result. All old physical gates passed; release touch
p95 was 74.395 ms. The remaining headroom problem is main rendering, not cache misses.

LVGL's RGB565 image-with-mask loop calls its RGB565 mix routine and writes the
destination even for fully transparent pixels. The bounded next hook skips those
reads/writes, directly copies fully opaque pixels, and delegates all fractional
mixing to `lv_color_16_16_mix` itself. LVGL 9.4 quantizes alpha via `(alpha+4)>>3`;
0–3 exactly preserve the destination and 252–255 exactly select the source.
The optional native diagnostic checks those thresholds for every 16-bit foreground
color against four backgrounds, and compares complete padded/strided masked images
for all alpha values with the actual native LVGL routine. Framebuffers, assets,
partial-opacity/mixed-mask handling and page behavior stay intact. A minimum area
and 16-bit alignment guard leave unsupported regions on the native path. Normal
font/fill drawing is retained; its native loop already skips zero mask pairs.
Counters report image calls, pixels, transparent/opaque shortcuts and elapsed work.

The actual native comparison passed 1,620,736 mask cases, all 4857 decoder pixel
comparisons and the duplicate-invalidation regression. A separate ASan/UBSan test
checks every alpha on padded image rows and guards outside the destination.
All 414 local tests and the P4 build passed. Physical run
`.panel-ci/run-20261024-234058-371077/` preserved exact golden pixels and passed all
gates up to responsiveness. Fixed-workload render p95 improved from 11.356 to
10.323 ms, and release p95 was 74.130 ms. However HTTP p95 446.103 ms failed the
unchanged reference. Two raw responses spent 1326.45/1384.92 ms receiving the body,
while their UI snapshots cost 346/339 us and worker work 1882/1867 us. No sample
is discarded; this is a failed release and no second attempt uses that plan.

A read-only, interleaved 96-status sample compared 16 KiB and 64 KiB host receive
buffers (`/private/tmp/hallpanel-status-buffer-1792878376152291000.json`). Their
p95/max were 59.923/88.061 and 84.284/177.419 ms respectively. Enlarging the buffer
did not help and is rejected. The SDK's 1500 ms retransmission timeout is consistent
with the body stalls, but timing alone does not prove packet loss. The next candidate
records the previous status response's socket enqueue time, payload bytes, age,
count and send errors on the HTTP task only. These are explicitly not delivery
or acknowledgment times. Header-only packet capture during the next full physical
run can distinguish transport retransmission from UI/worker work without recording
credentials or response bodies. All ordinary gates and timing thresholds remain.

## First full 60 fps pass and midnight test-contract correction

`.panel-ci/run-20261024-235220-888381/` passed every physical gate on build
`6e5499703a172efec99589db81376bbb`. Production measured 59.978 source changes/s,
59.984 redraws/s, zero fallbacks, and 100% hits. Cadence p50/p95/p99/max was
16.464/18.897/20.657/26.250 ms; jank above 25 ms was 0.669%. Render work was
12.343/13.362/13.597/14.013 ms. Host status was
22.488/53.428/61.275/61.275 ms. The fixed 25 fps workload retained its separate
protocol: press 18.0515/18.241/18.359/18.359 ms and release
74.005/75.906/76.510/76.510 ms. Its complete refresh work p99/max was
21.241/22.609 ms, higher than the initial 17.660/18.691 ms despite improved p95;
these tail samples remain in the raw report. No claim that every latency improved.
The first TCP trace (`/private/tmp/hallpanel-tcp-headers-1792878722925312000.pcap`)
contains 35,728 packets with at most 54 captured bytes each, zero panel-originated
repeated data segments, and eight host repeats across reset/readiness. Socket
enqueue maximum was 2627 us, with zero send errors. Earlier body stalls were not
reproduced; the trace does not establish their cause retroactively.

The second launch stopped before OTA because of automatic night mode and did not
claim an attempt. After a guarded wake, `.panel-ci/run-20261024-235842-266891/`
installed the identical artifact and passed functional/fault tests, then failed
visual capture at midnight: `Missing static label 'IDAG'`. The fresh UI correctly
showed `I MORGON`. `server/agents/home_status.py:calendar_panel` explicitly emits
either heading from live calendar data. It was incorrectly declared static in
visual policy v2. The failed report and its real UI/image are retained.

Policy v3 declares only `screen/5/0` dynamic, with the two exact permitted texts
and maximum content width 198 px. It still checks exact x/y/height, label type,
clickability, visibility/bounds, every other anchor, every pixel region and the
same channel/changed-pixel/geometry limits. Tests reject unknown text, missing or
clipped labels, excessive width and shifted geometry. The original reference
`.panel-visual/reference/` remains unchanged. Explicit derived reference
`.panel-visual/reference-calendar-v3/` reuses byte-identical original PNG/UI files;
its manifest records the original manifest/policy hash and recomputed contracts.
Offline comparison of the real midnight dashboard has zero layout differences.
No freshly captured pixels are substituted to make a failure pass.

The first fully passed production measurement above is the explicit 60 fps
reference, retaining its raw samples and source SHA. The checker recomputes the
original absolute limits, rejects incomplete/restoration failures, A/B/accelerated
protocols, missing samples, non-finite numbers and copy errors. It additionally
compares render/refresh p95, source rate, jank and host p95 with a fingerprinted
policy. Missing or incompatible references stop before OTA; no installation
creates or replaces one. CI and the checked flash path both enforce this gate.
New full installations must still repeat every gate with this corrected contract.

## Opaque settings-region correction (policy v4)

The first v3 installation `.panel-ci/run-20261025-001201-225931/` passed boot,
functional/fault checks and every layout contract. It failed the settings-dashboard
pixel check with 11 changed pixels, versus the unchanged maximum of 8. Inspection
of all exact coordinates and both cropped source images locates every difference
on the rounded modal's outer antialiased fringe (y=51–76). That fringe blends the
live underlying dashboard, including the now different calendar/weather. The
sheet itself is `bg_opa: COVER`, radius 28. This is outside its opaque interior.

Policy v4 compares the two settings sheets one pixel inside their original rounded
profile: include [193,52,831,500], radius 27. Dynamic exclusions and all content,
text, controls and layout checks remain. Coverage is 199219 pixels per settings
sheet, versus 201355 before (1.06% less, explicitly the outer edge). Channel
tolerance stays 8, changed-pixel limit stays 8 and geometry tolerance stays zero.
All 11 fringe differences are outside this opaque profile; the interior has zero
changed pixels. A targeted regression rejects changes immediately inside the
opaque profile while allowing the live fringe. No extra pixel noise is tolerated.

New private reference `.panel-visual/reference-calendar-v4/` derives from original
v2 PNG/UI files, byte for byte; v2 and v3 directories remain unchanged. Its manifest
records original provenance and the v4 contracts. No candidate image is promoted.
The failed v3 run and its exact differing pixels remain retained. This correction
does not change firmware rendering or any performance threshold.

## Verified installed result, October 25

The exact same candidate `2fd1677277e8f8985afba9328b5cfa05` passed complete physical
CI twice: `.panel-ci/run-20261025-002233-642549/` and
`.panel-ci/run-20261025-002814-815513/`. Both installed OTA SHA
`3e8b830f8b71a7b5bafac06b389101a31859f0acd339098b3218f20d8285319a`.
Each ran all 425 free tests/config validation, boot/restart checks, physical
navigation/settings, synthetic faults, all 14 visual scenes, idle/animation
comparisons, the fixed responsiveness workload, direct/staged diagnostics and
the mandatory production 60 fps absolute/relative gate, final health and log review.
Both passed. `.panel-ci/60fps-opaque-recovery.json.resolved.json` closes the exact
original quarantine after validating both artifacts and the unchanged source.

| Production listening orb | First full run | Second full run |
| --- | ---: | ---: |
| Actual source changes/s | 59.9782 | 60.0880 |
| Fresh frame intervals | 604 | 600 |
| Interval p50/p95/p99/max, ms | 16.424 / 18.605 / 20.328 / 26.691 | 16.506 / 18.852 / 20.343 / 26.158 |
| Intervals over 25 ms | 0.6623% | 0.3333% |
| Render p50/p95/p99/max, ms | 12.255 / 13.229 / 13.511 / 13.835 | 12.292 / 13.293 / 13.478 / 13.979 |
| Host HTTP p50/p95/p99/max, ms | 22.811 / 51.523 / 60.274 / 60.274 | 24.790 / 58.989 / 61.942 / 61.942 |
| Cache hits / fallbacks / decode errors | 605 / 0 / 0 | 601 / 0 / 0 |

The separate, identical `tabs-20-orb-10s-v2` protocol compares the initial 20-tap
measurement with all 40 taps across the two final runs. Samples are pooled only
within the same workload; no interval crosses a boot/run boundary. Source paths,
SHA256 values, merged raw arrays and summaries are saved in
`.panel-ci/investigations/60fps-20261024/comparison.json`.

| Fixed workload, ms (p50 / p95 / p99 / max) | Before | Final two runs pooled |
| --- | --- | --- |
| Remote press to software redraw | 17.575 / 17.982 / 18.229 / 18.229 | 17.819 / 18.202 / 18.338 / 18.338 |
| Remote release to software redraw | 77.907 / 80.686 / 80.778 / 80.778 | 74.725 / 77.694 / 77.940 / 77.940 |
| Native 25 fps refresh work | 12.720 / 13.282 / 17.660 / 18.691 | 9.875 / 10.658 / 16.384 / 24.438 |
| Status HTTP | 61.337 / 120.474 / 124.243 / 124.243 | 41.073 / 109.226 / 127.738 / 162.123 |

Refresh-work p95 improved 19.8%; its maximum worsened. Release p95 improved only
3.7%; press p95 is essentially unchanged/slightly higher. Host p95 improved, while
p99/max worsened. The earlier failed candidate's 1455.786 ms HTTP sample is retained
separately and its cause is not proven. These are not uniformly faster tails.
The protocol uses live calendar/weather, whose content changed at midnight; small
navigation differences cannot be attributed to code alone. Phase timings retain
buffer synchronization wait separately from rendering. No slow sample is removed.

The listening orb uses genuine intermediate RGB565+A8 images at the original
2.6-second production duration. These short steady-state measurements do not
establish every animation state or the entire UI at 60 fps. Remote-input timing
excludes physical touch sensing and optical scanout. No Tesla comparison was
physically measured. The installed panel is healthy with zero PPA/DMA/decode/send
errors, and the original night mode is restored. Source changes remain local.
