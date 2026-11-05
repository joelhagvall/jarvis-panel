# Bluetooth speaker

Jarvis can play the radio on a Bluetooth speaker, such as Joel's JBL Charge 4. By voice: "koppla till
JBL:en", "koppla från högtalaren", "vilka högtalare finns?". While a speaker is connected, the radio
plays there instead of on the panel. Jarvis's own answers, chimes, timers and alarms still come from the panel.
In Inställningar, Ljud ut chooses it from a list; by voice, `volym` changes its volume.

## Why the Pi and not the panel

The panel's radio chip, the ESP32-C6, has Bluetooth LE only. Speakers take music over classic Bluetooth
(A2DP), which the C6 lacks, so no panel firmware can do this. The Pi 5 on Jarvis has classic Bluetooth.
Its range (about 10 m) counts from Jarvis, not from the panel.

## How it fits together

- BlueZ (`bluetooth.service`) pairs and connects. `server/agents/speaker.py` drives it with `bluetoothctl`.
- BlueALSA (`bluealsa.service`) carries the sound. Its ALSA device `bluealsa` is the speaker connected last.
- MPD (`hallpanel-mpd.service`, a user service, `server/bluetooth/mpd.conf`) plays the streams on
  127.0.0.1:6601. `radio.py` sends them straight to MPD (`speaker.play_url`): Home Assistant's MPD player
  took 12.3 s to answer one `play_media`, MPD itself under 10 ms.
- Home Assistant shows MPD as `media_player.bluetooth_hogtalare` (`ha/ha_tool.py setup`). `server.env` holds
  `JARVIS_SPEAKER_PLAYER` with that entity. The voice tool `hogtalare` is offered only once it is set.
