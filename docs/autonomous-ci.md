# Autonomous local Hallpanel development

All agents use this repository's `tools/panel`, the shared hallpanel skill, and `main`.
The Mac compiles firmware. Tests execute against the physical ESP32-P4 through its
authenticated local interface. GitHub stores source and backups; no CI service is involved.

Jarvis deployment and native staging use `tools/jarvis_ssh.sh` for shell calls and
rsync alike. It supplies the same noninteractive Keychain/control-socket options
as the HA candidate tests. A successful HA test connection alone does not establish
that plain `ssh` will authenticate. No temporary PATH wrapper is needed.

The ESP32-P4 restart cache-sync correction and the bounded `tools/panel recovery`
workflow are documented in [restart-crash.md](restart-crash.md). A recovery plan
automatically seals checked source, immutable firmware, the diagnosis and the
active quarantine under Joel's standing authorization of October 26. Do not ask
again before task-scoped flash, restart or verification. The old generic
speaker-click warning is obsolete. All CI gates and bounded failure stops remain; recovery never
publishes.

## Commands

Run from any directory using the absolute path to `tools/panel`.

| Command | Effect |
|---|---|
| `tools/panel inspect [--out NEW_DIR] [--lossless]` | Fresh screenshot, widget tree, status, consistency check; private files |
| `tools/panel status` / `tools/panel ui` | Passive metrics / visible structural element IDs and geometry |
| `tools/panel session` | JSONL commands, persistent connection, optional `expect_page`, stops on first error |
| `tools/panel tap X Y` / `tap-text TEXT` / `tap-id PATH` | Real touch using coordinates, a unique label, or a fresh widget path |
| `tools/panel drag X1 Y1 X2 Y2` / `swipe X1 Y1 X2 Y2` | Real bounded pointer gesture |
| `tools/panel slider PATH VALUE` | Fresh slider geometry, drag and value verification |
| `tools/panel test-mode MODE --duration-ms N` | Bounded synthetic UI test, at most 30 seconds; `restore` ends it early |
| `tools/panel smoke --out NEW_DIR [--brightness-drag] [--test-mode]` | Free physical navigation, captures, auth/coordinate rejection, buffer release and restoration |
| `tools/panel visual record --out NEW_DIR` | Explicit new golden set; never replaces an existing directory |
| `tools/panel visual check [--baseline DIR] [--out NEW_DIR]` | Compare against a reviewed set; never promotes a failed capture |
| `tools/panel metrics collect --out NEW_DIR` | Status samples, private screenshots and measured latency |
| `tools/panel metrics check REPORT [--baseline REPORT]` | Memory floors and regression policy; reports whether a baseline was compared |
| `tools/panel responsiveness [--out NEW_DIR]` | 20 real tab taps plus 10 s orb with the real background; fresh p50/p95/p99/max, pacing, jank and HTTP timing |
| `tools/panel metrics baseline REPORT --out NEW_FILE` | Explicit first baseline from a complete passing measurement |
| `tools/panel monitor --once [--history FILE]` | Passive status sample, bounded history, restart/leak/failure alerts |
| `tools/panel scheduled-smoke [--idle-seconds 300]` | Physical smoke only with known healthy/idle voice, alarm, touch and test state |
| `tools/panel metrics collect --animation --out NEW_DIR` | Bounded real orb animation, no audio/model/screenshot load, then restoration |
| `tools/panel ci --local-only` | Static checks, free unit tests, ESPHome config, previous build backup, Mac compilation; cannot publish |
| `tools/panel ci [--visual-baseline DIR]` | All local gates, OTA, identity/boot, physical/synthetic/visual/performance/log gates and evidence |
| `tools/panel ci --brightness-drag [--visual-baseline DIR]` | Also verify real five-percent brightness detents and restore the original value before performance checks |
| `tools/panel ci --publish --files FILE... --message 'Message'` | Explicit publication request; commit and normal push to `origin/main` after every physical gate |

