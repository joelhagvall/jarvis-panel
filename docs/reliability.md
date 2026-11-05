# Restart, delivery and cancellation reliability

Fault checks and checked installation on 2026-10-25. These checks use fake providers,
temporary databases and the installed HA classes. They do not run paid model, STT
or TTS evaluations, trigger real alarms or send test notifications. Ordinary service
startup warm-up remains normal production behavior.

## Reminder delivery

Previously the watcher set `done` before contacting a destination. A rejected or
failed delivery disappeared from pending reminders. A failure also interrupted the
remaining work in that tick.

The existing SQLite table now has an additive `attempted` column. An atomic claim
is committed before dispatch. Only an acknowledged panel/phone service request
marks the reminder done. A known non-submission releases the claim for a later
20-second tick. A missing HA token, an explicit authentication refusal or a refused
connection is known not to have delivered; other transport/service errors are
treated conservatively as uncertain.

Uncertain delivery stays in the database, on the panel and in the reminder list as
`Leverans ej bekräftad`. It is not automatically replayed after a restart. A timeout
after acceptance must not cause a duplicate phone fallback. Separate database
connections cannot claim the same pending reminder. Cancellation still removes a
pending/uncertain reminder from the list. The existing six-hour expiry applies to
unattempted overdue reminders, not to an unresolved delivery claim.

An acknowledged HA service call does not prove that Joel heard the speaker or read
a notification. There is no exactly-once audible-delivery guarantee across a crash
between an external side effect and its response. Uncertainty is retained rather
than being labelled success or retried blindly.

The live database was backed up consistently with SQLite before migration, under
`~/.local/share/hallpanel/backups/reliability-20261025/` on Jarvis. No rows were deleted.

## Wakeups and prepared sound

- A failed preparation releases its in-memory guard, allowing the next minute's
  preparation tick to retry. A successful preparation is not repeated each minute.
- A busy/disconnected panel or unconfirmed volume does not consume the durable
  playback claim. Pre-dispatch failures may retry inside the existing two-minute
  catch-up window. Stop/cancellation and uncertain claim requests do not auto-repeat.
- The night-boundary override records whether it was applied. A failed setter can
  retry, preserving the original boundary and later manual edits. A missed edge
  is re-armed at a future minute boundary.
- A one-off/snooze is consumed after playback or a previously handled claim. The
  controller checks the scheduled instant before disabling it, preserving a new
  user edit made while an older alarm was playing.
- Prepared/reserve audio must have valid metadata, the expected 16 kHz mono PCM16
  WAV format and a readable final frame. A truncated candidate uses a valid local
  reserve. A corrupt reserve is not advertised as ready and cannot burn a claim.
  Claiming/status never synthesizes replacement audio or calls a model.

The durable claim still favors avoiding duplicate playback. A response lost after
claiming, or a crash after claiming but before announcement, can prevent playback.
That remains a limitation of the current at-most-once announcement protocol.

## Speech interruption

TTS text/synthesis jobs now start only when their audio response is consumed. An
unused response cannot leave reader/synthesis tasks running. Upstream text errors
propagate instead of looking like a successful empty/truncated stream. Existing
in-flight cancellation cleanup remains, and a free real-HA regression checks that
a subsequent turn can stream normally after a failed one.

## Source availability

The home summary no longer says everything is calm when all monitored data is
missing. Unavailable configured sensors are labelled as partial data, without
inventing a smoke/water alarm. A known active alarm retains priority. Failed HA
reads explicitly mark home status unavailable.

Calendar source age is bounded to ten minutes (twice the five-minute full-refresh
interval). Unchanged successful source checks refresh its timestamp too. Beyond
that bound, the panel shows `Kalendern kan inte uppdateras` and the watcher suppresses
calendar announcements. Retained old events are not presented as a verified empty
or current calendar. Other reminders/urgent checks continue independently.

## Native Assist timer limitation

HA 2026.10.4's `homeassistant.components.intent.timers.TimerManager` holds its timers
and tasks in memory and has no restore store. An isolated probe started a synthetic
timer through the actual class: the first manager had one timer; a newly created
manager had zero. No real HA timer or panel callback was involved.

Consequently ordinary Assist voice timers do **not** survive a full HA restart.
The ESPHome panel counts down received timers in RAM, and a panel reboot also loses
its local copy. Internet loss alone differs from restarting HA; a cloud-independent
clock is not a persisted clock. Wakeups and Jarvis reminders have their own persistent
storage and must not be confused with these timers.

This release does not patch HA internals or replace timer intent handlers. Making
voice timers durable requires a separate timer-storage/restore design, including
paused timers, cancellation, reconnect reconciliation and expiry while offline.

## Verification and deployment

The new fault cases reproduced failures before correction. Checked deployment
passed **529 local tests**, ESPHome configuration validation and **65 actual-HA
tests**. Installed hashes matched all nine checked runtime files. Both existing
reserve kinds validated, and the migrated production database had zero pending or
uncertain reminders. All native alarm helper values matched their pre-deploy snapshot.
The existing alarm plan was preserved. Personal schedule details are omitted.

The unchanged `tabs-20-orb-10s-v2` release gate passed its existing baseline before
and after deployment, restoring the original page. No firmware was flashed. This
25 fps diagnostic fixture is separate from production 60 fps throughput.

| Metric, ms | Before p50 / p95 / p99 / max | After p50 / p95 / p99 / max |
| --- | --- | --- |
| Remote release to redraw | 63.328 / 64.702 / 64.921 / 64.921 | 63.707 / 67.575 / 67.773 / 67.773 |
| Orb refresh completion interval | 40.960 / 42.078 / 43.757 / 44.312 | 40.922 / 42.253 / 43.670 / 44.741 |
| Presentation interval | 33.128 / 49.688 / 49.691 / 49.693 | 33.127 / 49.688 / 49.690 / 49.694 |
| Host status HTTP | 22.945 / 74.227 / 351.259 / 351.259 | 23.837 / 74.967 / 77.660 / 77.660 |

Refresh intervals above 50 ms were 0% in both runs. UI-task status snapshot p95/max
was 393/428 microseconds before and 468/531 after; HTTP worker p95/max was 3,649/4,159
and 3,073/3,912 microseconds. The slow 351 ms host response remains in the evidence.
These control measurements do not establish acoustic recovery, optical behavior or
a UI performance improvement from server changes.

The physical smoke test also passed: navigation, settings capture, input rejection,
device lease exclusion and repeated capture-buffer release. Eleven bounded synthetic
UI checks covered chat, listening/thinking/speaking, night, OTA, disconnected Wi-Fi/HA,
API timeout, missing image and expiry restoration. The original dashboard returned.
Screenshots were inspected. Those modes do not simulate real audio/network recovery.

Private raw evidence, source snapshots, fault-test logs, helper snapshots, installed
hashes and UI reports are retained in ignored `.panel-ci/reliability-20261025/`.
Reproduce free behavior checks with `.venv/bin/python tools/check.py --ha user@jarvis.local`.
