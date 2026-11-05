# Cold background preparation: NumPy bounds and a Rust sampler

The experiment compares the existing image pipeline with bounded NumPy geometry
and the same NumPy candidate with bilinear sampling in Rust. The initial release
selected bounded NumPy. Joel then explicitly chose to enable the additional Rust
sampler despite its small incremental gain. Its production source now lives in
`native/image_sampling`, with `styles/glass-v2/image_sampling.py` as the binding.
The original comparison and initial release evidence below remain unchanged.

## Workload and evidence

The `cold-image-five-sequential-processes-v1` workload runs on Jarvis with the
production venv, NumPy 2.5.4 and Pillow 13.0.0. Each run creates dashboard and
art PNGs, the frosted voice background, then RGB565 PNG and QOI variants of all
three images. The five processes run sequentially. All their imports, decoding,
cropping, encoding and startup count. Network downloads, publication and cache
hits are outside this workload. CPU seconds sum user and system time across
those processes. Peak RSS is the largest single-process peak, not their sum.

Three repetitions per image and variant rotate execution order. Inputs are the
repository's Mandelblom and Stjärnenatten JPEGs plus the saved NASA image for
2026-10-25. Temperature, throttling flags and load are retained per run. This is
a small controlled comparison of cold preparation, not a population latency
distribution or a measurement of the live server's resident memory.

Raw evidence is retained privately in `~/hallpanel-image-bench-20261025/` on
Jarvis; the Mac copy was deleted on 2026-10-27 to free disk. The first failed candidate and
the corrected candidate have separate source snapshots and results. The
unchanged source renderer is frozen in `tools/image_experiment/reference_bake.py`
from commit `88d145f94268ff94b71cbbeecb929de51504baff` for later pixel regressions.
See `tools/image_experiment/README.md` for the reproducible commands.

## Why the NumPy change is smaller

The original renderer retains five full-screen float64 geometry arrays for
each of six widgets, then samples and blends each widget across the full
screen. The candidate retains and processes only each widget's rectangle plus
a 64-pixel margin. That margin preserves the Gaussian highlight tail until its
float64 contribution underflows to zero. The shadow blur and its edge padding
still run in the original full-frame order. The first glass blend's float64
promotion is explicit before assigning back to slices.

The first one-pixel margin was too tight. It changed two Mandelblom source PNG
pixels by one channel level on both the Mac and Jarvis. Those first attempts,
including every timing sample and pixel comparison, are retained as failed
pixel-preservation experiments and are not the installed candidate's results.

A separate local float32 geometry probe changed four Mandelblom source pixels
by one level and two prepared RGB565 pixels by up to eight expanded channel
levels. That probe is also rejected and retained. The selected renderer keeps
the existing float64 geometry and sampling precision.

## Rust scope

The dependency-free Rust `cdylib` replaces only the bilinear sampler. Contiguous
float32 image data and float64 coordinate/result buffers cross a documented
ctypes FFI boundary. Python retains refraction coordinates, tint, shadow, PNG
encoding and the rest of the pipeline. This compares the extra value of a
native sampler after the simpler NumPy improvement; it does not establish how
fast a complete Rust image renderer could be.

Rust formatting, locked Clippy over all targets with warnings denied, locked
unit tests and release builds pass on both the Mac and Jarvis. Tests cover
interpolation, boundary clamping, rejected dimensions and nonfinite coordinates.

## Corrected candidate: measured results

These are medians of three cold runs per cell in `results/main-v2` on Jarvis.
Memory is **MiB** (1,048,576 bytes); timings are seconds. CPU time can exceed
wall time because it sums work across the processes' threads.

| Input | Variant | Wall s | CPU s | Peak RSS MiB |
| --- | --- | ---: | ---: | ---: |
| Mandelblom | Original | 13.156 | 14.066 | 427.03 |
| Mandelblom | Bounded NumPy | 10.736 | 11.647 | 184.13 |
| Mandelblom | Bounded NumPy + Rust sampler | 10.614 | 11.529 | 168.77 |
| Stjärnenatten | Original | 14.129 | 15.045 | 427.03 |
| Stjärnenatten | Bounded NumPy | 11.730 | 12.624 | 184.14 |
| Stjärnenatten | Bounded NumPy + Rust sampler | 11.601 | 12.504 | 168.77 |
| NASA 2026-10-25 | Original | 12.631 | 13.537 | 426.97 |
| NASA 2026-10-25 | Bounded NumPy | 10.280 | 11.191 | 184.09 |
| NASA 2026-10-25 | Bounded NumPy + Rust sampler | 10.141 | 11.055 | 168.72 |

