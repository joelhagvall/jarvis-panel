# Jarvis wakeups in Home Assistant

The `Jarvis väckning` YAML dashboard at `/jarvis-vackning/vackning` controls native
Home Assistant helpers. Values restore across restarts; package YAML intentionally has
no `initial` fields.

Each weekday is one row: a switch (`input_boolean.jarvis_vackning_<day>`), its own time
(`input_datetime.jarvis_vackning_<day>_tid`) and its own content
(`input_select.jarvis_vackning_<day>_typ`: Vanlig morgon = weather and calendar,
Kontorsmorgon = plus calm encouragement for Demoorten). Time and content show only
while the day is on. There is no master switch: a day that is on wakes. The old
`Veckoschema på` switch was removed on 2026-10-28, after it stood on with every day off
and "Vanlig morgon" was read as "every day". `jarvis_vackning_tid` and `_typ` remain only
as hidden fallbacks for a day whose own helper is missing.

The headline is `sensor.jarvis_nasta_vackning`'s `text` attribute ("Imorgon, torsdag
29 okt 08:45 · Vanlig morgon"). When nothing is scheduled it is empty and the dashboard
shows a warning with `status`: "Ingen väckning – inga dagar valda" when no day, extra
wakeup or snooze is on. `Bara en gång` is the one-off (`jarvis_vackning_engang`); it turns
itself off after its slot. Its `Lägg till` runs `script.jarvis_vackning_lagg_till_extra`
(tomorrow 07:00, Vanlig morgon), edited in place afterwards. Only the one-off also offers
`Egen väckning`: Joel writes a free wish (`input_text.jarvis_vackning_engang_text`, at most 255
characters, e.g. "väck mig och Alex, vi ska till Demoparken") and Jarvis writes the whole wakeup from
it, greeting included, with the same facts and rules: the wish steers tone and content, never facts.
An empty wish is a Vanlig morgon. The wish is part of the prepared file's key, so an edit after
preparation plays the reserve, never the old text; a snooze keeps the wish; the reserve and
`Provlyssna` use Vanlig morgon's reserve. The log's preparation line shows the wish. A new
`input_text` starts as `unknown` (shown as such on the dashboard); plans() reads it as empty, and
`input_text.set_value` with `""` once clears it (done on Jarvis 2026-10-31). New day helpers start at
00:00 in HA, so a fresh install sets their times through HA services before any day is
turned on. Changes to any helper immediately reschedule the native clock.

`ha/custom_components/jarvis_agent/wakeup.py` owns the clock. Native point timers
fire at the configured instant, with a minute check for preparation, restoration
and a restart catch-up of at most two minutes. An explicit one-off wins over the
weekly schedule at the same instant. A persisted claim on Jarvis prevents two
announcements for that instant, including across HA or server restarts.

Three minutes before an alarm, HA asks `server/wakeup.py` to read only that day's
calendar, SMHI and sunrise. The model request is isolated: no conversation history,
tools or interaction with the ordinary Jarvis agent. It uses the configured brain
model (Sonnet), a factual template already saved before the model starts, and then
the finished voice. The office style adds calm encouragement for Demoorten,
without guilt, forced cheerfulness or invented tasks. Missing sources stay unknown.
After today's sunrise (sun.sun's `next_rising` is tomorrow's) the fact is "Solen är redan uppe";
an empty fact is left out of the model request, since sent empty it is read as unavailable.

At the alarm time, claiming returns a finished WAV; it never calls a model or TTS.
Two persistent local reserve greetings cover unavailable/incomplete preparation.
Production reserve creation is separate from testing and plays nothing aloud.
Audio URLs have random 192-bit filenames under HA's existing `/local/` route.
They require no additional port. Private briefing JSON stays outside `www`.
Generated personal WAVs expire after two days and job/claim records after 14 days;
reserve audio is retained. Cleanup runs at preparation time.

## By voice

Jarvis reads and changes the same helpers with the tools `vackningar` and `vackning_andra`
(`server/agents/mornings.py`, 2026-11-02): a weekday on or off with its time and content, and the extra
wakeup with its moment and Joel's own instruction ("väck mig och Alex, vi ska till Demoparken" becomes Egen
väckning). There is one extra slot, so a new one replaces it and the tool's `tidigare` says which. A new
extra wakeup without an instruction is Vanlig morgon, never the previous wish; an edit keeps what is not
given. The switch is turned on last, after time, content and wish. Morgon-TV has the same pair,
`morgon_tv` and `morgon_tv_andra`. Changes show in the log below with the HA user of Jarvis's token.

## The wakeup log

Every wakeup event is one Swedish line in `/config/jarvis_logs/vackning.log` inside the HA
container (`~/docker/homeassistant/config/jarvis_logs/vackning.log` on Jarvis), for example
`[timestamp] Spelad: [demonstration content], prepared speech`.
It survives restarts and nothing rotates or prunes it. Lines: the clock starting, the next
wakeup whenever it changes (time, content, source vecka/engång/snooze; the minute tick writes
nothing when it is unchanged), preparation start and result (writer and missing sources),
a busy panel, played (content, volume, prepared or reserve text), already handled, Stopp,
cancelled, snooze, radio after, preview, every task error with its type, the night boundary
moved/restored, and each settings change: entity, old → new value and the HA user, or
`system` for HA's own changes. `WakeupController.note` buffers lines and appends them in
order through HA's executor, never in the event loop. Nothing here sends a push notification.

