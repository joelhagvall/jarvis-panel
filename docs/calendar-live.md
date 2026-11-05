# Near-real-time calendar data

Jarvis checks iCloud calendar metadata every **10 seconds** in a dedicated
`calendar-sync` daemon thread. It is independent of the watcher's home, alarm
and reminder checks. No firmware change or running Mac is required.

The metadata request reads collection sync tokens (or CTags), names and colours
with one depth-1 CalDAV PROPFIND. Discovery is cached for an hour. The existing
private CalDAV credentials, HTTPS host validation, redirect refusal, 8-second
request timeouts and 1 MiB response limit are shared with `calendar_palette.py`.
A refused sign-in pauses metadata requests for five minutes, then retries with
fresh discovery. Repeated refusals back off to ten, twenty, forty and at most
sixty minutes. Changed credentials retry immediately. A successful request
clears the refusal. This recovers from transient iCloud 401/403 responses without
hammering a revoked password or requiring a service restart.

Only changed calendars are fetched through the existing calendar MCP's
`agenda(days=2, calendar=...)`. Deleted and renamed collections are merged
correctly. Tokens advance only after all changed-calendar reads succeed.
Failures, unparseable replies and truncated agendas retain the last good data.
There is a complete two-day refresh every five minutes and at day rollover.
Collections without a change token have a scoped 30-second fallback.
Errors back off to at most one attempt per minute.

After committing the new event snapshot, Jarvis calls the existing Home
Assistant `homeassistant.update_entity` service for `sensor.jarvis_hemmet`.
Home Assistant fetches the updated snapshot and pushes attributes over its
existing ESPHome subscription to the panel. The ordinary 15-second HA polling
remains as a fallback. No speech, alarm or actuator service is invoked by sync.
An HA refusal uses the common `hass` client's process-wide auth stop.

The target is a visible update within roughly **10–30 seconds after iCloud
has accepted a change**, under normal connectivity. iCloud upload time from
the Calendar app and arbitrary network outages are outside that target.
The existing card still shows three timed events today, or tomorrow when
today has none left; all-day events are not introduced by this change.

The calendar page behind the card (docs/glass-v2.md, Kalender) reads seven days with all-day events
separately (`server/agents/calendar_week.py`): it re-reads when this sync's `revision` changes, so it follows
the same 10–30 s, and otherwise every five minutes. A failed read keeps the last good week and is retried after a
minute; `journalctl --user-unit hallpanel | grep "calendar week"` shows failures and recoveries.

After each week read it fetches every event's details (`show_event`: alarms, notes, place, repeat, link) for the tap's
card. An edited alarm or note changes a calendar's sync token but no agenda line, so the sync's `token_changes`
count (in `/calendar/status`) also triggers a read, and then every event's details again; without a change each
event's details are re-read at most every 30 minutes. A failed `show_event` keeps that event's last details and logs
`calendar week: details failed: <type>` and how many succeeded; the notes never reach the journal.

## Shared diagnostics and physical test

```sh
.venv/bin/python -m unittest tests.test_calendar_sync tests.test_watch
tools/panel calendar-check --metadata --out /tmp/NEW-METADATA-DIRECTORY
tools/panel calendar-check --live --out /tmp/NEW-LIVE-DIRECTORY
tools/panel calendar-check --cleanup NONCE --out /tmp/NEW-CLEANUP-DIRECTORY
```

The CLI works from any directory and uses `--host` for Jarvis SSH and `--url`
for the physical panel. The deployed `server/check_calendar_sync.py` performs
the remote calendar operations using existing local credentials. Tokens and
passwords never travel in command arguments or output.

The opt-in live test creates one uniquely tagged `T <16 hex digits>`
event in Att göra, observes the actual panel's widget tree, edits its title to
`U <the same 16 hex digits>`,
observes that change and deletes it. This exact reserved test namespace is
excluded from announcements. Short probe titles keep the full identity visible:
LVGL's DOT mode also shortens text returned by the widget tree, so long fixture
titles would give false timeouts. No unrelated event or calendar is modified.
Ownership is kept in a private Jarvis state file; ambiguous writes are not
retried. An unconfirmed creation retains its cleanup record. Cleanup always
runs, and a cleanup/restore failure produces a failed report.

Measurements start when the runner receives the iCloud/MCP acknowledgement;
the report also includes request-start-to-panel time. These use the runner's
monotonic clock, not assumptions about clock alignment between Mac and Pi.
Screenshots, metadata and reports remain local/private. The original view is
restored unless a real user preempts the test. A failure captures a screenshot,
widget tree and sync status before cleanup; the report records its failing phase.

Authenticated `GET /calendar/status` on the existing Jarvis server exposes
revision, check/read/push counts, last success and error type. It contains no
event text, credentials, URLs or sync tokens. Production sync is read-only;
only the explicit live probe uses calendar writes.
