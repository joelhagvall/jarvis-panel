#!/bin/sh
# The checked firmware installation path. Each upload restarts the panel.
set -eu
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PYTHON="$ROOT/.venv/bin/python"
ESPHOME="$ROOT/.venv/bin/esphome"
umask 077
# Every harness uses the same kernel locks. A marker alone never grants access.
if [ "${1:-}" != --panel-lock-child ]; then
  exec "$PYTHON" "$ROOT/tools/panel_lock.py" run --lock workspace -- \
    sh "$ROOT/tools/flash_panel.sh" --panel-lock-child "$@"
fi
"$PYTHON" "$ROOT/tools/panel_lock.py" verify --lock workspace
shift
if [ -f "$ROOT/.panel-ci/quarantine.json" ]; then
  printf 'Physical installation stopped: unresolved CI quarantine at %s\n' "$ROOT/.panel-ci/quarantine.json" >&2
  exit 1
fi
# The whole run also goes to a private log, so a failure can be read afterwards even when the terminal output was
# lost (2026-10-30: one failed flash could not be diagnosed). POSIX sh has no pipefail: tee reads a FIFO instead of a
# pipe, so the script's own exit status stays the result.
RUN_LOG="$ROOT/.panel-ci/flash-logs/$(date +%Y%m%d-%H%M%S)-flash.log"
mkdir -p "$ROOT/.panel-ci/flash-logs"
FIFO=$(mktemp -u "${TMPDIR:-/tmp}/hallpanel-flash.XXXXXX")
mkfifo "$FIFO" "$FIFO.err"
: > "$RUN_LOG"
tee -a "$RUN_LOG" < "$FIFO" &
TEE_PID=$!
tee -a "$RUN_LOG" < "$FIFO.err" >&2 &
TEE_ERR_PID=$!
exec > "$FIFO" 2> "$FIFO.err"   # stderr stays stderr, in the same log
rm -f "$FIFO" "$FIFO.err"
printf 'Flash log: %s\n' "$RUN_LOG"
DIRECT=
case "${1:-}" in --direct-display|--double-buffer|--fast-display) DIRECT=$1; shift ;; esac
# Select once before costly checks. No opportunistic first-USB selection.
DEVICE=${1:-hallpanel.local}
# The last line always says how it ended, also for a background run whose wrapper reports exit 0.
STAGE=preflight
finish() {
  code=$1
  if [ "$code" -eq 0 ]; then printf 'FLASH OK: installed on %s, all gates passed\n' "$DEVICE"
  else printf 'FLASH FAILED at %s (exit %s)\n' "$STAGE" "$code"; fi
}
stop_capture() {
  if [ -n "${CAPTURE:-}" ]; then kill "$CAPTURE" 2>/dev/null || true; wait "$CAPTURE" 2>/dev/null || true; CAPTURE=; fi
  if [ -n "${SERIAL:-}" ]; then kill "$SERIAL" 2>/dev/null || true; wait "$SERIAL" 2>/dev/null || true; SERIAL=; fi
}
# Keep the script's exit status: stopping the capture first must not turn a failure into "FLASH OK".
trap 'code=$?; stop_capture; finish "$code"; exec >&- 2>&-; wait "$TEE_PID" "$TEE_ERR_PID" 2>/dev/null; exit "$code"' EXIT
"$ROOT/tools/panel" preflight --device "$DEVICE"
# Fail in seconds, not after minutes of checks, when another session holds the panel.
STAGE=device-lock
"$PYTHON" "$ROOT/tools/panel_lock.py" run --lock device -- true
# Stamp the firmware with the same input digest as CI, and say which uncommitted files go in.
STAGE=build-id
HALLPANEL_BUILD_ID=$("$ROOT/tools/panel" gates --record-build --device "$DEVICE")
export HALLPANEL_BUILD_ID
# Resolve the debug URL now: mDNS often does not answer right after an upload.
URL=$("$ROOT/tools/panel" gates --resolve-url --url "${HALLPANEL_DEBUG_URL:-http://hallpanel.local:8080}")
STAGE=checks
"$PYTHON" "$ROOT/tools/check.py" ${DIRECT:+"$DIRECT"}
STAGE=gate-baselines
# A missing/stale responsiveness reference stops before upload. First-reference
# creation is an explicit reviewed operation, never an installation side effect.
"$ROOT/tools/panel" responsiveness --check "$ROOT/.panel-ci/baselines/responsiveness.json"
"$ROOT/tools/panel" throughput --check "$ROOT/.panel-ci/baselines/throughput.json" --require-target
STAGE=visual-reference
if [ ! -f "$ROOT/.panel-visual/reference-jarvis-line-v7-hemmet/reference.json" ]; then
  printf 'Reviewed visual reference missing; installation stopped before upload.\n' >&2
  exit 1