`tools/panel COMMAND --help` gives the exact arguments. `--url` for ordinary remote
commands precedes the command. CI, metrics, visual and smoke accept their own `--url`.
CI's `--device` is the OTA hostname; it never automatically selects USB.

## Pipeline and publication

CI takes the workspace lock before inventorying every versionable file, its mode,
deletions and content hash. The content digest becomes the firmware `build_id`.
It checks the inventory between stages. Editing the candidate during verification
invalidates the run. Ignored generated files, secrets and private artifacts are excluded.

The pipeline runs Python AST checks, shell syntax checks, `git diff --check` and the
existing `tools/check.py` offline suite/config validation. C++ protocol, lease and codec
tests compile on the host; the ESP-IDF compiler validates the actual firmware code.
ESPHome copies local headers only when they are explicitly listed in `esphome.includes`; a wrapper's quoted
include is insufficient. `tests/test_ui_priority.py` checks that both priority headers ship (a missing core
header was caught by the firmware compile before upload).
There is no test-skip option for installation. Model evals and HA service installation
are separate workflows. Changes to HA must still use `tools/deploy_jarvis.sh` and its
container tests.

The previous Mac binaries, ELF and available linker map are copied **before compilation**
and labeled unverified. Each candidate keeps its own ELF for crash decoding.
A file on disk is not proof that the device runs it. Only a complete physical pass
creates `.panel-ci/verified/<build_id>/` with the OTA binary hash and evidence pointer.
Missing or stale visual/performance baselines stop the normal pipeline before OTA.
Idle measurements use Hemmet and restore the original tab. The expected baselines are
`.panel-ci/baselines/performance.json` (idle with captures) and
`.panel-ci/baselines/animation.json` (orb without captures). A complete compatible
`.panel-ci/baselines/responsiveness.json` is also required before OTA.

After upload, CI requires a new device boot ID, the candidate build ID, assets ready,
Wi-Fi, 65 seconds of uptime, and no further restart. It obtains a new lease after
the reboot. Boot/runtime logs use the existing encrypted ESPHome API; opening a USB
serial port is deliberately avoided. A missing log connection is a failed gate.
Crash, heap corruption, brownout and bootloop signatures stop the run.

`--publish` requires an explicit file allowlist. Any other dirty or untracked source
blocks publication. This is intentional: a candidate built from another agent's
uncommitted code cannot honestly be published as only this agent's change. The branch
must be `main`, upstream must be `origin/main`, and both origin URLs must identify
`joelhagvall/hallpanel`. CI fetches `main` and requires HEAD to match it before commit.
It runs `git status` immediately before staging the allowlist, uses a plain commit
message, keeps Git hooks, and pushes without force. Failures never trigger a push.

## Locks, sessions and agent coordination

`PanelLock('workspace')` serializes builds, generation and release publication on the
Mac. `PanelLock('device')` serializes local interaction and OTA. They use kernel `flock`,
private stable inodes, thread ownership and verified inherited file descriptors.
The kernel releases a lock when its last owner exits. Lock files are never deleted
as a way of breaking an active session. Acquire workspace before device.

Protocol 2 adds a device lease shared across Mac and Jarvis. `/ui`, captures, touch
and `/test` require its `X-Panel-Lease`; `/status` remains an authenticated passive read.
The lease lasts at most 300 seconds and the client renews before protected operations.
Synthetic state has a separate hard timeout of at most 30 seconds. Lease release/expiry
restores owned synthetic changes. A real finger has priority over remote pointer input.

Use `with PanelClient(persistent=True) as client, client.exclusive():` for a suite.
For a child process use the client's `subprocess_options()` (environment **and**
`pass_fds`), never copy a lease ID into an arbitrary shell environment.
`.venv/bin/python tools/panel_lock.py run --lock workspace --lock device -- COMMAND...` wraps cooperative
tools. `tools/flash_panel.sh` uses this wrapper and a bounded device lease for upload.

All agents can analyze in parallel. Assign files before concurrent edits; take the
workspace lock while changing shared generators or preparing a release. Hash checks
detect writers which ignore the lock. Locks cannot prevent an editor ignoring the
protocol from changing files, or an OTA-password holder from bypassing this harness.
The existing OTA password and device token remain separate. No router/firewall port
is opened. The HTTP interface is intended for the trusted LAN; it does not add TLS.

