# Settings slider detents

Skärmljus, Nattljus and Volym snap to five-percent stops while dragged and on
release: 45 → 50 → 55%. The whole 48–52% interval selects 50%. Stops are based
on percentages, not offsets from the minimum. Existing safety bounds remain
5–100% for day brightness, 1–100% for night brightness and 0–100% for volume.
The night slider therefore also retains its 1% lower endpoint.

The knob, displayed percentage and preview use the same snapped value. Release
commits that value through the existing board scripts. Brightness previews
still avoid flash/HA writes. Volume changes only on release, with its existing
local confirmation sound. No model or voice API is called.

Opening settings and Home Assistant/programmatic updates preserve exact saved
values, including the existing 12% night setting. They are not rounded merely
by opening the sheet. Touching that slider then selects a five-percent stop.
No layouts, labels or visual golden references need replacement for this change.

## Reusable checks

```sh
.venv/bin/python -m unittest tests.test_slider_detents
tools/panel slider-check --out /tmp/NEW-NATIVE-DIRECTORY
tools/panel ci --local-only
tools/panel ci --brightness-drag --visual-baseline REVIEWED-DIRECTORY
tools/panel smoke --brightness-drag --out /tmp/NEW-PHYSICAL-DIRECTORY
```

`slider-check` compiles against the existing native LVGL simulator library
(default `styles/glass-v2/.esphome/build/style-glass-v2`; override `--build-dir`).
It executes the actual generated value/release lambdas and the actual shared
PanelDebug pointer implementation. Each of the three sliders gets nine real
LVGL pointer drags: the 48–52% capture interval, adjacent 45/55% stops and both
endpoints. It also verifies that syncing 12% does not round or commit it.
Native LVGL proves event logic, not operation on the ESP32-P4. Missing native
dependencies fail explicitly. Each run saves source hashes, logs and JSON.

The physical brightness option tests a normal stop plus raw touch positions
48% and 52%, which must both show 50%, and restores the original brightness
and view. It does not move night brightness or volume. Before touch it reads
the exact existing backlight float through the encrypted ESPHome API using
the private existing `api_key`. After the real touch tests it restores that
float once and reopens settings through the normal gear control to verify
the original percentage. Thus an existing 51% is restored exactly even though
touch now selects 50%. Missing credentials or inconsistent native/UI state
stop before touch. A real finger, reboot, voice or alarm preemption prevents
blind restoration. Ambiguous native writes are never retried.

The first controlled installation stopped before changing brightness because
the old test could only restore values reachable through touch. That failed
report remains intact. A new same-boot complete detent smoke report can
supplement this specifically diagnosed host-test preflight failure through
`recovery plan --installed-report ... --smoke-report ...`. Source/runtime,
boot identity, clean logs and successful restoration must match before one
remaining OTA can be authorized; arbitrary failures cannot use this route.

## Installation gate

At implementation time the panel is quarantined after screenshot latency
failed its measured baseline in `.panel-ci/run-20261024-114750-236556/`.
The normal firmware pipeline must continue to honour that quarantine. This
UI change does not diagnose or resolve screenshot latency. Local tests,
native LVGL verification and compilation are permitted. A physical installation
needs a reviewed, bounded authorization through the shared recovery harness;
all physical/visual/performance gates still apply. A failed attempt stops
further installation and never permits publication or baseline replacement.
