# Board checklist

What has been proven on the real panel, and how to prove the rest. Update a row when a flow is tested on the
board (date, how). "Simulator" means it was only seen in ESPHome's SDL simulator on the Mac.
Commands are `~/Documents/GitHub/agent-skills/hallpanel/scripts/panel.sh <command>`; a voice line is said to the panel.
For a flow Joel tests, start `logs SECONDS` first so the result is in the log, not only in what he saw.

## Proven on the board

| Flow | Since | How it was proven |
| --- | --- | --- |
| Boot without crash, painting at ~12.6 s, all images by ~40 s | 2026-10-23 | `baseline` (`docs/baseline/boot.tsv`) |
| Wi-Fi (2.4 GHz, `fast_connect`), Home Assistant adoption with the API key | 2026-10-23 | `serial`, `state` |
| Assistant select set to Jarvis; replies from Jarvis's agent | 2026-10-23 | `state`, `measure` |
| Sound: replies, announcements (`say`), the listening chime, volume | 2026-10-23 | `say`, Joel heard it |
| Full-duplex audio: the wake word listens while the speaker plays | 2026-10-23 | no `Parent bus is busy` in `logs` |
| "Hey Jarvis" right after an answer (0.02–0.05 s until it listens) | 2026-10-23 | `measure` (`voice.tsv`) |
| Mic button tap talks without the wake word | 2026-10-23 | `measure` |
| Tap on the voice page goes back; a tap while he talks stops him (24 ms) | 2026-10-23 | `measure` |
| Touch on every tab, Hemmet, Tavla (tap back), Natt (tap wakes), Inställningar, Klar | 2026-10-23 | `measure`, Joel |
| Natt: the screen at Nattljus 12 % (needs `gamma_correct: 1`) | 2026-10-23 | Joel |
| Rendering: orb 12.5 redraws/s at 10–11 ms; PSRAM stable at ~17 MB free | 2026-10-23 | `bench`, `measure` (`bench.tsv`) |
| OTA over Wi-Fi with the password; a wrong password is refused | 2026-10-23 | `flash hallpanel.local`; espota2 with a wrong password: "Authentication invalid" |
| OTA without flicker (XIP from PSRAM) | 2026-10-23 | Joel watched an update |
| Health sensors in Home Assistant (memory, loop time, Wi-Fi, uptime, restart reason, render) | 2026-10-23 | `health` |
| Assist timer: rings, a touch stops it | 2026-10-23 | "sätt en timer på en minut", Joel |
| Reminder spoken on the panel | 2026-10-23 | "påminn mig om två minuter": `notice:reminder` in Jarvis's history, played 21:24 |
| Radio on the panel; "Hey Jarvis" heard while it plays; stopped by voice | 2026-10-23 | "sätt på P4", "stäng av radion"; `play_media` to playing in 0.76 s |
| Mikrofon off and on by long press | 2026-10-23 | `logs`: 'Mikrofon' >> OFF, >> ON; Joel |
| Rensa empties the bubbles | 2026-10-23 | Joel (that Jarvis forgets is not checked yet) |
| Swipes left and right through Gå hemifrån, Hemmet, Tavla, Natt; the tab bar stands still while the page slides | 2026-10-23 | Joel |
| The active tab follows the page; Tavla and Natt go back to the page they came from | 2026-10-23 | Joel |
| A swipe is never also a tap (Tavla → Natt stays on Natt); night mode always ends on a tab page | 2026-10-23 | Joel |
| The boot screen (orb, "Jarvis", the boot steps), fading out when the painting is in | 2026-10-23 | Joel |
| The ready sound "Gryning" at 70 % by day; silent at night (log: `Ready sound skipped: night`) | 2026-10-23 | `serial`, Joel heard it |
| Reminder page: Hemmet's card opens it, 5 min starts a timer, +1 min adds a minute, Avbryt stops it, a reminder opens whole with Ta bort/Stäng; Ta bort's HA→Jarvis path answers (an unknown id, 404 logged) | 2026-11-03 | `tools/panel tap` + screenshots after `FLASH OK`; Ta bort on a real reminder not pressed (it would remove Joel's) |

## Not proven yet

| Flow | How to test | Notes |
| --- | --- | --- |
| The update screen: the ring fills, "Klar. Startar om …", no teal while it restarts | `flash hallpanel.local` while Joel watches | Rebuilt 2026-10-23 after the ring stayed empty |
| The ready sound stays silent after an OTA update | `flash hallpanel.local`, then `logs`: `Ready sound skipped: software restart` | |
| Rensa makes Jarvis forget | tap Rensa, then "vad sa jag nyss?" | |
| Radio feels quick: the short confirmation ("P4 Stockholm.") since 21:35 | "sätt på P4" | Before: ~10 s until full volume, his reply over the ducked radio |
| Continue conversation: Jarvis asks back and listens without the wake word | a question that makes him ask back | |
| Background change: Jarvis bakgrund to Stjärnenatten and back | `select.jarvis_bakgrund` in Home Assistant | Changes Joel's choice; set it back |
| Night mode by itself at Natt börjar, and back at Natt slutar | wait for 22:00, or move the times on the device page | Changes Joel's times; set them back |
| Smoke or leak: the panel goes to Hemmet at full brightness, at night too | no safe trigger yet | Faking `sensor.jarvis_hemmet` could reach Jarvis's notices; build a test hook first |
| The pills: no internet, Home Assistant lost | stop Home Assistant for a minute, or pull the router's WAN | Disruptive; only with Joel |
| The 07:45 refresh of NASA's picture | `health`/`logs` the next morning | |
| A crash is reported (`Senaste omstart`, `CRASH DETECTED`) | only when one happens | `decode` the addresses |

## Another firmware on the panel (e.g. the public esp32-p4-7b-esphome config)

`tools/flash_panel.sh` cannot bring Jarvis back from a firmware without `panel_debug`: before its upload it
resolves the debug URL, reads the night flag and takes a lease over port 8080. To return, upload the build
that ran before (check first that `styles/glass-v2/.esphome/build/hallpanel/build/firmware.elf`'s sha256
starts with the running `firmware_elf_sha256` from `tools/panel status`) with
`esphome upload panel.yaml --device hallpanel.local`, then `tools/panel gates --url http://hallpanel.local:8080`.
The other firmware must accept the panel's `ota_password`, keep the same Wi-Fi and C6 settings, and is tried
only with USB ready: without Wi-Fi it can only be reached over USB-C.
Home Assistant stops reconnecting to the panel once another firmware refused its API key (`Connection requires
encryption` in its log, 2026-11-03): the panel then shows `SPARAD KALENDER` and `Uppdaterar avgångar` and the
visual gate fails on `screen/5/0`. Reload the panel's ESPHome entry (`POST /api/config/config_entries/entry/<id>/reload`,
the entry titled Hallpanel) before the gates, and check `media_player.hallpanel_hallpanel` is no longer `unavailable`.