- `radio.py` asks `speaker.output()` on each play: the connected speaker, else the panel. The radio follows the
  output both ways: connecting a speaker moves a playing radio there, and disconnecting (also "spela på
  panelen", Ljud ut = Panelen) moves it back to the panel. By voice the radio also pauses, resumes and stops
  after a number of minutes (a sleep timer in the server's memory: a restart of `hallpanel.service` forgets it).
- Switched on by hand, the JBL reconnects to Jarvis by itself (when Jarvis was its last host).
  `speaker.Watcher` in `hallpanel.service` checks `bluetoothctl devices Connected` every 2 s (10 ms a check;
  D-Bus monitoring needs root). A change that holds for two checks moves a radio playing on the panel to the
  speaker. A speaker that is switched off or goes out of range stops the radio there: it does not move to the
  panel, since switching the speaker off means quiet. Changes made by Jarvis's own connect/disconnect are left
  alone. Each change is a `speaker: ...` line in `journalctl --user-unit hallpanel` (not `-u`, which finds
  nothing). Tested 2026-10-27 with `bluetoothctl disconnect`/`connect` around Jarvis: off with the radio on the
  panel, on (P3 moved to the JBL), off again (stopped, panel stayed quiet).
- After a wakeup, `input_select.jarvis_vackning_radio` (Radio efter väckningen) starts a channel through the
  server's `POST /radio`, which first connects the paired speaker if it is on (docs/wakeups.md).
- While the panel listens, `ha/custom_components/jarvis_agent/speaker_duck.py` turns MPD down to a sixth of
  its volume (a tenth until 2026-10-28). While Jarvis answers it plays at 40 % (a quarter before), so the radio
  is heard under the reply (Joel's choice; he wanted a little more music); a tenth sounded as if it had stopped. Then it goes back. Without it the music drowned the
  question: the satellite listened 3-4 s and heard nothing.

- The panel's Inställningar has a `Ljud ut` list (`styles/glass-v2/gen.py`, `choice_row`; Bakgrund is one too):
  `select.jarvis_ljud_ut` (Panelen and the paired speakers) gives its entries in its `panel_options` attribute,
  and picking entry i runs the panel's pick script i, which asks HA for the select's own option i (a template:
  the generated config is JSON, without `!lambda`). The select calls the server's `POST /speaker`, which connects
  or disconnects. HA polls `GET /speaker` every 10 s, so a speaker turned off shows Panelen soon after.
- **Koppla om** (2026-11-01, Joel wanted to fix a silent JBL himself): the round arrow button left of the Ljud ut
  list (`gen.py` `reconnect_button`), also `button.jarvis_koppla_om_hogtalaren` in Home Assistant. It calls the
  server's `POST /speaker/reconnect` (`speaker.reconnect`): disconnect the speaker, connect it again (a failed
  first try switches Jarvis's adapter off and on, which cleared bluetoothd's stuck "Operation already in progress"
  that day), start a radio that played there again, then heal PipeWire, WirePlumber, jbl45 and Soloist in the
  background (`watch.heal`, docs/spotify.md). Spotify plays on only if it played here; paused here, Soloist is
  activated without play. Silent Spotify with a working JBL was a stream volume of 0 (docs/spotify.md), not the link. The Soloist watch is held off meanwhile (`watch.hold_off`) so it does not heal the same
  drop twice. The panel's note now says "Kopplar om …" until the actual HA action returns (at most 80 s), then
  "Återansluten · ljudet återställs" only on success. That confirms the Bluetooth link, not acoustic recovery;
  healing still runs in the background. Refusals and timeouts remain visible until another explicit action.
  Log: `speaker: omkoppling ...` and `speaker:
  omkopplad ...` in `journalctl --user-unit hallpanel`. A speaker that does not come back answers "svarar inte. Är
  den på, inom räckhåll och inte kopplad till en telefon?": Joel's phone holding the JBL blocked Jarvis that day.
  Ljud ut uses the same status in Inställningar and Spelas nu. A selection waits for both its service response
  and HA's matching current choice (up to 45 s), in either order. Re-selecting the current output still needs
  a successful service response. Request tickets reject late callbacks; overlapping switch/reconnect actions
  are ignored and the visible dropdown returns to the pending/confirmed selection. No automatic retries.
  `styles/glass-v2/output_feedback.h`, `tests/test_output_feedback.py` and `tests/test_choice_list.py` own the
  state contract and generated actions. Panel transitions/refusals/timeouts log under `output` (`tools/panel logs`).
  Backend failure reasons remain in Jarvis's journal above. Spotify's informational tooltip cannot cover a pending/error line.
  HA 2026.10's ESPHome notification handler does not catch a plain `HomeAssistantError`: a real Bluetooth
  failure escaped it and left the panel waiting 45 s. `select.py` and `button.py` translate backend refusals
  to `ServiceValidationError`, which that handler returns to ESPHome. `OutputResultTest` in
  `tests/test_media_ha.py` checks both failures and confirmed state updates in the actual HA runtime.
  The 2026-11-02 physical UI check is retained in `.panel-ci/output-live-ui-20261102/`: choice and reconnect
  displayed waiting, then actual Bluetooth failures after about 20/46 s; reselecting Panelen showed confirmed
  success at the first poll. These observation times start after the remote tap returns, not at finger contact.
  JBL did not reconnect in that test, so this establishes UI/error propagation, not sound recovery.
- bluetoothctl on Jarvis began colouring its lines without a terminal on 2026-11-01 (after 13:33; version 5.82,
  unchanged since August, cause unknown). `speakers()` then found nothing and Ljud ut showed only Panelen; `_ctl`
  strips ANSI codes since (tests/test_speaker.py).
- `Spelas nu`: `radio.py` fires the HA event `jarvis_agent_speaker` with the channel when it plays on the speaker,
  and `media.py` shows it while the panel plays nothing itself. Its wire gets a seventh field `1`, so the panel's
  own player going idle after a reply does not hide the row (`now_playing.h`); the field holds the speaker's
  name, so the row says "P3 på JBL Charge 4". Pause and stop go straight to MPD. The sheet's volume slider sets
  the speaker then: one panel script per 5 % stop calls `jarvis_agent.speaker_volume`, and HA's `speaker_volume`
  attribute (the level the ducking returns to) places the knob.
- The speaker's battery is not shown: the JBL Charge 4 offers no hands-free profile or BlueZ battery service,
  only JBL's own app protocol (checked 2026-10-27).
- The song comes from the stream itself: SR's streams carry it as ICY metadata, which MPD shows as the current
  song's Title (`speaker_title.py`). It follows what is heard; SR's API was minutes behind on 2026-10-27 (it still
  named a song that started 4.5 min earlier). MPD's idle is not verified to report title changes, so the title is
  also read every 5 s.
- After Home Assistant restarts, `media.py` asks MPD what plays and SR for the channel's name and logo (the
  stream URL holds the channel id), so Spelas nu comes back by itself. During HA's startup MPD did not answer
  in time at first, so it asks up to six times, 10 s apart.
- Volume by voice: the tool `volym` (`server/agents/volume.py`) sets, raises, lowers or reads the volume where
  the sound plays: the speaker while one is connected, else the panel, unless Joel names one. The speaker's goes
  through `jarvis_agent.speaker_volume`, which changes the level the ducking returns to while Jarvis listens.

## Setup (once)

1. Joel, in his own terminal (it needs sudo): `ssh jarvis 'sudo sh ~/hallpanel-live/server/bluetooth/setup.sh'`.
   It installs `bluez-alsa-utils` and `mpd`, turns on `bluetooth` and `bluealsa`, and switches off
   Debian's system-wide MPD.
2. From the Mac: `sh tools/deploy_jarvis.sh`. It installs `hallpanel-mpd.service`, adds the HA media player
   and sets `JARVIS_SPEAKER_PLAYER`.
3. Hold the speaker's Bluetooth button until it blinks, then on Jarvis:
   `~/hallpanel-live/venv/bin/python3 ~/hallpanel-live/server/agents/speaker.py pair jbl`.
   It scans for 20 s, then pairs, trusts and connects. Pairing is a terminal step, not a voice one.
   The JBL dropped the connection once right after its first pairing; `speaker.py connect jbl` held.

## Check it

- `speaker.py status` lists paired speakers and which is connected.
- The switch, from a terminal on Jarvis (radio playing): `speaker.py connect panelen`, then `speaker.py connect jbl`.
  A new process finds the channel from MPD's stream URL (speaker to panel) and from HA's Spelas nu sensor
  (panel to speaker), so this also holds after `hallpanel.service` restarted. Load the server's settings first,
  or Jarvis knows no speaker player: `set -a; . ~/.config/hallpanel/server.env; set +a`, then run from
  `~/hallpanel-live/server/agents` (`radio.py play P3` starts a test; `radio.py stop` ends it). Read HA there with
  `python3 -c 'import hass; print(hass.get_json("/api/states/sensor.jarvis_uppspelning"))'`; the token is a file,
  not an environment variable. Passed both ways on 2026-10-27.
- `bluetoothctl info MAC` must say `Bonded: yes`. Jarvis's adapter starts with `Pairable: no`, so a pairing
  without `pairable on` connects once and then drops. `pair` switches it on only for the pairing.
- BlueALSA plays one client at a time: a second `aplay -D bluealsa` reconfigures the speaker and stops MPD's
  radio. Jarvis's answers on the speaker would need a mixing stack such as PipeWire (Joel chose not to, 2026-10-27).
- The panel's 2-minute soak gate failed twice while radio played on the speaker (one 96 ms frame, orb prefetch
  fallbacks) and passed with the music stopped: the Spelas nu updates (text and cover) disturb the orb. Not yet
  diagnosed; run the gates with music idle, and a visual reference must be recorded with music idle too.
- `systemctl --user status hallpanel-mpd` and `journalctl --user-unit hallpanel-mpd`: an ALSA error on play
  means no speaker is connected.
- Free tests: `tests/test_speaker.py` (fake `bluetoothctl`), `tests/test_radio.py`.
