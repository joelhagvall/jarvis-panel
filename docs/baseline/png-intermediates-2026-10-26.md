# Intermediate PNG compression: cold pipeline profile, 2026-10-26

A cProfile run of the five cold image stages on Jarvis showed that PNG
DEFLATE work, not Python, imports or process startup, dominated new-picture
preparation. `optimize=True` on the voice backdrop alone cost 2.5–3.3 s.
That file and the baked `bg`/`art` sources are intermediates: `panel_image`
re-encodes them as the RGB565 PNG/QOI pair the panel downloads.
They now use `compress_level=1`. The device-facing `panel_image` output keeps
its encoding unchanged.

## Method

Same five fresh processes as production (`server/agents/background.py`):
bake+frame, voice frosting, three RGB565 conversions. Installed live code
(identical to HEAD `ae99ea9`) versus a copy with only `bake.py` and
`voice_background.py` changed, production venv, `nice -n 19`, three repetitions
of almond, starry and the saved NASA image, throttling `0x0` throughout.
Raw results, profiles and the harness are kept privately in
`.panel-ci/png-intermediate-20261026/`.

Per process, interpreter start plus NumPy/Pillow imports cost about 0.16 s,
about 0.8 s over the pipeline. Merging processes would save little.

## Results (medians, seconds)

| Input | Wall before | Wall after | CPU before | CPU after | voice stage before → after |
| --- | ---: | ---: | ---: | ---: | --- |
| almond | 7.31 | 4.34 | 8.45 | 5.49 | 2.79 → 0.28 |
| starry | 7.89 | 4.38 | 8.99 | 5.52 | 3.50 → 0.29 |
| NASA | 7.17 | 4.26 | 8.32 | 5.39 | 2.69 → 0.28 |

The bake stage fell from 2.6–2.9 s to 2.3–2.4 s. Peak RSS went from 186 to
184 MiB. All 108 comparisons match: pixels for every intermediate and device
PNG, and exact bytes for every device PNG and QOI.
`tests/test_intermediate_png.py` guards the compression choice and pixels.

## Follow-up: the blur was memory-bound, not CPU-bound

After the change, the Rust Gaussian blur was the largest single cost: 1.44 s across
seven calls in the bake stage. The assumption that threading would help was
**wrong**. With the original tap-outer loop, each tap streamed the whole ~5 MB
buffer through DRAM. Four threads made the blur slower (1.44 → 1.75 s) and doubled
pipeline CPU. Blur-only medians by thread cap: 1 thread 1.42 s, 2 threads 1.67 s,
4 threads 1.75 s.

Looping rows outermost and taps innermost keeps one accumulator row in cache. Every
value keeps its tap order and per-tap rounding, so output is bit-identical. Blur-only
medians: 0.30 s with one thread, 0.17 s with two. The installed version caps the
blur at two threads; four gave no extra wall time for about 0.3 s more CPU.

Full cold pipeline on Jarvis, medians of six samples per variant in ABCDDCBA order,
wall / CPU seconds:

| Input | Before | Row order, 1 thread | Row order, 2 threads |
| --- | --- | --- | --- |
| almond | 4.30 / 5.44 | 3.19 / 4.32 | 3.04 / 4.35 |
| NASA | 4.30 / 5.40 | 3.12 / 4.25 | 2.98 / 4.28 |
| starry | 4.42 / 5.55 | 3.31 / 4.45 | 3.17 / 4.48 |

Peak RSS stayed at 184 MiB. Throttling was `0x0`; load average 0.6–2.8, with other
deploy tests on Jarvis at the same time. 1,296 comparisons across both sessions
(intermediate pixels, device PNG/QOI bytes) found no difference. Raw evidence:
`.panel-ci/blur-threads-20261026/`. Rust tests compare threaded bands bitwise with one
thread across edge shapes and kernels; `tests/test_blur.py` covers the threaded path
against NumPy.
