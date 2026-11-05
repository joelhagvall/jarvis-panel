# Spotify on the JBL through Spotify Soloist

Status 2026-10-28: paired with Joel's account and playing. Spotify plays on the JBL Charge 4 (Bluetooth, the
same speaker as the radio), not on the panel's own speaker. Joel chose this.

## By voice

The tool `spotify` (`server/agents/spotify.py`, on when `JARVIS_SOLOIST_WS=127.0.0.1:9431` is in server.env,
which `tools/deploy_jarvis.sh` sets once the key is there) runs `soloist ctl --ws ...`: play a link/URI, or a
query (Soloist has no search: Spotify's Web API searches when `SPOTIFY_CLIENT_ID` and `SPOTIFY_CLIENT_SECRET`
are in server.env, else, or when it fails, the web search looks for open.spotify.com links; the asked type comes
first), pause, resume, next, previous, volume 0-100, shuffle on/off, add a song to the queue (`add_to_queue`:
Soloist queues tracks only) and status (track, artists, album, playlist, position, `active`).
Before playing it stops MPD's radio on the speaker and connects the JBL when it is off. Joel's question is not
drowned: `SoloistDuck` in `ha/custom_components/jarvis_agent/speaker_duck.py` turns Spotify down to two thirds
while the panel listens and leaves it at full volume while Jarvis answers (`SPOTIFY_LISTEN_SHARE`,
`SPOTIFY_SHARE`; Joel 2026-11-01: "jag vill ha musiken rätt högt när jag pratar"; a sixth and 40 % from
2026-10-28, a tenth and a quarter before). The radio keeps its own, stronger ducking (`SpeakerDuck`). If his
questions start to get lost under loud Spotify, that is the trade-off to show him, not to undo silently. Only while Jarvis is Spotify's active device
(`speaker_duck.playing_here`: with `is_active` false `set_volume` would turn down Joel's phone), over the same
WebSocket (`set_volume`; Soloist sends `playback_state` with the volume on connect). Tests:
`tests/test_spotify.py`, `tests/test_media_ha.py` (SoloistDuckTest, in Home Assistant's runtime: `sh tools/ha_runtime_tests.sh`).
Spotify started during a voice turn ("Spela Kent på Spotify") is turned down as soon as it plays, so "Spelar Kent"
is heard; before 2026-10-28 only music already playing when the panel began listening went down.
Jarvis answers a play with what plays only ("Spelar Kent.", never "på högtalaren"; Joel, 2026-10-30), and Swedish
speech names the speaker "högtalaren", never "JBL:en", "JBL-högtalaren" or "JBL Charge 4" (`voice.spoken_names`,
tests/test_voice.py); the screen keeps the speaker's own name. Music Jarvis starts in a voice turn ends that turn on
Spelas nu (docs/now-playing.md).

On the panel, Spelas nu shows it ("Spotify på JBL Charge 4 · artist", the song, Spotify's cover) whenever
neither the panel nor the radio plays: `ha/custom_components/jarvis_agent/spotify_state.py` keeps a WebSocket to
Soloist open for its pushed `playback_state`, `media.py` shows it, and the row's pause/stop and the sheet's
next/previous and volume go back to Soloist (stop is a pause, so Spotify keeps its queue, but `media.py` reports
it idle until it plays again: `spotify_stop`, logged as `Spotify stopped from the panel`). Covers are fetched
only from `i.scdn.co` (`media_artwork.spotify_image`).

Sound: with PipeWire's defaults the JBL stuttered (95 xruns, BlueALSA "PCM drop/resume"); the sink now uses
period wakeups, no mmap, 2048-frame periods and 44.1 kHz (`server/soloist/pipewire-jbl.conf`). Check with
`pw-top -b -n 2` (the ERR column of `jbl`) and `journalctl -u bluealsa | grep -c 'PCM drop'`.
Silent while "playing" (2026-10-29): the JBL dropped off Bluetooth (bluealsa `Freeing device` 09:20:59) and came back
at 09:31; Soloist still said `playing` (its position ran past the track's length), but `pw-top` showed `spotify`
and `jbl` idle/suspended and `bluealsa-cli info .../a2dpsrc/sink` said `Running: false`. Pause/play did not help;
`systemctl --user restart hallpanel-soloist`, then `soloist ctl --ws 127.0.0.1:9431 activate` and `play` did
(`jbl` R, `Running: true`). `server/soloist/watch.py` now does that by itself: `hallpanel-soloist-watch.timer` checks
every 30 s, restarts after two silent checks while the JBL is connected (about a minute), at most three times an
hour, and never while paused, with the JBL off or while another device (Joel's iPhone) is Spotify's active
device: Soloist's `now` says "playing" for the phone's playback too, and on 2026-10-29 the restarts that caused
took the music from the phone every ~70 s; only `is_active: true` counts.
Silent after the JBL came back (2026-10-30 13:18, 2026-10-31 07:55): switched off mid-play and back on 8 s later,
the JBL reconnected by itself, but PipeWire's `jbl` sink kept the dead BlueALSA PCM (`spa.alsa: Could not look up
revents: No such device`, 17 500 lines at the drop) while `spotify` and `jbl` both said running, so the silent check
missed it. Restarting Soloist alone did not help. After `systemctl --user restart pipewire wireplumber` a direct
`pw-play --target jbl /usr/share/sounds/alsa/Front_Center.wav` was heard, but Spotify (Soloist restarted 3 s after
PipeWire) stayed silent until Soloist was restarted once more with activate and play. `pw-record --target jbl -P
stream.capture.sink=true` read peak 0 both while silent and after the sound was back, so it is no silence test;
Joel's ear and the `pw-play` test were. The watch
now remembers a drop (BlueALSA `Freeing device: <JBL>` in `journalctl -t bluealsa`) while Spotify played here and,
when the JBL is back, restarts PipeWire, WirePlumber, `hallpanel-jbl45` and Soloist and resumes only if Spotify
still plays here (`heal` in `watch.py`, same three-an-hour budget, logged as `soloist-watch: ... heal`). A drop with
nothing playing needs nothing: the nine such reconnects that week all sounded.
Never restart PipeWire while the JBL is off Bluetooth (2026-11-01 13:35): the `jbl` sink cannot open its BlueALSA
PCM (`PCM not found`, exit 234), `pipewire.service` hits its start limit and `pipewire.socket` fails with it. Clear it
with `systemctl --user reset-failed pipewire.service pipewire.socket wireplumber.service` and
`systemctl --user start pipewire.socket`; the watch's `heal` starts PipeWire again when the JBL is back.
Silent with everything "running" (2026-11-01 13:32-14:28, found 14:28): Soloist said playing, `spotify` and
`jbl` ran in `pw-top` with 0 errors, BlueALSA said `Running: true`, `pw-play --target jbl` was heard, yet Spotify was
not. The cause: the `spotify` stream's main volume was 0 (`pw-dump`: `"volume": 0.0`, `channelVolumes` 0.064 =
Soloist's 40 %). The voice path's `_wake_link` played its silence with `pw-play --volume 0`; WirePlumber saved that
as the volume of media.role Music (`~/.local/state/wireplumber/stream-properties`, `Output/Audio:media.role:Music`)
and restored it onto every Soloist stream created after it (a new play after a pause, a restart). Any sound at full
volume overwrote it, so a `pw-play` test followed by a Soloist restart "fixed" it (13:52-13:53), which looked like a
Soloist bug. Disproved on the way: the codec switch at a play's start, the Soloist build (1.3.10.22 behaves the
same), the sink's idle suspend, and a restart of Soloist alone. Fixed three ways: `_wake_link` has its own role and
no `--volume 0`; `wireplumber-jarvis.conf` sets `state.restore-props = false` for Spotify; the watch sets a `spotify`
stream at volume 0 back to 1.0 while it plays here (`muted_spotify`, logged `played at volume 0`). By hand:
`pw-cli set-param <spotify node id> Props '{ volume: 1.0 }'`. Check: `watch.py --check` shows `spotify_volume_0`.
Test order for a silent JBL: `pw-play` (Bluetooth), then the `spotify` node's volume in `pw-dump`, then Soloist.
A 3 s `pw-record` of the `spotify` stream is no silence test: it read 2 048 frames, peak 0 while Joel heard the
music, and as a watch check (14:00-14:13) it restarted Soloist and cut a song (`NoStreamCaptureTest`).
Shuffle by voice (2026-11-01 14:36, "shuffla mina gillade låtar", heard as "tjaffla"): the model called play and
shuffle; shuffle ran during the reply, then the play deferred until after it started Liked Songs at their first song,
in order. Now play takes `shuffle` (one call; the tool description asks for it), applied after the start confirms,
with a `next` so the first song is random too (`_shuffle`, logged `spotify: shuffle on and next, after a fresh
start`). A separate shuffle call during a voice turn waits until Jarvis has spoken and a play from the same turn has
started (`_shuffle_after_reply`). Shuffle while music already plays does not skip. Tests: tests/test_spotify.py.
Shuffle is never the default (Joel, 2026-11-01): Spotify keeps shuffle on after a "shuffla", and the next "Spela
Coldplay" began on a random song. Every new play of a song, album, list or artist sends `shuffle off` before `play`
unless shuffle is asked for (`_in_order`); resume leaves shuffle as it is.
Stuck at a track's end (2026-10-29 several times, 2026-10-31 07:59-08:07): Soloist logged `playing ... [2:52 /
2:52]` every 10 s and never went on; the panel showed 2:52 / -0:00. `spotify.py next` went on at once. The watch
reads Soloist's last position line (`journalctl _SYSTEMD_USER_UNIT=hallpanel-soloist.service --grep 'playing
spotify:'`) and sends `next` after two checks at the end while it plays here (`skip`, logged `soloist-watch:
... stuck at the end`). Why Soloist stops there is not known. After that fix Spotify itself still
moved the phone's playback back to "Jarvis" at a track change (11:34:23, no local command in any log). To keep
Jarvis out of Connect: `systemctl --user stop hallpanel-soloist` (the nightly updater restarts it only when it
runs; voice "spela ..." fails until `systemctl --user start hallpanel-soloist`). Its log: `journalctl --user-unit hallpanel-soloist-watch` (only
lines that change something) and `~/.local/state/hallpanel/soloist-watch.json`. `python3
~/hallpanel-live/server/soloist/watch.py --check` prints what it sees (playing, JBL connected, `spotify`/`jbl` node
states) without acting. Tests: `tests/test_soloist_watch.py`.

## From an agent

Any agent (or Joel's terminal) plays Spotify on the JBL with the voice tool's own code on Jarvis, over
`tools/jarvis_ssh.sh user@jarvis.local` from the Mac:

```sh
cd ~/hallpanel-live/server && set -a && . ~/.config/hallpanel/server.env && set +a
~/hallpanel-live/venv/bin/python agents/spotify.py play --album "Ghost Stories Coldplay"
~/hallpanel-live/venv/bin/python agents/spotify.py pause   # resume, next, previous, volume 40, status
~/hallpanel-live/venv/bin/python agents/spotify.py shuffle on   # off; queue "titel artist" or a track link
```

`play` takes a query (web-searched for an open.spotify.com link, the asked type first) or a Spotify link/URI,
stops the radio, connects the JBL when it is off and answers what Soloist reports playing.

Silent live check on Jarvis, no playback: `cd ~/hallpanel-live/server && set -a && . ~/.config/hallpanel/server.env
&& set +a && ~/hallpanel-live/venv/bin/python agents/spotify.py status` (or `search "titel artist"`).

Not verified by voice yet: a spoken "spela ..." end to end, and whether the JBL hands over when it is
already connected to the Apple TV.

## Route

Spotify app or Jarvis → Spotify Soloist on Jarvis, the Spotify Connect speaker "Jarvis"
(`server/soloist/run.sh`, user service `hallpanel-soloist.service`) → PipeWire sink `jbl`
(`server/soloist/pipewire-jbl.conf`) → BlueALSA → the JBL.

- Soloist is Spotify's own headless client; its docs name a Raspberry Pi as a network speaker
  (https://developer.spotify.com/documentation/soloist/features). The account that makes the API key needs
  Premium. The key is Joel's own, kept on Jarvis at `~/.config/soloist/api-key` (mode 600), never in chat or git.
  Soloist takes it only as `-k`/`--api-key` (no environment variable in 1.3.10's `--help`), so it is in the
  process's command line, readable in `/proc` by local processes (no `hidepid`); accepted, as Jarvis has one login
  user. Checked 2026-10-30: the key is not in the journal. The data and cache directories are kept 700 by
  `tools/deploy_jarvis.sh` (they held the stored session at 775 until 2026-10-30).
- Audio: Soloist plays only through PipeWire (PulseAudio fallback), not ALSA. PipeWire and WirePlumber were
  installed for it (`--no-install-recommends`). WirePlumber's Bluetooth and ALSA monitors are off
  (`server/soloist/wireplumber-jarvis.conf`) so BlueALSA keeps owning Bluetooth for MPD's radio; the only sink is
  the static `jbl` node, which opens BlueALSA only while something plays.
- Control: Soloist's WebSocket API on 127.0.0.1:9431 (play a URI, pause, next, previous, seek, volume, state).
  It has no authentication, so it stays on loopback.
- Builds stop working 90 days after they are built (exit code 10): `server/soloist/update.sh`, run daily by
  `hallpanel-soloist-update.timer` and after an exit 10, fetches Spotify's current arm64 build, restarts Soloist
  only when it changed, and then plays on when the JBL was playing (`activate`, `play`; a run by hand at 09:03 on
  2026-10-30 left it paused before that, `tests/test_soloist_watch.py` UpdateTest). Spotify publishes
  one almost daily; Jarvis's `send-action-alerts.sh` (`check_soloist_build`, jarvis-docs) pushes once when the
  installed build is 7 days old, again with 14 days left, and silently when it is current again.
- Pairing is once, from the Spotify app on the same network ("Jarvis" under Connect to a device); done
  2026-10-28, the session is kept in `~/.local/share/soloist/data`. To pair again: the phone must reach UDP 5353
  and Soloist's discovery TCP port, which changes each start (`ss -ltnp | grep soloist`, the non-loopback
  one). Add `ufw allow proto udp from PHONE to any port 5353` and the same for that TCP port, both with a
  `TEMP Soloist` comment, pick Jarvis in the app, wait for `logged in as` in
  `journalctl --user-unit hallpanel-soloist`, then delete both rules. Joel's iPhone was phone.example.invalid (DHCP);
  Home Assistant does not report its IP.

## Logs

- `~/.local/state/hallpanel/audio.jsonl` on Jarvis: every 5 minutes (`server/audio_health.py`, a thread in
  hallpanel.service) BlueALSA's "PCM drop" lines since the previous sample, the JBL sink's PipeWire error count,
  whether the speaker is connected and Soloist's state and version; the last 2016 samples (a week). Read it with
  `~/hallpanel-live/venv/bin/python ~/hallpanel-live/server/audio_health.py` (drops per hour, the last day) or
  `--sample` for one now. A sample with drops also prints `audio: ...` to `journalctl --user-unit hallpanel`.
- Soloist: `journalctl --user-unit hallpanel-soloist` (login, every track, `client expires in N days`); its
  updater: `journalctl --user-unit hallpanel-soloist-update`.
- The voice tool's calls: the brain's line in `journalctl --user-unit hallpanel` (`spotify N ms`).
- "It stuttered at the start" (2026-10-28): `journalctl --since HH:MM:SS --until ... | grep -iE "bluealsa|underrun|xrun"`
  on Jarvis. `Starting transport: A2DP Source` at the song's start means the Bluetooth link to the JBL woke up
  then (it closes when playback pauses); BlueALSA's `PCM drop` lines come when a stream stops, not as underruns.
  `wpctl status` (with `XDG_RUNTIME_DIR=/run/user/1000`) shows whether Spotify plays straight to "JBL via BlueALSA"
  or through the 33/45 filter `jbl45`.
  Since then a voice-started song gets silence to the JBL while Jarvis answers (`spotify._wake_link`, `pw-play`),
  so `Starting transport` comes with the reply, not with the song. Which questions were heard as English: the `(en)` column of `show_turns.py 50`.
- Jarvis's port watch (`~/jarvis-docs/scripts/status.sh`) knows Soloist: its discovery TCP port changes at every
  start and is recorded as `tcp dynamic soloist`, its mDNS as `udp 5353 soloist` (2026-10-28, after new-port
  pushes at each restart). ufw blocks both from the LAN.

**Spelas nu empty after a takeover (seen once, 2026-11-03, cause not found):** `spotify.py play` took Spotify over
from another device while HA had just restarted, and `sensor.jarvis_uppspelning` stayed `idle` for minutes while
`spotify.py status` said `playing`, `active: true`. Soloist itself sent a correct `playback_state` with
`is_active: true` to a fresh WebSocket client, HA held an open connection to 9431, and no `spotify_state` debug line
showed a reconnect. A `pause` then `resume` made HA and the panel show the track at once. Check the sensor
before blaming the panel; a free regression waits for the Soloist message order that causes it.

## Before a panel flash or a visual reference

The visual gate needs Spelas nu idle, and a paused Spotify row still shows. On Jarvis:
`~/.local/share/soloist/bin/soloist ctl --ws 127.0.0.1:9431 pause; systemctl --user stop hallpanel-soloist`
(HA's row goes idle within ~5 s: check `sensor.jarvis_uppspelning`'s `wire` is `idle||||0|0`). Afterwards:
`systemctl --user start hallpanel-soloist`, then `soloist ctl --ws 127.0.0.1:9431 activate` and `... play`.

To see what Soloist sends (for example across a track change): `soloist ctl --ws 127.0.0.1:9431 trace` in one
shell and `... next` in another. A change gave `playback_state` `buffering` for about a second (2026-10-28).
Soloist 1.3.10 follows every incremental event (`playback_changed`, `track_changed`, `position_sync`) with a full
`playback_state` (traced at a natural track change 2026-10-30), so `spotify_state.py` reads only `playback_state`,
`volume_changed` and `auth_state`. Spotify's WebSocket reference promises a snapshot only "when broad changes require
it": if a later build stops sending them, Spelas nu goes stale and those events must be handled.

## What did not work (2026-10-28), so it is not tried again

- **Music Assistant 2.10.8 to the panel's speaker.** Installed, signed in to Home Assistant and Spotify, and
  searching worked, but playback failed: its librespot engine got `error audio key 0 1` from Spotify for every
  track (two tried). MA says librespot mainly works for accounts made before December 2024. Its Soloist engine
  works, but MA captures Soloist's audio and streams it on; MA itself warns Spotify's terms do not clearly allow
  that, and Spotify's developer terms forbid capturing content. Joel declined that risk. MA, its data (tokens),
  its image and the 8097 firewall rule were removed the same day.
- Logins through MA's web UI were awkward behind Jarvis's firewall: Home Assistant's sign-in and Spotify's
  callback return to jarvis.local:8095, which the Mac cannot reach; an SSH tunnel to localhost and a
  long-lived Home Assistant token worked.
- The panel (ESP32) cannot play Spotify itself, and Tailscale does not help: the ESP32 cannot run it.

Soloist 1.3.10 has no playback-speed command: `set_playback_speed` over the WebSocket API answers `unknown
command` (tried 2026-10-28 for the turntable's 45 rpm idea), though its `playback_state` reports
`options.playback_speed`. Faster playback is not possible either way, since Spotify streams in real time; a pitch
shift without a tempo change works through a PipeWire filter (rubberband LADSPA, installed 2026-10-28) in front
of the `jbl` sink: `bash server/soloist/pitch_test.sh [seconds]` on Jarvis plays Spotify that way for a moment.

The turntable's 33/45 knob (2026-10-28): the panel calls `jarvis_agent.turntable_speed`, Home Assistant asks the
server's `/speed` (server/speed.py), which moves Soloist's streams to the `jbl45` filter sink
(`hallpanel-jbl45.service`, `soloist/jbl45.conf`) and back; Home Assistant sets 33 again at the next song, a pause
or a stop (`rpm` on `sensor.jarvis_uppspelning`). Check it on Jarvis with
`curl -s -H "X-Hallpanel-Token: $T" http://127.0.0.1:8765/speed` (T from `~/.config/hallpanel/server.env`):
`{"rpm": 33, "available": true, "streams": 1}`; the panel logs `[vinyl] Turntable at 45 rpm` and where the stylus is.
End to end, with Spotify playing: `tools/panel tap-text "Spelas nu"`, then `tools/panel tap 59 411` (the knob,
`now_playing.KNOB`) and `/speed` says 45 and the attribute `rpm` too; tap again and both are 33 (checked
2026-10-28, the page at 58.6 images a second at 45).

Soloist reports the whole account's playback, also when another device plays (Joel's iPhone and AirPods): then
`status` is "playing" with `is_active` false and a nameless item. Home Assistant counts Spotify only when
`is_active` is not false (spotify_state.parse); without that, Spelas nu showed "Musik på högtalaren", and stopping
the radio fell back to the iPhone's song (2026-10-28). The same holds for control: `soloist ctl` pause, next,
prev, volume, shuffle and add-to-queue act on whatever device is active, and on 2026-10-29 a pause meant for the
JBL paused the phone. `spotify.py` runs those only while `now --json` says `is_active: true` (`HERE_ONLY`; else
"Spotify spelar inte på JBL:en just nu"), "stäng av musiken" pauses Spotify only then (`agent_core._music_off`),
and resume brings a paused session to the JBL (`activate`, `play`) but leaves one the phone is playing.
`soloist ctl` exit code 2 (connection failed) is said as "går inte att nå just nu"; 3 (not logged in, API error)
keeps Soloist's own reason.

"Spela X på Spotify" without the model (2026-10-28; through Sonnet it took 4.4-7.7 s, 2.5-5.7 s of it the web search
for the Spotify link): server/quick_command.py matches the plain forms ("spela [albumet|skivan|spellistan|låten] X
på Spotify", "spela något med X på Spotify"), spotify.quick_play searches and plays only a match holding every word of
the request (`confident`), and anything vague, long, a correction, or without a confident match goes to the model as
before. Since 2026-10-30 a bare name without "på Spotify" ("Spela Coldplay.", "Kan du spela Westlife?", "Spela mina
gillade låtar") takes the same path, as Sonnet took 2.7-3.8 s to play the same thing; radio and channel names (P1-P4,
Ekot, Din gata), "på/i/från …" (a speaker or room), "nästa", "igen", "musik" and more than six words still go to the
model (`SPOTIFY_BARE`/`NOT_MUSIC`, tests/test_quick_command.py). Since 2026-11-02 "Skulle du kunna …" is as polite as "Kan du …" (four "Skulle du kunna spela Coldplay på
Spotify?" waited 2.0-2.6 s on the model on 2026-11-01), and "sätt på lite X på Spotify" plays the artist X. On Jarvis the picks for Coldplay, Anna Book, Kent,
Starboy, Westlife, Magnus Carlsson, Pitbull, The Weeknd, Håkan Hellström and Opeth were right in 0.3-0.6 s; the
misheard "annan bok" had no confident match and stays with the model. The history marks such a turn's tool with
`"direct": true`. To see what a sentence would do without playing, on Jarvis:
`cd ~/hallpanel-live/server && set -a && . ~/.config/hallpanel/server.env && set +a && python3 agents/spotify.py try "Spela Ghost Stories av Coldplay på Spotify"`
prints the match, the search time and the pick; `"direct": false` means the model takes it. `try` uses the deployed
`quick_command.py`; to check picks for a matcher change before deploying, call `spotify.choose(query)` on Jarvis.

The search is Spotify's Web API since 2026-10-28: Joel's developer app "Jarvis" on developer.spotify.com (his
account), client credentials only, so it searches and never plays or reads his account. Its id and secret are the
two `SPOTIFY_CLIENT_*` lines in server.env, appended with pbpaste and never printed. 0.4-0.5 s a search with a
kept token, against 1.1-3.1 s for the web search. The API gives no popularity, so `confident` goes by Spotify's
order within each type (its docstring has the rules); 11 of 11 sentences picked right on Jarvis, among them
"Starboy" (The Weeknd's song, not the artist StarBoy), "Westlife" and "Kent" (the artists) and "albumet
Parachutes". When the API fails, `journalctl --user-unit hallpanel` has `spotify: Web API search failed` and the web search takes
over. A spoken turn's time is its `direct: ... spotify N ms` line there, and `python3 ~/hallpanel-live/server/show_turns.py 5`
on Jarvis lists the last voice turns with their times (no ad hoc SQL needed).
The first one by voice (2026-10-28, "Spela Starboy"): spotify 1567 ms, then 1.0 s speech.
Since then the direct play answers from the search hit at once, and by voice the music starts only once the
panel has said it (Joel, 2026-10-28: "Spelar Kent" was drowned): `spotify._after_reply` waits for
`JARVIS_PANEL_SATELLITE` to be idle again, at most 12 s; typed requests start at once. Measured on the Kent turn:
responding 20:05:10.3, idle 20:05:14.0, after the speech. A start that fails after the reply is only logged (the
reply is said): `spotify: starting after the reply, N ms`, then `spotify: direct play started after N ms`,
`... NOT confirmed within 3 s` or `... FAILED after the reply`.
"Mina gillade låtar" / "liked songs" plays the account's collection, `spotify:user:<JARVIS_SPOTIFY_USER>:collection`
(server.env; the account Soloist logs as "logged in as spotify-user"), on both paths and without a search: the search
found someone else's public playlist named "Mina gillade låtar" (2026-10-28).
The model's play waits for the reply the same way (`control`: by voice it returns just "Spelar X på JBL:en.",
which in Swedish is spoken as it is by `agent_core.direct_reply`, so never put instructions to the model there), after "Play soccer with Kent" started Socker
under a 7 s English answer (2026-10-28). That question was Swedish heard as English: transcription.py now reads
an English-heard question again as Swedish and keeps the surer reading (log: `transcription: heard as English`).
The satellite's and the media players' states over time: `python3 ~/hallpanel-live/server/show_satellite.py` on Jarvis.