Input is never retried after a disconnect. Read-only boot/status polling may retry.
A killed host leaves a bounded lease and test timeout, not an indefinite UI test.
Unknown state, connection loss, restoration failure or memory floor violation stops
the pipeline; it does not blindly resume queued gestures or flash again.
Failure after OTA starts also creates `.panel-ci/quarantine.json`. Further physical CI
and scheduled smoke stop until the recorded cause is resolved and the quarantine is
explicitly reviewed. Read-only inspection and local checks remain available. A quarantine
on one host must also be communicated to other scheduler hosts; local files are not a
cross-host notification service.

## Visual and interactive coverage

The versioned `tests/visual/policy.json` specifies 14 scenes: dashboard, Hemmet,
settings on both pages, Tavla, and synthetic night, listening, thinking, speaking,
OTA, Wi-Fi warning, HA warning, timeout and missing image. Synthetic animation frames
are frozen and backgrounds hidden where needed for reproducibility. Captures settle,
then compare two lossless RGB565 frames for stability before acceptance.

Policy declares pixel masks, channel tolerance, changed-pixel budget and structural
geometry contracts. Dynamic text, slider values and live backgrounds are excluded
explicitly. Some live views have geometry contracts only; a golden screenshot does
not imply every pixel in that screenshot is compared. Bounds checks reject clipped
stable controls. Reports include current/reference/diff images and a local HTML gallery.
Smoke saves before/after interaction screenshots and verifies the restored original page.
Brightness dragging is opt-in and restores the exact previous value. Preserve Joel's
chosen daytime speaker volume; 20% is his night-mode preference, not a forced test
setting. Save/read actual settings before tests and restore only test-owned changes.

Widget IDs are structural paths in the current visible tree, not permanent semantic
IDs. Inspect again after navigation or a UI revision. Use `expect_page` guards.

The first protocol-2 installation uses the checked flash path with local locks and
explicit legacy migration support. Its old visual reference becomes stale when the
policy changes, so that upload cannot be reported as a complete visual pass. Capture
and review a **new** golden directory; preserve the earlier set. Do not recreate
goldens merely because a comparison failed. After review select it as the reference
and establish the measured performance baselines before a full release run.

## Measurements and fault boundaries

Every checked flash and physical CI run also runs the fixed responsiveness workload.
Its independent raw sample rings and stricter absolute/relative gates are documented in
[responsiveness.md](responsiveness.md). Establish the first measured reference explicitly at
`.panel-ci/baselines/responsiveness.json`; normal installation stops before OTA if it is
missing or incompatible. Preserve previous references on failures.

Status includes firmware/ELF identity, boot ID, reset reason, uptime, Wi-Fi state/RSSI,
free/minimum internal heap and PSRAM, largest internal block, and capture count.
The board callback adds non-resetting lifetime render counters, window render statistics,
touch-sample-to-refresh timings and an assets/network-ready milestone. FPS here means
LVGL redraws per second, not the LCD scanout rate. CPU utilization is unsupported.
Network capture latency and framebuffer copy duration are separate measurements.
The shared client's 16 KiB TCP receive buffer and phased capture timing are
documented in [screenshot-transport.md](screenshot-transport.md). The latency gate
still measures the complete transaction against the unchanged baseline.
Software touch timing starts after input sampling; it is not finger-to-photon latency.
`firmware_elf_sha256` follows ESPHome's configured retrieval length, currently a
nine-character prefix. The release report's `firmware_sha256` hashes the complete OTA
binary; the 32-character candidate `build_id` is the running-firmware identity gate.

Performance baselines must use the same page/workload and measured fields. Hard floors
and relative tolerances live in `tests/performance/policy.json`. Required missing metrics
fail closed. Missing optional metrics are reported as unsupported, not invented as zero.
Use the bounded animation workload to measure active rendering without screenshot load.
Idle screenshot collection measures a different workload and needs its own baseline.