Bounded NumPy reduces peak RSS by about **57%**, wall time by **17–19%** and
CPU time by **16–17%** relative to the original, depending on the input. It
passes the proposed halved-memory criterion. The Rust sampler adds only about
**1.1–1.3%** lower median wall time, **1%** lower CPU time and **8.3%** lower
peak RSS relative to bounded NumPy. These small samples do not establish a
population speedup; the extra build and FFI maintenance are not justified here.

Every one of the corrected suite's **162 output comparisons** is pixel-identical
to the original: dashboard, art and voice source PNGs, their RGB565 PNGs and
their lossless QOI sidecars, for both NumPy and Rust in every repetition. No
timing samples were removed, and every recorded throttling check is `0x0`.

The initial production choice was bounded NumPy with the original precision;
Rust was initially retained only as an experiment. The benefit applies
to new, uncached images. This does not reduce the permanently running server's
RSS, change voice/model latency, or speed up a background already in the cache.

## Initial NumPy installation and release verification

`tools/deploy_jarvis.sh` completed successfully after **545 local tests**, ESPHome
configuration validation and **65 candidate tests in the actual HA runtime**.
No paid model/STT/TTS evaluation was started. The deployment restarts the Jarvis
service through the existing checked script; panel firmware and HA were not
restarted for this change. The normal service startup behavior is unchanged.

Before installation, the runtime hash audit found only the intended renderer
change and the updated background-module documentation. After installation,
all **47** audited Python files match the candidate, including the other
session's deployed work. The service is active. A cold run importing the
**installed** renderer took 10.277 s wall / 11.189 s CPU with 184.16 MiB peak RSS;
all nine resulting files are byte-identical to the NASA baseline. This extra
verification run is separate from the 27-run comparison.

The real visual check passes against the existing v4 reference without changing
it. The responsiveness workload passes before and after and restores the page.
Times below are p50 / p95 / p99 / max in ms:

| Fixed responsiveness workload | Before | After |
| --- | --- | --- |
| Injected press to redraw | 17.87 / 18.43 / 18.43 / 18.43 | 18.01 / 18.37 / 18.51 / 18.51 |
| Injected release to redraw | 63.92 / 66.85 / 67.21 / 67.21 | 63.84 / 67.25 / 67.58 / 67.58 |
| 25 fps fixture completion interval | 40.88 / 42.01 / 44.26 / 45.93 | 40.92 / 41.95 / 44.48 / 45.17 |
| Host status HTTP | 23.24 / 78.71 / 85.33 / 85.33 | 19.95 / 82.84 / 114.35 / 114.35 |

The 25 fps fixture's refresh jank over 50 ms is 0% in both runs. Observer overhead
on the UI task p95/max is 415/439 µs before and 435/445 µs after. HTTP-worker
p95/max is 2,623/2,983 µs before and 2,633/2,724 µs after. These software timestamps
do not measure finger-to-photon latency.

### Retained failed HTTP tail check

The first after-install production throughput run preserved the render/cadence
limits and 0 repeated frames, but **failed** the unchanged measured-baseline
comparison for host HTTP p95: **98.629 ms**, against a permitted 97.459 ms
(47.459 ms baseline + 50 ms allowance). Image-result retrieval overlapped this
run. That does not establish the cause of the slower network tail.

A single repeat after all rendering and transfers had ended passes every
unchanged absolute and baseline gate. Both runs, including the failed verdict,
remain in the evidence. No baseline, limit or sample was replaced. The capture
command's exit status reports completed capture/restoration; the separate
`--check` exits 1 for the failed first after-run, and 0 for the idle repeat.

| Production throughput, p50 / p95 / p99 / max ms | Before | First after (HTTP gate failed) | After idle (passes) |
| --- | --- | --- | --- |
| Refresh completion interval | 16.39 / 19.06 / 19.82 / 20.22 | 16.39 / 18.84 / 19.82 / 20.31 | 16.49 / 18.81 / 19.74 / 20.51 |
| Display presentation interval | 16.563 / 16.568 / 16.572 / 16.580 | 16.562 / 16.569 / 16.572 / 16.575 | 16.563 / 16.568 / 16.571 / 16.576 |
| Host status HTTP | 20.89 / 45.53 / 57.06 / 57.06 | 40.09 / 98.63 / 120.93 / 120.93 | 20.58 / 29.99 / 40.99 / 40.99 |