fi
cd "$ROOT/styles/glass-v2"
# Preserve the currently built image before compiling the candidate.
if [ -f .esphome/build/hallpanel/build/firmware.factory.bin ]; then
  keep="$ROOT/backup/$(date +%Y-%m-%d_%H%M%S)"
  mkdir -p "$keep"
  cp .esphome/build/hallpanel/build/firmware.factory.bin .esphome/build/hallpanel/build/firmware.ota.bin "$keep/"
fi
STAGE=compile
"$ESPHOME" compile panel.yaml
# Keep every build's ELF by its build id: when a candidate crashes and the panel rolls back, the crash of the
# rolled-back build can only be decoded with its own ELF (2026-10-29: it had to be rebuilt by hand).
# `tools/panel crash-report` picks the running build's ELF from here. The newest 12 are kept.
if [ -f .esphome/build/hallpanel/build/firmware.elf ]; then
  mkdir -p "$ROOT/.panel-ci/elf"
  cp .esphome/build/hallpanel/build/firmware.elf "$ROOT/.panel-ci/elf/$HALLPANEL_BUILD_ID.elf"
  ls -t "$ROOT/.panel-ci/elf/"*.elf | tail -n +13 | while read -r old; do rm -f "$old"; done
fi
# Recheck after compilation in case the selected cable was unplugged.
STAGE=preflight-after-compile
"$ROOT/tools/panel" preflight --device "$DEVICE"
# The restart and the gates use up Natt's idle trigger, so the gates put a night panel back themselves.
NIGHT=$("$ROOT/tools/panel" gates --night-flag --url "$URL")
printf 'Installing on %s; the panel will restart.\n' "$DEVICE"
STAGE=upload
"$PYTHON" "$ROOT/tools/panel_lock.py" run --lock device \
  --lease-url "$URL" --allow-legacy -- \
  "$ESPHOME" upload panel.yaml --device "$DEVICE"
# Record the panel's log from its restart through the gates: a candidate that crashes is rolled back and
# only the next boot says why (2026-10-28). Not over USB, which the upload's serial port holds.
CRASH_LOG="$ROOT/.panel-ci/flash-logs/$(date +%Y%m%d-%H%M%S).log"
case "$DEVICE" in
  /dev/*) ;;
  *) "$ROOT/tools/panel" crash capture --device "$DEVICE" --out "$CRASH_LOG" & CAPTURE=$! ;;
esac
# With the panel also on USB (CRASH_SERIAL=/dev/cu.usbmodemN), its console: the only place a panic's assert text goes.
if [ -n "${CRASH_SERIAL:-}" ]; then
  "$ROOT/tools/panel" crash serial --port "$CRASH_SERIAL" --out "${CRASH_LOG%.log}.serial.log" & SERIAL=$!
fi
# Visual (the private reference is never promoted), responsiveness, throughput and the two-minute
# soak, in tools/panel_gates.py. It leaves night mode first; failed limits stop installation verification.
STAGE=gates
GATES=0
"$ROOT/tools/panel" gates --url "$URL" --wait-ready 90 ${NIGHT:+"$NIGHT"} || GATES=$?
# A gate that failed because the candidate crashed fails at once, before the rolled-back firmware has booted and
# reported the crash: keep recording a little longer then (2026-10-28 the scan missed exactly that).
[ "$GATES" -eq 0 ] || sleep "${CRASH_SETTLE_S:-45}"
stop_capture
# A new crash or rollback fails the install even when the gates passed (they could run on the old firmware
# or on a restarted candidate). When the gates failed too, their failure is the one reported.
CRASHED=0
"$ROOT/tools/panel" crash scan "$CRASH_LOG" --build "$HALLPANEL_BUILD_ID" --url "$URL" || CRASHED=$?
[ "$GATES" -eq 0 ] || exit "$GATES"
STAGE=crash-check
[ "$CRASHED" -eq 0 ] || exit "$CRASHED"
