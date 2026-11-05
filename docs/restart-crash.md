# ESP32-P4 restart cache-sync fault

The physical panel panicked at the end of OTA on 2026-10-24 at 09:13 CEST.
The upload completed before `Rebooting safely`; the next boot reported reset
reason 4 (panic), store access fault 7 on core 1, PC `0x4ff2a070` and
MTVAL `0x3ff1009c` (`CACHE_SYNC_MAP_REG`). The top two instruction windows
match the previous OTA binary: `Cache_WriteBack_All` called by
`esp_restart_noos_inner`. The previous complete ELF was not retained, so the
rest of that backtrace cannot be reliably decoded.

This matches [ESP-IDF issue 19171 / IDFGH-18386](https://github.com/espressif/esp-idf/issues/19171).
On October 9, an Espressif collaborator confirmed reproduction and the cause:
resetting the other CPU during cache synchronization leaves the shared sync
engine busy. The restart's next write to a sync register faults. Espressif
reported failures within 50 restarts before the correction and none in 500
after stalling the other CPU and waiting for the sync engine to become idle.
Their report concerns revision 1.3; Hallpanel is revision 3.2. The matching
exception, register family and unchanged SDK sequence support this diagnosis;
local hardware verification of the correction is still required.

## Repository correction

`components/panel_restart` is automatically included by `board.py` for physical
builds. The linker wraps `esp_restart_noos`; agents need no special upload or
shutdown instructions. It:

1. Disables interrupts and arms the RTC watchdog, using IDF's one-second stages.
2. Stalls the other CPU without first resetting it.
3. Reads the shared sync engine until `CACHE_SYNC_DONE` is set.
4. Enters the original IDF restart only after synchronization completes.
5. Waits for the watchdog if the engine stays busy. It never continues into
   another cache writeback after a polling timeout.

The wrapper runs in IRAM and performs no logging, allocation, RTOS operations
or cache-sync register writes. IDF still handles peripheral resets, PSRAM stack
switching, cache shutdown, clock changes and final CPU reset. Its redundant
reset of the already-stalled CPU occurs after the cache transaction finishes.
PSRAM XIP stays enabled. The workaround is pinned to ESP-IDF 5.5.5; another SDK
version stops compilation until the upstream implementation has been reviewed.

`tests/test_panel_restart.py` compiles the actual wrapper with a modeled cache
engine. It checks both CPU directions, pending synchronization, watchdog setup
before waiting, and a permanently busy engine refusing the original restart.
It does not establish behavior on the physical board.

## Verification boundaries

The installation at 09:41 CEST was one previously approved exception. Its log
replayed the older crash **before** OTA. After its own reboot it reported reset
reason 3 (software reset) and no new crash. A historical report must not be
treated as a new exception, and a healthy screen alone is insufficient proof.

Keep the original failed report and quarantine until a bounded, authorized
installation and restart verification of the corrected firmware have passed.
The first installation still restarts the old, uncorrected firmware. Do not
run an unbounded restart stress test on the household panel.

## Same recovery commands for every agent

```sh
tools/panel ci --local-only
tools/panel recovery plan --run .panel-ci/run-TIMESTAMP --diagnosis docs/restart-crash.md --out .panel-ci/restart-recovery.json
tools/panel ci --recovery-plan .panel-ci/restart-recovery.json --visual-baseline .panel-visual/REVIEWED-REFERENCE
tools/panel ci --recovery-plan .panel-ci/restart-recovery.json --visual-baseline .panel-visual/REVIEWED-REFERENCE
tools/panel recovery finish .panel-ci/restart-recovery.json
```

Preparing the plan performs no device access. It checks the local CI result,
source inventory, diagnosis, firmware hash and linked IRAM guard. Joel's standing
authorization of October 26 covers task-scoped restarts, flashes and verification.
Plan creation automatically seals those exact artifacts and the current quarantine
for 24 hours. No additional human confirmation is required. Both physical
runs still execute every ordinary CI gate. They install the frozen approved
binary, not a later build with a different compilation timestamp. The first
tests installation; the second exercises shutdown of the corrected firmware.
No publication is possible in recovery mode. An incomplete or failed attempt
blocks the next; neither crashes nor disconnections trigger an automatic retry.
`finish` preserves the old quarantine in a resolution record and only removes
the active quarantine after both full runs prove distinct, normal software
boots. Jarvis's separate quarantine/timer state must also be reconciled before
scheduled physical smoke tests resume.

If a correction is already installed and its boot, crash-log review and physical
smoke passed, `plan --installed-report PATH` can prepare **one** further attempt.
It proves that all runtime source, including the restart guard, is identical;
only host harness, tests and documentation may differ. It also binds the current
firmware ELF and boot ID, which CI checks again before upload. Prepare a new
checked and automatically sealed plan; never reuse or rewrite a failed plan's ledger.

The first installation of this correction on October 24 passed boot and all
seven smoke checks, then found a host test race: the one-second synthetic expiry
elapsed before the next status request could observe the active page. The host
test now uses a five-second expiry and has a slow-HTTP regression. The original
failed CI report is retained; it does not count as a complete physical CI pass.

## Physical result, October 24

The corrected firmware's own OTA restart passed with reset reason 3, a new
boot ID, and no new crash in boot or runtime logs. Full CI passed in
`.panel-ci/run-20261024-103223-201234`: 325 free tests, build and linked-IRAM
verification, seven physical smoke checks, ten synthetic checks, fourteen
visual scenes and both measured performance baselines. Build ID:
`7f1af15ef52e7399bbca730c10fc9e91`. Animation median was 24.07 redraws/s at
14.03 ms average render duration. This is one corrected restart, not a local
500-restart stress test.

`recovery finish` archived and resolved the Mac quarantine. To reconcile the
same fault with an existing Jarvis harness, use the shared command below.
It requires the exact previously read SHA-256 of Jarvis's quarantine and the
live boot/firmware from the successful physical CI. It preserves the remote
quarantine and leaves physical tests stopped if either has changed.

```sh
tools/panel recovery sync-jarvis .panel-ci/PLAN.json.resolved.json --host user@jarvis.local --url http://hallpanel.local:8080 --expected-quarantine-sha256 SHA256 --resume-smoke
```

This resumes only the existing timers. It never runs an immediate physical
smoke test. The timer's ordinary lease, idle, voice and alarm guards still
apply. A systemd failure restores the active quarantine and disables the
physical timer. The dedicated token stays on Jarvis.

## A candidate that crashes before it is confirmed (2026-10-28)

ESPHome confirms a new firmware 60 s after boot. A crash before that rolls the panel back to the
previous build, which then runs normally, so a check that only looks at the screen passes. On
2026-10-28 the Jarvis-line candidate aborted (an assert, core 1, from the FreeRTOS tick hook) within
a minute of its first boot; the same build installed again a few minutes later ran cleanly through all
gates. The cause was not found: the network log has no assert text and the backtrace is mostly
unresolved.

- `tools/panel gates` waits for the build in `.panel-ci/last-flash.json` and checks that identity again after
  every gate, including a failed gate. A rollback stops the chain at `build` before further physical tests
  (`--any-build` skips identity matching). Crash parsing also recognises ESPHome's internal API reconnect
  lines, so a new crash revealed after its own reconnect cannot be mistaken for the first boot's old report.
- `tools/flash_panel.sh` records `esphome logs` from the end of the upload through the gates
  (`tools/panel crash capture`, `.panel-ci/flash-logs/`, last 20 kept; not over USB) and then runs
  `tools/panel crash scan`: a crash report or rollback that the first connection did not already show
  fails the install at `crash-check`, is decoded with the candidate's ELF and keeps that ELF beside
  the log. After a failed gate it records 45 s more (`CRASH_SETTLE_S`), so a candidate that crashed is reported
  by the rolled-back boot before the scan. The whole run's own output is kept beside it too
  (`<time>-flash.log`, named on its first line), so a failed stage can be read afterwards. The manual steps below are
  for a capture made some other way.
- 2026-10-28 10:29: the same Abort again (pc 0x4FF2AE00, `__assert_func` under `esp_vApplicationTickHook`) on the
  first boot of a build whose only change was the clock's position, two of six installs that day. The other
  backtrace words are not in the ELF's text (stack noise); `panel crash scan` now marks them.
- With the panel also on USB, `CRASH_SERIAL=/dev/cu.usbmodemN sh tools/flash_panel.sh` records its console
  (`<log>.serial.log`) and the scan prints its assert/panic lines (`CONSOLE: ...`). Opening the port with DTR
  and RTS low reset the panel; `panel crash serial` keeps them high, which did not (checked by uptime).
  The panel's USB port showed as /dev/cu.usbmodem101 with serial number 02:00:00:00:00:08, the ESP32-P4's own;
  the Wi-Fi address 02:00:00:00:00:01 belongs to the ESP32-C6, so the two never match. `tools/panel preflight
  --device PORT` reports `board_identity_verified: false` either way; with one ESP port plugged in by Joel it is
  the panel.
  The backtrace words above are The repository registers no tick hook, so the assert
  is in an ESP-IDF component; its text goes only to the USB serial console, which a network capture cannot see.
- The old firmware's next boot prints the crash once in `esphome logs panel.yaml --device
  hallpanel.local`: `CRASH DETECTED ON PREVIOUS BOOT`, the reason, `pc`/`btN` addresses and
  `OTA rollback detected! Rolled back from partition 'app0'`. Start the log before the upload to
  catch it, and stop it yourself afterwards: `esphome logs` never exits on its own, so a loop that checks a
  deadline between reconnects runs on (macOS has no `timeout`).
- The addresses belong to the candidate's ELF (`styles/glass-v2/.esphome/build/hallpanel/build/firmware.elf`,
  until the next compile): `~/Library/Caches/esphome/idf/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-addr2line -pfiaC -e ELF ADDR...`.

- 2026-10-29 10:29: Abort on core 0 (pc 0x4FF2AE00, the same panic-handler frames) during the visual gate of the
  candidate that lets screenshots copy the scanned buffer; ESPHome rolled it back. No USB console, so no assert
  text. The health history showed `internal_min_free` at 7–15 KB on most builds that day (already 15 KB on the
  morning's build) and the artwork worker had just moved 8 KB more into internal RAM (a 4 KiB read buffer and a
  4 KiB HTTP buffer); both went back out (PSRAM read buffer, 1 KiB client buffer). Unproven cause; watch
  `internal_min_free` in `~/.local/state/hallpanel/health.json` and record the console on the next abort.
  10:44 the same Abort again, within the first minute, on the next candidate (artwork priority by page). Both
  crashed candidates carried the screenshot change (copy the scanned buffer while a flip is pending); the nine
  flashes before it that day did not crash, though one candidate with it passed every gate but the soak. That
  change is reverted; Spelas nu while the record turns still answers screenshots with 503.

### Found: the /ui tree ran internal RAM out (2026-10-28 10:42)

With the panel on USB the console showed `abort() was called at PC 0x481a511d`: `__wrap___cxa_allocate_exception`
<- `operator new` <- `std::string` in `PanelDebug::ui_()` (panel_debug.cpp), on loopTask, about 20 s into the
visual gate's first navigation. The firmware has `CONFIG_SPIRAM_USE_CAPS_ALLOC` without `CONFIG_SPIRAM_USE_MALLOC`,
so every `new` takes internal RAM, and no C++ exceptions, so a failed `new` aborts. The tree reached ~55 kB and was
copied twice more (the return `out + ...` and the reply's wire buffer); during the boot's image downloads internal
RAM had no such blocks (largest free block 82 kB even when idle). It aborted on three of nine installs, never in
normal use, since only the gates and agents read /ui. Fix: the tree, the job's result and the wire buffer are
`panel_debug_memory::String` in PSRAM (`components/panel_debug/psram_string.h`), reserved once, with no copy on
return; `tests/test_psram_string.py`. The next install passed every gate first time. The "tick hook" frames of
the network crash report were the panic handler's own, not the cause.