All three production captures have 0% refresh jank over 25 ms and 0 repeated
presentations. These are short orb measurements, not every screen or optical
scanout. The result supports keeping the image optimization; it does not claim
a panel UI speedup from changing a renderer that runs only on Jarvis.

The initial sandboxed local-check attempt could not bind localhost for 42 fake
server tests. The same pre-change checks passed with the required local-socket
access. Both logs are retained alongside the successful deployment log, raw
timing/source snapshots, pixel comparisons, visual report and runtime manifests.

## Follow-up: Joel chose to enable the Rust sampler

Joel explicitly requested using the Rust variant after reviewing the initial
decision. The additional gain remains small; this changes the maintenance
tradeoff, not the original measurements. The crate moved to
`native/image_sampling` and the renderer now imports the checked production
binding in `styles/glass-v2/image_sampling.py`.

The binding checks ABI version 1 before passing any buffers. It validates image
shape/dtype and coordinate shapes, and makes inputs contiguous **and aligned**
before crossing FFI. Nonfinite coordinates are rejected by Rust. There is no
silent NumPy substitution if the library is missing or incompatible. Python
still owns the pipeline; Rust replaces only bilinear sampling.

`tools/check.py` now builds and checks Rust before running Python tests.
`tools/deploy_jarvis.sh` builds the target library in a private Jarvis staging
directory, runs the Rust gates plus the Python ABI tests and full-render pixel
tests there, then installs that checked library before the binding and renderer.
The stage is removed on exit. No third-party Rust crate was added.

The checked deployment passed **550 local tests**, **65 HA runtime tests**, the
Rust fmt/locked Clippy/locked test/release-build gates on both architectures,
and **5 ABI tests plus 2 full-render tests on Jarvis** before changing live code.
After installation the backend reports `rust`, ABI `1`, and the actual loaded
library is `~/hallpanel-live/styles/glass-v2/libhallpanel_image_sampling.so`.
All **48** audited Python files match the candidate and the service is active.

Three cold verification runs using the installed default backend produced
**27 byte-identical PNG/QOI files** against the original full-frame renderer,
covering all three inputs. These single runs are integration checks, not a
replacement for the interleaved three-repetition comparison above. Raw evidence
is retained separately in `.panel-ci/rust-sampling-production-20261025/`.

| Installed Rust verification (one run per image) | Wall s | CPU s | Peak RSS MiB |
| --- | ---: | ---: | ---: |
| Mandelblom | 10.690 | 11.592 | 168.72 |
| Stjärnenatten | 11.636 | 12.550 | 168.73 |
| NASA 2026-10-25 | 10.178 | 11.090 | 168.66 |

The Rust release's unchanged responsiveness and throughput baseline comparisons
pass before and after. Times are p50 / p95 / p99 / max in ms:

| Rust release check | Before | After |
| --- | --- | --- |
| Injected press to redraw | 18.00 / 18.32 / 18.43 / 18.43 | 17.73 / 18.30 / 18.34 / 18.34 |
| Injected release to redraw | 62.53 / 64.66 / 64.91 / 64.91 | 63.79 / 67.32 / 67.38 / 67.38 |
| 25 fps fixture completion interval | 40.89 / 42.12 / 44.01 / 46.27 | 40.96 / 42.08 / 44.63 / 45.40 |
| Responsiveness host HTTP | 25.03 / 77.20 / 78.10 / 78.10 | 19.22 / 86.24 / 125.87 / 125.87 |
| Production refresh completion interval | 16.46 / 18.88 / 19.84 / 20.37 | 16.44 / 18.93 / 19.87 / 20.11 |
| Production presentation interval | 16.562 / 16.567 / 16.571 / 16.577 | 16.562 / 16.568 / 16.573 / 16.576 |
| Production host HTTP | 19.96 / 43.47 / 97.48 / 97.48 | 20.92 / 29.66 / 59.60 / 59.60 |

The 25 fps fixture has 0% refresh jank over 50 ms; production has 0% over 25 ms,
0 repeated presentations and 0 decoder errors in both short captures. No optical
or acoustic measurement is inferred, and the differences in these UI timings
are workload variation rather than a claim that Rust on Jarvis accelerates LVGL.

UI-task observer overhead p50/p95/p99/max is 320/425/596/596 µs before and
328/411/445/445 µs after. HTTP-worker overhead is 2,410/2,724/2,785/2,785 µs
before and 2,450/3,760/3,886/3,886 µs after. Actual production source changes
are 60.377/s before and 60.385/s after. The visual regression check passes the
unchanged reference, and all physical test workloads restore the original page.
