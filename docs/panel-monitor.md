# Physical performance and passive monitoring

`tools/panel_metrics.py` is shared by the local CI command and all agents. It never writes firmware, starts actual voice, calls a model or promotes a baseline automatically. The explicit `--animation` command runs a bounded silent visual test mode and verifies that the original page returns.

## Measurements

```sh
tools/panel metrics collect --out /tmp/hallpanel-metrics-NEW --samples 5 --workload idle
tools/panel metrics collect --animation --out /tmp/hallpanel-animation-NEW --samples 5
tools/panel metrics check /tmp/hallpanel-metrics-NEW/metrics.json
tools/panel metrics baseline /tmp/hallpanel-metrics-NEW/metrics.json --out .panel-ci/baselines/idle-NEW.json
tools/panel metrics check /tmp/hallpanel-metrics-NEXT/metrics.json --baseline .panel-ci/baselines/idle-NEW.json
```

The output directory must be new. Captures and JSON are local and private (directory `0700`, JSON `0600`). Use `--no-screenshot` for status-only sampling. Workload names and pages must match when comparing measurements. Review the first measured baseline before using it as a release gate. A failed run cannot establish a baseline, and existing baselines cannot be overwritten by this command.

The policy is [`tests/performance/policy.json`](../tests/performance/policy.json). Required metrics fail closed when unavailable. By default, each measurement needs uptime, internal heap and PSRAM. Internal heap below 96 KiB, PSRAM below 4 MiB, a restart, Wi-Fi disconnect or a changing page fails the run. A measurement also fails if internal heap falls more than 64 KiB or PSRAM more than 256 KiB during the sampling window. These are conservative safety budgets, not measurements of normal use. Relative regression budgets compare only actual prior samples. A run without a supplied baseline reports `baseline_checked: false`.

The report records which fields the installed firmware exposes. It lists unsupported metrics separately. RSSI, Wi-Fi connection, minimum heap and firmware identity require the extended status protocol. Render rate/duration, touch-to-refresh and boot-ready time are checked only when the firmware provides those metrics. Add them to `required` when that coverage is required; absence then blocks the release. No placeholder number counts as evidence.

When lifetime render counters are available, redraw rate and average render duration come from differences between consecutive samples in the measured workload. A quiet interval with no redraw has no render-duration sample. Older firmware's rolling window averages are discarded in that case. Firmware `render_max_ms` and touch percentiles still cover their firmware counter window; their notes identify that limit.

`screenshot_latency_ms` measures the HTTP transfer and device capture request, including any safe rate-limit wait. It excludes local PNG/JPEG processing. `screenshot_copy_us` is the device main-task framebuffer copy. Software touch-to-refresh telemetry starts after input is sampled and ends when LVGL completes a refresh. It does not measure finger-to-photon latency. FPS means redraws, not LCD refresh rate. CPU utilization, optical flicker, actual backlight output, microphone quality and speaker quality need suitable counters or physical measurement equipment. The harness does not infer them from screenshots.

For an animation or simulated voice measurement, the CI test-mode runner controls that workload and passes its label into the same metrics API. Do not compare an idle measurement against an animation baseline. A status-only monitor does not itself activate voice or animation.

The `animation` workload requires observed redraw-rate and render-duration samples, with at least one redraw per second in every measured interval. This catches an animation that never starts. Its measured baseline then applies the relative FPS and frame-duration regression budgets. It uses no screenshot transfer during the interval, so capture overhead does not define the animation result.

## One passive health check

```sh
tools/panel metrics monitor --once --history .panel-ci/health.json --keep 2016
```

This reads `/status` once and stores an allowlist of telemetry. It never stores widget text, SSIDs, tokens or arbitrary exception messages. It detects a changed boot ID or falling uptime, three consecutive failed reads, low memory and sustained memory loss. A `millis()` wrap near 49.7 days does not count as a restart.

Leak detection compares the medians at either end of twelve samples over at least thirty minutes, with the same page, boot and firmware. At least 80% of adjacent readings must fall or stay equal. It flags more than 64 KiB lost internal heap or 512 KiB lost PSRAM as a possible leak; it does not claim a root cause. Workload changes can still affect memory, so investigate before changing firmware.

