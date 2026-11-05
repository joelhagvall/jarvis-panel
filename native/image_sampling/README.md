# Rust image sampling

This dependency-free library performs bilinear RGB sampling, RGB QOI encoding
and edge-padded Gaussian convolution. Python/NumPy retains the pipeline and
generates the original Gaussian weights. It is loaded
only by cold background preparation processes, not by the persistent web server.

`styles/glass-v2/image_sampling.py` checks sampling ABI version 1 and separate
additive QOI/blur feature versions, and validates shape,
dtype, contiguity and alignment before passing borrowed arrays to Rust. Callers
must not mutate those arrays concurrently. The Rust entry point checks sizes
and finite coordinates. Its slice construction has the documented FFI lifetime
and aliasing contract; interpolation, encoding and convolution use safe Rust
after that boundary.

The RGB-only QOI encoder preserves Pillow's opcode choices, colorspace tag and
wire bytes. Python owns its worst-case bounded output buffer. Blur reuses two
caller-owned buffers and retains vertical-then-horizontal tap order, edge
padding and per-tap float32 rounding through float64 intermediates. Each pass
walks output rows outermost and taps innermost, so one accumulator row stays
in cache instead of the whole buffer streaming through memory once per tap.
Each pass also splits its output rows into contiguous bands on up to two
scoped threads (images under 64 rows stay on the calling thread). Every value
still sees the same taps in the same order, so the output is bit-identical. Existing
sampling clients remain compatible because additive features have separate ABI getters.

`tools/check.py` runs fmt, locked Clippy with warnings denied over all targets,
locked tests and a release build before the free Python suite. A checkout loads
the resulting `.dylib` on macOS or `.so` on Linux from `target/release/`.

`tools/deploy_jarvis.sh` calls `tools/prepare_image_sampling.sh` to build the same
crate in a private temporary directory on Jarvis. Before touching installed code,
the candidate must pass the Rust gates, real Python-to-Rust ABI tests, exact
QOI wire/decoder and blur comparisons, and full-render pixel comparisons
against the original renderer on that CPU. The
checked library is installed by temporary-file rename alongside `bake.py`, then
the binding, renderer and dependent server image workers are copied. The
temporary build is removed on exit.
The installed renderer requires its native library and never silently substitutes
NumPy. ABI changes must increment the affected Rust feature version and Python's check.

For manual local verification:

```sh
cargo fmt --check
cargo clippy --locked --all-targets -- -D warnings
cargo test --locked
cargo build --locked --release
```

The first sampling comparison is in `docs/baseline/image-experiment-2026-10-25.md`.
QOI and blur candidate measurements are in `docs/baseline/qoi-blur-2026-10-25.md`.
The 2026-10-26 pipeline profile, which found PNG compression as the main cost and the blur as the
remaining native candidate, is in `docs/baseline/png-intermediates-2026-10-26.md`.
Mac-only results are not a Raspberry Pi performance or installation claim.
