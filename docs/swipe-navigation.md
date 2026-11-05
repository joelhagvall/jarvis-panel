# One gesture, one adjacent page

The page order is Gå hemifrån → Hemmet → Tavla → Natt. Left advances once,
right goes back once, and the end pages do not wrap. Tab clicks still select
the requested page directly.

Three independent issues were found on October 24:

- Sequential `if` actions could see the `cur_page` change caused by an earlier
  action in the same swipe script. The router now uses mutually exclusive
  `if`/`else` branches.
- The shared bar lives on LVGL's top layer. Its default gesture-bubble flag
  made LVGL search past its swipe handlers to that layer. A drag starting on a
  tab could become a release click on the starting tab. The bar now stops
  gesture bubbling and consumes the remaining press before changing pages.
- The remote event-mode pointer resumed LVGL's periodic read timer while
  pressed. Stale reads between explicit movement samples reset gesture
  velocity/distance. This pointer now pauses its own read timer after each
  explicit read. Explicit stationary reads still process long presses.

The real touchscreen had the same problem, found on 2026-10-29 from recorded finger samples
(`tools/panel touches`): the GT911 reports about every 13 ms, LVGL reads every 6-7 ms, so every
other read repeats the last point. LVGL 9.5 zeroes a gesture's distance on any read that moved less
than `gesture_min_velocity` (3 px), so Joel's swipes became gestures 30-60 ms late and 2 of 4 never
did. board.py sets the minimum to 0 for every pointer: the distance (50 px) is kept across repeated
reads. Sliders and the chat cannot swipe pages because of it: the settings sliders' gestures end at the
top layer, the music volume slider stops them (`gesture_bubble: false`), and scrolling areas never get
a gesture. `tests/test_panel_diagnostics.py` replays one of those swipes through LVGL's rule;
`tools/panel touches --summary` reports `lvgl_gesture_after_ms` per swipe. `tools/panel swipe-tour` replays
swipes Gå hemifrån -> Tavla and back (never into Natt) and reports press-to-frame and render work per page
change: on 2026-10-29 each change was one frame of 16-42 ms render work, 32-61 ms from touch-down.

The first physical CI caught the second issue and restored the dashboard.
Its failed report is retained in `.panel-ci/run-20261024-105134-831462`.

## Repeatable checks

`tests/test_panel_navigation.py` executes the generated routes with synchronous
page-load events, checks both directions and boundaries, and checks the bar's
gesture ownership. The physical smoke suite tests actual tab-bar swipes and
screenshots at each transition, including Gå hemifrån → Hemmet → Tavla.

```sh
tools/panel navigation-check --out /tmp/NEW-NATIVE-GESTURE-DIRECTORY
```

This optional Mac/native check links the already-built simulator's actual LVGL
library. `--build-dir` selects another native simulator build. A fresh checkout
needs to build the glass-v2 native simulator first. It never contacts the panel.
It sends a real LVGL pointer drag across the bar, including a screen change
and periodic LVGL timer processing during that drag. It imports the actual
production pointer method from `panel_debug.cpp`. Both the old bubble setting
and the stale-timer behavior must reproduce zero gestures and one false click.
The corrected combination must give one gesture and zero clicks. The report
binds the generator, production pointer, native probe and runner source hashes.

If physical CI quarantines this known navigation failure, the shared recovery
CLI can prepare one controlled correction, requiring clean boot/log evidence
and the matching native LVGL proof:

```sh
tools/panel ci --local-only
tools/panel recovery plan --run .panel-ci/LOCAL-RUN --diagnosis docs/swipe-navigation.md --ui-report .panel-ci/FAILED-RUN/report.json --native-report /tmp/NATIVE-GESTURE-DIRECTORY/report.json --out .panel-ci/swipe-recovery.json
tools/panel recovery approve .panel-ci/swipe-recovery.json --authorization 'Actual human authorization'
tools/panel ci --recovery-plan .panel-ci/swipe-recovery.json --visual-baseline .panel-visual/REVIEWED-REFERENCE
tools/panel recovery finish .panel-ci/swipe-recovery.json
```

All ordinary CI gates remain mandatory. Changed evidence, a different boot,
failed verification or a second attempt stops the workflow. No publication or
automatic firmware restore occurs.

A further diagnosed failure can cite `--prior-plan PATH`. Its approval, attempt
ledger, report and original quarantine must all match. The script permits only
two correction attempts after the initial installation, never an unbounded
repair loop. Failed reports and plans remain immutable.