Listening/thinking/speaking test modes operate LVGL only. They do not start wake-word
inference, record audio, call models, play sound, disconnect Wi-Fi/HA or change alarm
entities. Fault scenes prove warning rendering and cleanup, **not** the production
network timeout/reconnection path. Actual acoustic recording/playback and real
disconnect/recovery tests remain gated on suitable measurement and a separately bounded
test method. No automated test changes smoke/water alarms or stops a ringing timer.

## Repair and recovery

`--repair-argv COMMAND ARG...` must be the final CI option. It invokes an explicitly
chosen local repair adapter only after a failed local check, with at most three attempts.
`--files` bounds its changes; a no-op, changed HEAD or out-of-scope file stops the run.
CI re-inventories and reruns static/unit/config checks after each correction. Hardware
failures, timeout, unknown exceptions and concurrent edits never invoke this adapter.
The adapter is trusted code with the caller's permissions; CI is not an OS sandbox.

The checked ESPHome 2026.9.1 build uses two 0x7C0000 OTA app slots, configured
`CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y`, ESPHome safe mode and a 5-second task watchdog.
The installed bootloader must also support rollback; a build flag alone is insufficient
proof. ESPHome normally confirms after 60 seconds and can confirm on orderly shutdown.
The host's boot-health gate does not replace that bootloader checkpoint.

