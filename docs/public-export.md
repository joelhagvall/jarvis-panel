# Public snapshot

This is a manually reviewed snapshot. The private export policy, sanitizer implementation and its unit tests stay in the working repository. They can describe original private values. All engineering/measurement tools are included; no sync or push runs automatically.

## Free checks in the public checkout

Create its own `.venv` with Python 3.13, install ESPHome and `tests/requirements.txt`,
and install SDL2, a C++ compiler, ffmpeg, ImageMagick and Rust. Build the native
sampler with `python tools/check.py --tests-only` (its first step). Native UI tests
also need the `style-glass-v2` simulator's LVGL headers/library. Generate
`styles/glass-v2/glass-v2.yaml` with `gen.py` and compile it with ESPHome; do not run
the simulator or contact a physical panel for these checks. Keep the ESPHome Inter
font cache used by the image generators. Audio-resampler tests need ESPHome's
`esp-audio-libs` managed component from a prepared panel build. None of those build
artifacts belongs in Git.

The canonical local suite is `tools/check.py --tests-only`: it isolates modules,
uses offline provider stubs and rejects skips. HA-runtime tests run separately with
fake providers inside a Home Assistant environment; do not point checks at production.
Plain whole-process pytest has shared module/global-state collisions in this project.
The export uses a qualified Soloist-watch import and `pytest.ini` collects only
`test_*.py`; `wake_word_test.py` remains an explicit optional clip-analysis CLI.

Run targeted checks with the same offline environment as the canonical runner.
Do not run paid integration/model/voice evaluations for export verification.