The Jarvis units read the panel at the fixed `http://hallpanel.local:8080`, while the panel gets that address by DHCP from the Inteno router at router.example.invalid (no `manual_ip` in firmware; a reservation is unconfirmed, 2026-10-29), and Jarvis cannot resolve `hallpanel.local`. If every read fails after a move or power cut, check `ip neigh` on Jarvis for MAC `02:00:00:00:00:01` before blaming the panel.

On Jarvis the history is `~/.local/state/hallpanel/health.json` (`hallpanel-health.timer`, every 5 minutes): each
entry's `status` holds `psram_free`, `psram_min_free` (lowest since boot), `internal_free` and `internal_min_free`, so
that file answers "how is the panel's memory now and over the week". On 2026-10-29 it showed PSRAM falling from
20.2 MB to 4.2 MB free as the Spelas nu record's two 7 MB atlases arrived; see now-playing.md for the fix.

At five-minute intervals, 2,016 samples retain about seven days of history; Jarvis keeps 4,032 (14 days, about 7.4 MB) since 2026-10-29 so that a two-week soak fits. Retention is capped at 10,000 samples, and an unexpectedly oversized input file is rejected. A process lock serializes history writes. `notify: true` appears only when an alert category appears or clears. Repeated identical alerts stay quiet at the notification layer. This module pushes nothing itself; on Jarvis `server/panel_health.py`, run by `hallpanel-sl-check.timer` through `server/check_sl.py --notify`, reads this history every 5 minutes and pushes to Joel's phone when the panel restarts in a loop, loses memory or Wi-Fi, or the monitor stalls (never when it is merely unreachable: Joel turned that push off on 2026-10-28), and once per crash boot (panic, watchdog, brownout; not power-on, OTA/software or USB restarts). Since 2026-10-29 it also pushes once per boot whose lowest free memory since start fell under `panel_health.FLOORS` (32 KiB internal, 1 MiB PSRAM), once that boot has been up 30 minutes, so development builds flashed over within minutes stay quiet. Nothing is pushed during the panel's night; the first run treats crashes already in the history as seen. The "minne lågt" alert (current free memory under the policy `minimum`) needs `monitor.low_memory_samples` (3, so 15 minutes) samples in a row from one boot under the floor: on 2026-11-03 one sample 72 bytes under 96 KiB pushed "minne lågt" and "fungerar igen" five minutes apart, and replaying the 10-day history gave 9 such pairs with one sample and none with three.

## Soak report

```sh
tools/panel soak                 # whole history, streamed read-only from Jarvis over ssh; nothing is written
tools/panel soak --days 7        # the last week, e.g. after the panel moved to the hallway
tools/panel soak --json          # every boot and field, for comparison or another tool
tools/panel soak --history FILE  # a local copy
```

[`tools/panel_soak.py`](../tools/panel_soak.py) turns the history above into one report: sample coverage, stretches where
the panel did not answer, gaps where the monitor itself did not run, restarts by reset reason, crashes (the same reset
reasons that `server/panel_health.py` pushes), the longest uptime, memory floors and drift per boot, Wi-Fi RSSI, render and
touch timings, and one row per Stockholm day. A boot is dated from its uptime; "offline up to N min" is an upper bound
because samples are five minutes apart. Render and touch zeros mean nothing was drawn or touched and are left out.
Memory drift compares the first and last hour of a boot of at least six hours and depends on what the panel showed, so
it is a lead, not a leak verdict. Development flashes count as software restarts; judge stability on a period without them.

## Optional Jarvis systemd timer

The checked-in user units under [`tools/systemd`](../tools/systemd/) are an opt-in template. They are not installed or enabled by local CI. They assume a checkout at `~/hallpanel`, a `.venv` with the panel dependencies, an ignored `styles/glass-v2/secrets.yaml` containing the dedicated debug token, and state directory `~/.local/state/hallpanel`. Adjust paths to the actual checkout before installation. Keep the secret file at `0600`; never put it in a unit or command line.

After explicitly deciding to enable monitoring on Jarvis:

```sh
install -d -m 700 ~/.local/state/hallpanel
install -d -m 700 ~/.config/systemd/user
install -m 600 tools/systemd/hallpanel-health.service tools/systemd/hallpanel-health.timer ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now hallpanel-health.timer
systemctl --user status hallpanel-health.timer
journalctl --user-unit hallpanel-health.service --since today
```

These units perform passive checks only. A scheduled smoke test must additionally establish an authenticated device lease and a verified idle period from device telemetry before any input. Page names alone do not prove that nobody is using the panel. No unattended smoke timer is enabled by these templates.