Host automatic restore is disabled until an actual, reviewed recovery rehearsal exists.
No rehearsal is fabricated from configuration. If the device is unreachable, the host
stops and saves evidence; it does not attempt USB, power cycling, partition changes or
repeated OTA. A verified firmware archive is retained for an explicitly approved recovery.
See [ESPHome safe mode](https://esphome.io/components/safe_mode/) and
[ESP32-P4 OTA rollback](https://docs.espressif.com/projects/esp-idf/en/v5.3.2/esp32p4/api-reference/system/ota.html).

## Private artifacts and monitoring

`.panel-ci/run-*/` contains the candidate manifest, checks/build/OTA logs, firmware hash,
screenshots, UI/status snapshots, physical results, metrics, runtime log and final report.
Directories are private, files use mode 600, known local credentials are masked in logs,
and the entire state directory is ignored by Git. UI captures can still contain calendar
or reply text; keep them local. A failed redactor withholds the log instead of writing secrets.
The collector does not publish artifacts or use GitHub Actions.

Completed runs retain at most 20 directories with a 512 MiB budget (the newest run is
kept even if it alone exceeds the budget). Verified firmware retains the latest three
identities. Incomplete runs are kept for investigation. Explicit goldens are never pruned.
Passive history retains 2016 samples, seven days at five-minute cadence. The optional
daily smoke timer requires five minutes without a real physical touch and known idle
voice/announcement/alarm state. It repeats the guard after acquiring the device lease;
unknown telemetry skips interaction. Neither timer is installed implicitly. See
[panel-monitor.md](panel-monitor.md) for opt-in systemd units, alert transitions and
the limits of leak/restart detection. Installing a timer is separate from writing its unit.

## Verification record

After an accidental real voice start interrupts idle metrics, use
`tools/panel ci --verify-installed INTERRUPTED_RUN --url URL --device HOST --visual-baseline REVIEWED_DIRECTORY`.
It never builds, uploads, restarts or publishes. It requires the existing quarantine,
clean prior boot/physical/visual gates, measured voice activity on that same boot,
an unchanged firmware artifact and unchanged ESP32 runtime inputs. A narrow source
allowlist permits the verifier, its navigation helpers/tests/documentation and the
independent host-side transcription client/tests to change. Every difference is
recorded; firmware generators, headers, visual/performance policies and other
runtime inputs must match. Host changes still require checked Jarvis deployment.
It rejects a new boot, active voice/alarm/touch, unknown failures or runtime changes.
It reruns local checks and the **same** complete physical, visual, performance and
log gates as normal CI. Only a complete pass archives the interruption evidence,
marks the installed firmware verified and resolves that exact quarantine.
The original interrupted report is retained with its failed result. Another
interruption or failed gate retains quarantine; no input or OTA is blindly retried.

Record real command results and hardware observations in the release report. A host
unit test of a fake device verifies harness behavior; it is not a physical panel pass.
Local-only success never creates a verified firmware or enables publication. Missing
connectivity, unsupported measurement and unperformed recovery/audio tests stay explicit.

Production 60 fps also requires a measured `.panel-ci/baselines/throughput.json`
before OTA. CI retains the independent fixed 25 fps responsiveness workload, then
checks original-duration 60 fps source rate, cadence/jank, presentation (repeated
frames from the display interrupt), copy errors and relative render/host regressions,
then the Spelas nu record's frame rate in the synthetic `turntable` test mode (`tools/panel turntable-fps`: at
least 55 redraws a second and at most 12 ms each, no music needed; added 2026-10-28 after a plinth picture under the
record made it 34 a second unnoticed, since the other gates measure only the orb), and finally runs a 120 s
production soak (`60fps-soak`): at most one late presentation
and three repeated frames; a failed soak is measured once more (`60fps-soak-repeat`)
before the run stops. Soak reports are retained and never become references. An A/B method result never replaces the final production
measurement. References are explicit and fingerprinted; failed ones cannot be
promoted. The checked flash script enforces the same two timing references.

Completed run retention now archives old directories in `.panel-ci/history/` and
leaves their original paths as relative symlinks. It never deletes raw evidence to
meet the active-directory count/size limit. Active/incomplete, baseline and
quarantine evidence stay protected. An earlier version incorrectly deleted old
completed runs, including failures; some early Oct 24 CI raw files were lost.
The investigation records that loss. Summaries do not replace those raw files.

The debug ELFs are the exception: they are build output, not measurements, and each run
kept two of them (about 92 MB), which filled the Mac disk (9.7 GB in `.panel-ci` on
2026-10-26). After archiving, `prune_archived_elfs` removes `firmware.elf` and
`previous-build/firmware.elf` from archived runs older than the newest ten. It skips runs
named by `quarantine.json`, a baseline or a sealed recovery plan (a plan with an
`.approval.json`), because those check the ELF hash. The removed hashes stay in the run's
`elf-pruned.json`. Reports, logs, images and firmware binaries are never removed.

For an intentionally different production asset/buffer profile, CI accepts
`--performance-baseline FILE --animation-baseline FILE` for explicitly reviewed,
separately measured references. Both default to the original paths. Missing,
wrong-workload or incomplete explicit files stop before upload; no fallback,
new baseline, policy relaxation or overwrite occurs during CI. Selected paths
and hashes are in the report. The current orb-v5 profile review and raw
before/after evidence are in [the profile record](baseline/performance-profile-v5-2026-10-25.md).

## Learning from failures

Follow the event-driven [harness maintenance](harness-maintenance.md) workflow.
Use the existing owner/test/runbook for a new discovery; no discovery requires no
extra diary entry. Transient boot reads (including .local construction) now retry
without weakening build/boot identity, minimum healthy uptime or restart rejection.
The checked flash script defaults to Wi-Fi and preflights an explicit USB port
before checks/build and again before upload; it never chooses the first port.

A single recovery attempt that stopped only on `status_roundtrip_ms` can instead
use `--verify-installed FAILED_RECOVERY_RUN --transport-diagnosis FIXED_AB_DIR`
with the same explicit visual/performance profile arguments. This requires
`without-logs/responsiveness.json` and `with-logs/responsiveness.json`: both fixed
cases must be complete, restored, from the identical boot and pass the unchanged
responsiveness policy/reference. The prior isolated failure is re-evaluated too.
The sealed recovery claim must bind the retained quarantine, source manifest,
firmware/ELF and earlier installed guard. Only the explicit host/docs allowlist
may differ. This consumes one exclusive continuation before physical QA; failure
or interruption prevents retry. It rebuilds or uploads nothing, repeats every
physical gate and retains original failed reports. The A/B experiment alone
never resolves quarantine or identifies the cause of a transient network delay.