## Playback and night settings

The controller waits up to 30 seconds for an idle satellite/player instead of
interrupting a conversation. It confirms the requested volume before playing a
local WAV through `assist_satellite.announce`. A claimed/ambiguous announcement is
never automatically replayed. A full software test cannot establish audible
volume, wake effectiveness or behavior during an actual device outage.

The panel caps night volume at 35%. When an alarm falls within its night interval,
HA temporarily moves the night end to one minute before the alarm, allowing the
panel's normal two-second night check to leave night mode before the 65% request.
The original boundary and restoration instant persist through HA's Store API.
It restores at the original morning boundary, only if the setting is still the
controller's value. A later manual change is preserved. Ordinary daytime volume
stays at the selected wake volume, matching Joel's first working wakeup.
Disabling an armed future alarm restores its temporary night boundary. A cold
restart after the intended night edge uses the next minute boundary; actual audio
can then start up to roughly a minute later than the catch-up request. Preview
does not alter night boundaries.

`Radio efter väckningen` (`input_select.jarvis_vackning_radio`: Av, P1, P2, P3, P4 Stockholm) starts that
channel once the greeting has played, on the Bluetooth speaker if it is on and answers, else on the panel
(docs/bluetooth-speaker.md). A failure there is logged and never fails the wakeup; previews start no radio.

`Provlyssna` uses the next wakeup's content (Vanlig morgon when none) and its local reserve, with no model call.
It restores the previous volume afterwards. `Stopp` only stops a wakeup this
controller owns. ESPHome's normal `media_stop` targets music, so the controller
uses the existing native API's STOP command with `announcement=True`.
`Snooze 5 min` stops the owned announcement and schedules a one-off five minutes
later with the same morning type. It leaves the weekly time alone, and is available
for 30 minutes after a wakeup. `Stopp` also cancels a pending snooze.
The dashboard status shows a finished outcome (klar/stoppad) only during that
30-minute window; afterwards it shows `Schemalagd` when the clock is armed. Snooze and
Stopp are shown only while `binary_sensor.jarvis_vackning_pagar` is on: a wakeup plays,
a snooze is pending, or the last wakeup was within 30 minutes (the sensor's `pagar`).
Errors stay visible until the next wakeup.

On the panel (2026-11-02): Natt shows the next wakeup from the sensor's `panel` attribute (`panel_line`:
"Väckning 08:45" within a day, "Väckning mån 08:45" later, "Snooze till 08:50", "Ingen väckning"), and the
Jarvis card's Härnäst shows it from 18:00. While `binary_sensor.jarvis_vackning_pagar` is on, **Snooze** and
**Stopp** stand left of the tab bar on Gå hemifrån and Hemmet (gen.py `wake_strip`). A tap on the voice page
silences the playing wakeup and returns there. Snooze calls `jarvis_agent.wakeup_snooze` and reads "Snoozad";
Stopp calls `jarvis_agent.wakeup_stop` and hides the strip until the sensor goes off and on again. The panel
log has `Wakeup: Snooze pressed`, `Wakeup on: yes/no` and a WARN line when HA refuses.

## Install and verify

Run the checked release workflow, including the real-HA tests, before installation.
Copy `ha/jarvis_wakeup.yaml` and `ha/jarvis_wakeup_dashboard.yaml` into HA's `/config`.
Run `tools/setup_wakeup_ha.py /config` inside the HA container. It supports the
existing mapping-based packages/dashboards, backs up `configuration.yaml`, and
checks that only its two entries changed. It never edits raw `.storage` files.
Validate HA configuration, then use `tools/deploy_jarvis.sh --restart-ha`.
Later deployments update the two YAML files when the package is already installed.

Initialize controls through HA services on the first installation only. A helper removed from the
package stays in HA's entity registry as `unavailable`; remove it with the websocket command
`config/entity_registry/remove` (run from the HA container's Python), never by editing `.storage`. Native
helper edits thereafter are the source of truth. Prepare reserve audio via the
authenticated server `POST /wakeup/reserves`, without playing it. Check the next
wakeup sensor, future weekday plans, service registration, dashboard YAML and
that the reserve files can be served by HA. Do not run a paid model test or a
speaker test merely to verify installation; Joel tries `Provlyssna` himself.

Free regressions cover date/time/weekday edits, DST, simultaneous slots, duplicate
claims across restarts, source failures, isolated model input, template readiness
during slow generation, native announcement stop, busy-session protection,
five-minute snooze, preview volume restoration and preservation of later night edits.

Restart and delivery fault handling, prepared-WAV validation and the separate
native Assist timer persistence limit are documented in [reliability.md](reliability.md).
