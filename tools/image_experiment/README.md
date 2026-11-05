# Cold background image experiment

This is an offline comparison harness. It never publishes an image, changes the
chosen background, calls a model, or touches the panel. After the initial
comparison, Joel chose to enable the small Rust sampler in production. Its
source now lives in `native/image_sampling`; the checked Python binding is
`styles/glass-v2/image_sampling.py`. The saved experiment snapshots remain unchanged.

`reference_bake.py` freezes the pre-experiment full-frame glass renderer from
commit `88d145f94268ff94b71cbbeecb929de51504baff`. It is also the independent pixel
oracle for `tests/test_bake.py`. The original unmodified source and first failed
candidate remain with the private raw experiment evidence.

## Protocol

Run on Jarvis in a separate scratch directory with its production venv and
NumPy/Pillow versions. Copy the harness, `server/panel_image.py`,
`server/voice_background.py`, `styles/glass-v2/gen.py`, and the input images into
the same relative layout as the repository. Put the reference in
`sources/baseline/bake.py`, and the candidate in `sources/numpy/bake.py`.

The fixed `cold-image-five-sequential-processes-v1` workload includes:

1. A fresh process importing the chosen renderer and creating dashboard and art PNGs.
2. A fresh process creating the voice page's frosted PNG.
3. Three fresh processes preparing dashboard, art and voice images as RGB565 PNG/QOI.

All decodes, crops, imports, encodes and process startups in those stages count.
Network downloads, publication, cache hits, the coordinator's imports and later
pixel comparisons do not count. Peak RSS is the largest **single child** peak,
not the sum of peaks from processes that never coexist. CPU time is cumulative
user plus system CPU seconds for all five children. Wall time spans all stages.
This measures cold local image preparation, not the HTTP download or the live
server's resident memory.

`suite.py` runs three repetitions of each of three variants on Mandelblom,
Stjärnenatten. The public workload omits the private historical image input. It rotates variant order by
input and repetition. Every run has its own output directory and raw timings;
temperature, throttling flags and host load are recorded around each run. No
slow sample is dropped. Compare medians per input, not a pooled percentile of
different images. A throttling flag fails the suite and retains the evidence.

## Rust experiment

The dependency-free `cdylib` replaces only bilinear sampling. Python retains
the rest of the pipeline, and ctypes passes contiguous buffers without an IPC
copy. It uses the same float32 source and float64 coordinates/results as NumPy.
The small unsafe FFI boundary documents buffer lifetimes and aliasing; the
interpolation itself is safe Rust. Deployment builds and verifies the library
before installing it for the short-lived background renderers.

```sh
export PATH="$HOME/.cargo/bin:$PATH"
cargo fmt --manifest-path native/image_sampling/Cargo.toml --check
cargo clippy --manifest-path native/image_sampling/Cargo.toml --locked --all-targets -- -D warnings
cargo test --manifest-path native/image_sampling/Cargo.toml --locked
cargo build --manifest-path native/image_sampling/Cargo.toml --locked --release
nice -n 19 ~/hallpanel-live/venv/bin/python tools/image_experiment/suite.py
~/hallpanel-live/venv/bin/python tools/image_experiment/compare.py results/main
```

`suite.py --out main-v2 --numpy numpy-v2` selects a second, separately retained
candidate and output directory. Existing directories are never overwritten.
`compare.py` runs only after the timing suite ends, so decoding comparison images
cannot inflate the measured CPU time or memory. Its report covers source PNGs
as well as all device PNG/QOI pixels. Compare Rust against the saved bounded
NumPy candidate, not merely against the old renderer; current production
`bake.py` already imports Rust. The original run used the experimental crate
name/path retained in the evidence archive; the ABI's sampling operation is unchanged.

## Isolated QOI and blur comparisons

`qoi_blur_suite.py` compares two frozen source roots using the same cold-pipeline
protocol, three repetitions and rotated order. It never queries the panel or
Jarvis. Use an isolated working copy and the selected Python runtime:

```sh
python tools/image_experiment/qoi_blur_suite.py \
  --baseline evidence/baseline-root --candidate evidence/qoi-root --out evidence/qoi-mac
python tools/image_experiment/qoi_blur_suite.py \
  --baseline evidence/qoi-root --candidate evidence/combined-root --out evidence/blur-mac
```

Each root contains frozen `styles/glass-v2/{bake,gen,image_sampling}.py`,
`server/{panel_image,voice_background}.py`, and its checked native release library.
Build the baseline from the unchanged crate. Snapshot each source and binary
before changing the next variant. The public workload uses only the shipped paintings. Historical private inputs are not distributed. Retain all output PNG/QOI
files, raw timings, input/source hashes, host load and priority. Comparisons
check encoded bytes in addition to the independent pixel tests. Mac results
must be confirmed on Jarvis before release. Concurrent panel work means deferring
Jarvis workloads and physical gates rather than bypassing them.
