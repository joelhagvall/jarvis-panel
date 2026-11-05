# Measurements

Historical build measurements below use ESPHome 2026.9.1, ESP-IDF 5.5.5 and LVGL 9.5.0 for the ESP32-P4.
The board arrived on 2026-10-23. Actual hardware results are in [the baseline](baseline/README.md) and
[the performance work](baseline/performance-2026-10-23.md) and
[the measured display and memory updates](baseline/performance-2026-10-24.md); those supersede the estimates below.

**2026-10-22, the full board build** (`board.py --voice jarvis_orb.py --remote ...` in `styles/glass-v2`: dashboard with live data, Hemmet, Tavla, Natt, the orb, voice with timers, night mode, the Mikrofon switch and the mic buttons) compiles for the ESP32-P4 on the Mac: flash 2,011,326 bytes, 24.8 % of one 7.75 MiB app slot (21.9 % on 2026-10-21, before all of that), and static internal RAM 132,724 of 655,360 bytes (20.3 %). The images are not in that: they are downloaded into PSRAM at boot, an estimated 10–11 MB of the 32 MB since the orb's frames are opaque (2 bytes a pixel, `orbs/opaque.py`), to be measured on the board with `--bench`.

Every number below is one of four kinds, and they are kept apart on purpose:

| Kind | Meaning | Numbers |
| --- | --- | --- |
| **Measured** | Read from a real build for the ESP32-P4 | App size per build, static internal RAM |
| **Calculated** | Arithmetic from image sizes, which will not change on the board | Image budget per orb and background |
| **Estimated** | Reasoned from the config, not observed | PSRAM in use |
| **Not measured yet** | Needs the board and `bench/bench.h` | Redraws per second, render time, PSRAM under voice and Wi-Fi load |

Only the measured and calculated numbers should be used for decisions until the board has run the bench firmware.

**Flash target:** keep the final firmware under 75–80 % of one app slot, so that voice and new assets have room. With images compiled in, liquid glass v2 with the orb reaches 87.8 %. With every image downloaded from Jarvis at boot, the stateful Jarvis orb build measures 17.9 % (built on 2026-10-21). That is the chosen route.

## Flash (measured)

Both builds below were compiled for the ESP32-P4. With 16 MB flash, ESPHome's default partition table has two OTA app slots of `0x7C0000` bytes, which is 7.75 MiB each.

| Build | App size | Share of one slot |
| --- | --- | --- |
| Main design (`python3 board.py gac`): dark cards and one painting | 3.78 MB | 46.5 % |
| Liquid glass v2 with dashboard, orb page, 24 orb frames and bench (`python3 ../../board.py --bench gen.py --demo` in `styles/glass-v2`) | 7.14 MB | 87.8 % |
| Liquid glass v2 with the stateful Jarvis orb (62 frames), all images downloaded from Jarvis at boot, and bench (`python3 ../../board.py --bench jarvis_orb.py --remote http://jarvis.local:8765/assets` in `styles/glass-v2`) | 1.45 MB | 17.9 % |

Static internal RAM is 93–95 kB of 640 kB in the first two builds. The third uses 114 kB, because it adds the HTTP client and the image downloader.

## Image budget (calculated)

RGB565 takes 2 bytes per pixel, plus 1 byte when the image has alpha.

| Asset | Size |
| --- | --- |
| Full-screen background, 1024×600 | 1.17 MiB |
| Glass v2 orb: 24 × 220² × 3 B | 3.32 MiB |
| `voice-lowkey` pearl: 16 × 160² × 3 B | 1.17 MiB |
| `voice-vercel` globe: 24 × 240² × 2 B, no alpha | 2.64 MiB |
| `voice-starry` orb (24 × 220² × 3 B) plus edge strips | 3.32 MiB + 0.37 MiB |
| `voice-futuristic` orb: 24 × 240² × 3 B | 3.96 MiB |

A full-screen animation would take 24 × 1.17 MiB, which is 28 MiB. That is why only the orb area animates.

**Conclusion:** the dashboard and one voice screen fit in one OTA slot as long as the voice screen's images stay below about 3.5 MB. Extra paintings should not be compiled in. Jarvis should serve them at runtime through `online_image`, which decodes them into PSRAM.

## PSRAM (estimate)

Images that are compiled in stay in flash and are read through the cache, so they do not use PSRAM.

PSRAM holds:
- the LVGL draw buffer: `buffer_size: 100%` is 1.2 MB
- the panel frame buffer or buffers: 1.2 MB each

Expect roughly 2.5–4 MB of the 32 MB to be in use when images are compiled in.

When images come from Jarvis, they move from flash into PSRAM. For the stateful Jarvis orb, this is calculated as:

| Item | Size |
| --- | --- |
| Two backgrounds | 2.34 MiB |
| 62 orb frames | 8.59 MiB |
| Download buffers, 64 × 8 kB, kept for good | 0.5 MiB |
| LVGL and panel buffers | about 2.4 MiB |
| **Total** | **about 14 MiB of 32 MB** |

The download buffer was 64 kB per image by default, which would have held 4 MiB for nothing. `jarvis_orb.py` sets it to 8 kB. Each painting loaded with `online_image` adds 1.2 MB. This estimate does not include Wi-Fi buffers, audio streaming or the voice assistant. Those have to be measured together with rendering, while Jarvis is listening and speaking. Confirm everything with the bench log below.

## Animation speed (measure on the board)

The simulator on a Mac says nothing about how fast the ESP32-P4 is.

`bench/bench.h` logs these every 2 seconds, per page:
- redraws per second
- average and worst render time
- free PSRAM, and the lowest it has been
- free internal RAM

`board.py --bench` also replays the voice flow every 20 seconds.

For reference only, the Mac simulator gave 13–15 redraws/s at 8 ms on the orb page, and 75–80 ms per full-screen redraw during the page fades.

**On the board:**

```sh
cd styles/glass-v2
# the chosen route: stateful Jarvis orb, images from Home Assistant on Jarvis (tools/deploy_jarvis.sh puts them there)
python3 ../../board.py --bench jarvis_orb.py --demo --remote http://jarvis.local:8123/local/hallpanel > bench-p4.yaml
esphome run bench-p4.yaml   # flash over USB-C and read the log
```

| Check | Target | If it misses |
| --- | --- | --- |
| Orb page | At least 12 redraws/s, the orb's own frame rate | Use 16 frames or a smaller orb |
| Page fade | Each frame under about 50 ms (our target, not a rule) | Use a move animation, or no animation |
| PSRAM | Stable over several voice flows | Look for a leak |
| Under load | Redraws/s and lowest free PSRAM hold while wake word, microphone, speaker and Wi-Fi run during the orb animation | Fewer orb frames, or pause the orb while audio streams |

## Agent latency (measured on the Mac)

On 2026-10-21, `server/hallpanel_server.py` was tested end to end with the real agent (`claude -p`, Claude Code 2.1.306) on the Mac. These questions used no skills:

| Question | Reply | Time |
| --- | --- | --- |
| "Hur många dagar är det kvar till julafton…" | "Det är 64 dagar kvar till julafton." | 4.6 s |
| "Och till nyårsafton då?" | "Det är 71 dagar kvar till nyårsafton." | 5.7 s |

The follow-up worked, so the history reaches the agent as context. Both turns were stored in SQLite.

## Integration test with mocked tools (measured on the Mac)

`tests/integration_test.py` runs the whole chain:

panel request → server → Claude Code → tool → reply → history

The tools come from a mock MCP server, `tests/mock_tools_mcp.py`. It logs every call, can be set to fail, and has no code that reaches iCloud or the TV. Joel's real skills are switched off (`--disable-slash-commands`), and so are Bash and every tool that writes.

The run on 2026-10-21 passed 5 of 5:

| Request | Mock | Tools called | Reply | First token | Total |
| --- | --- | --- | --- | --- | --- |
| Lägg till köp kvarg idag kl 17 i kalendern | ok | list today, then add "Köp kvarg" 2026-10-21T17:00 in Att göra | "Klart, jag har lagt in köp kvarg i dag klockan 17 …" | 8.7 s | 14.5 s |
| Boka tandläkaren på fredag klockan tio | fails | list Friday, then add "Tandläkaren" 2026-10-23T10:00 | "Det gick inte … eftersom kalendern inte svarade. Händelsen skapades inte …" | 12.5 s | 12.9 s |
| Vad har jag i kalendern i morgon? | ok | list 2026-10-22 | "I morgon, torsdag, har du standup klockan halv tio och tvättstuga …" | 5.1 s | 5.8 s |
| Stäng av tv:n | ok | tv_power(on=false) | "Klart, tv:n är avstängd." | 9.1 s | 9.7 s |
| Hur många dagar är det kvar till julafton? | ok | none | "Det är 64 dagar kvar till julafton …" | 3.8 s | 4.7 s |

Only aggregate results are distributed; raw provider replies and arguments stay private.

**What it shows:**
- Jarvis picks the right tool with the right arguments, including relative dates.
- When the tool fails, Jarvis does not claim the event was added.
- Before adding an event, Jarvis looks at the day first. That costs one extra round trip.
- The first run found a test flaw: the mock calendar did not remember added events, so Jarvis wrongly inferred that an earlier event was missing. The mock now keeps state.

**Latency:** most of the time goes to Claude Code starting up and to tool round trips. Streaming only brings the first word 0.4–0.9 s earlier, apart from the first case. So streaming text-to-speech helps less than three other things:
- saying something at once while a tool runs (the orb's "thinking" state already covers this)
- a warm agent process instead of starting `claude -p` for every question
- skipping the extra look before adding an event when it is not needed

**Not verified yet:**
- Joel's real skills. The test proves the routing and the honesty rule with mocks, not the real calendar skill over CalDAV.
- The same chain on Jarvis, where the Pi is slower than the Mac.
- The Home Assistant integration that calls `/converse`.
- How stable `claude -p` is as an always-on service.


## Benchmark: Claude Code against gpt-6-luna (measured on the Mac)

The five cases from the integration test were run on 2026-10-21 against two agents. Both used the same server, the same mock tools and the same prompt, and both passed 5 of 5:
- `claude -p` (Claude Code 2.1.306), as the server runs it today
- `gpt-6-luna` through the OpenAI Responses API (`server/agents/openai_agent.py`), with `service_tier: fast` and `reasoning.effort: none`

| Request | Claude Code, first token / total | gpt-6-luna fast, first token / total | Claude Code cost | gpt-6-luna cost |
| --- | --- | --- | --- | --- |
| Lägg till köp kvarg idag kl 17 i kalendern | 8.09 s / 8.46 s | 2.53 s / 2.99 s | 0.087 $ | 0.00022 $ |
| Boka tandläkaren på fredag klockan tio | 14.98 s / 15.39 s | 1.9 s / 2.22 s | 0.082 $ | 0.00024 $ |
| Vad har jag i kalendern i morgon? | 11.2 s / 11.6 s | 2.28 s / 2.79 s | 0.074 $ | 0.00024 $ |
| Stäng av tv:n | 5.04 s / 5.62 s | 1.87 s / 2.15 s | 0.074 $ | 0.00023 $ |
| Hur många dagar är det kvar till julafton? | 3.04 s / 3.91 s | 0.75 s / 1.1 s | 0.060 $ | 0.00012 $ |

**Summary:**

| | Claude Code | gpt-6-luna fast |
| --- | --- | --- |
| Time to first word | 3–15 s | 0.75–2.5 s |
| Total time | 4–15 s | 1.1–3 s |
| Cost per question | 6–9 US cents | about 0.02 US cents |

So gpt-6-luna fast is about 5 times faster and costs a small fraction as much.

**How these numbers were obtained:**
- Claude Code reports its own cost in `total_cost_usd`. On a subscription it is not billed, but it shows how heavy each call is.
- The gpt-6-luna cost is the token count times the prices in a pasted note: $0.20 in and $0.75 out per million tokens in fast mode. Checked on 2026-10-21 against OpenAI's pricing page, the output price is $1.00 (see Prompt caching below), so these costs are slightly low.

**Why it is not a pure model comparison:**
- Much of Claude Code's time is the CLI starting up, its large system prompt and its agent loop. That is the setup on Jarvis today, but it is not Claude's raw speed.
- A fair model comparison would be the Claude API called directly, for example with Claude Haiku 5.5 and the same tools. That is not measured.
- gpt-6-luna added the event at once. Claude Code first looked at the day, which costs one extra round trip.

**Consequence for Jarvis:**
- A direct API agent with function tools fits everyday voice commands best.
- gpt-6-luna cannot read `SKILL.md` files, so each skill it should use must become a tool, either as a function or over MCP.
- Claude Code stays for longer tasks.
- The OpenAI key lives in `~/.config/hallpanel/openai.env`, outside the repo. Both the agent and the test read it from there.

## Live on Jarvis with the real calendar (measured on the Pi)

`tests/live_jarvis_test.py` ran on Jarvis on 2026-10-21 with this chain:

gpt-6-luna fast (`openai_agent.py --mcp`) → the real `calendar-mcp` (`http://jarvis.local:3020/mcp`, CalDAV to iCloud) → reply through `server/hallpanel_server.py`

The history went to a temporary database, not the real one.

- **Read phase:** Luna was offered only `calendars`, `agenda`, `search` and `show_event`.
- **Write phase:** Luna was also offered `add_event`, and nothing that edits, deletes or undoes. The script then checked the calendar itself and removed the event with `undo_change`, using the change id from Luna's own call.

Second (warm) run:

| Request | Tool call | Calendar time | First token | Total |
| --- | --- | --- | --- | --- |
| Vad har jag i kalendern i morgon? | agenda 2026-10-22 | 279 ms | 2.73 s | 3.24 s |
| Har jag något inbokat på fredag? | agenda 2026-10-23 | 321 ms | 2.29 s | 3.15 s |
| När är nästa tvättid? | search "tvätt" | 397 ms | 2.83 s | 3.17 s |
| Lägg in Jarvis-test i Att göra i morgon klockan sju | add_event Att göra, "Jarvis-test", 2026-10-22 07:00 | 570 ms | 2.69 s | 3.11 s |

**Results:**
- All 4 passed. The write case was re-scored, not re-run: the check first demanded `T07:00`, but Luna sent `2026-10-22 07:00`, which `calendar-mcp` accepts.
- The temporary test event was verified and undone; private change IDs omitted.
- The first, cold run took 5.5 s for the first question, because the first `agenda` call took 2.5 s. After that every command took about 3.1–3.2 s in total.
- The calendar itself answers in 0.3–0.6 s. Most of the rest is the model and the agent process starting.
- Calendar results were checked against the source; personal contents omitted.

**Not measured yet:**
- the whole voice path: microphone, Swedish speech to text, text to speech
- longer conversations with follow-up questions and interruptions

## Agent kept running, and warm-up on the wake word (measured on the Pi)

`tests/latency_jarvis.py` ran on 2026-10-21. It asked the real calendar 3 read questions in three ways, with 60 s between questions. Times are from sending the question to having the whole reply.

| Way | Calendar questions | Question without a tool |
| --- | --- | --- |
| New agent process per question (`JARVIS_AGENT_CMD`) | 2.71–2.77 s | Christmas question, but it read the calendar: 3.58 s |
| Agent kept running inside the server (`JARVIS_AGENT=inprocess`) | 2.53–2.63 s | 1.15 s |
| Kept running, plus `/warm` 2 s before the question | 2.75–3.32 s | Christmas question, but it read the calendar: 2.77 s |

**Reading:**
- Keeping the agent running saves about 0.2 s on calendar questions.
- Here `/warm` gained nothing. The calendar had been used all morning and never went cold: 60 s of idle is not enough. The real cold case, a first `agenda` call of 2.5 s, came after hours of idle.
- With a tool, most of the time is Luna's two round trips, about 2.2 s. The calendar takes 0.3–0.5 s.
- Whether `/warm` helps after hours of idle is not measured. Joel stopped the 6-hour probe (`tests/idle_probe_jarvis.py`), and it can be run later. `/warm` stays in place because it costs nothing when the system is already warm.

## Swedish voices (measured)

Each voice read the same three Jarvis replies, of about 4, 5 and 11 seconds of speech. The time is until the audio file was complete. The listening page `sketch/voices.html` was removed on 2026-10-30 (git history).

| Voice | Runs on | Cost | Per sentence |
| --- | --- | --- | --- |
| Microsoft Mattias, Sofie, Hillevi | Home Assistant Cloud (`tts.home_assistant_cloud`, sv-SE), called from Jarvis | Included in Joel's subscription | 0.27–1.11 s |
| OpenAI `gpt-4o-mini-tts` Cedar, Marin, with an instruction to speak calm standard Swedish | OpenAI, called from the Mac | Per use, price not checked here | 1.2–2.4 s (one outlier of 15.5 s) |
| Piper `sv_SE-nst-medium`, `sv_SE-lisa-medium` | Locally on Jarvis, offline | Free | 0.43–1.11 s, about 0.1 s per second of speech; loading a voice takes 3 s once |
| Google Translate, macOS Alva | Comparison only | – | – |

Joel's shortlist (2026-10-21) is the male voices **Mattias**, **Cedar** and **Piper NST**. He then chose **Mattias with shortened pauses**: pauses went from 1.0–1.2 s to 0.4–0.5 s, and the three clips from 7.1 / 5.4 / 11.3 s to 4.8 / 4.4 / 9.7 s. He dropped the female voices, and `sketch/voices.html` (removed 2026-10-30) played only these three. Joel judges the sound; the agent cannot hear audio. Home Assistant also has `stt.home_assistant_cloud` for Swedish speech to text. Its speed, and KB-Whisper's on the Pi, are not measured yet.

## Wake word "hey Jarvis" (firmware measured, model tested on synthetic speech)

**Firmware.** `board.py --voice` adds:
- the microphones (ES7210) and the speaker (ES8311, amplifier on GPIO53)
- `micro_wake_word` with the `hey_jarvis` model
- the Home Assistant voice assistant, wired to the stateful orb page

Built for the ESP32-P4 with `jarvis_orb.py --remote`, it measures 1.78 MB (21.9 % of a slot). Static internal RAM is 120 kB.

**Model test.** `tests/wake_word_test.py` runs the same model through `pymicro-wakeword`. On 2026-10-21 it ran 70 synthetic clips (7 phrases × 10 voices), clean and with noise added. The model card says `probability_cutoff` is 0.97, the sliding window is 5, and it was trained on English.

| Voice | Peak on "Hej Jarvis" | Peak on "Hey Jarvis" |
| --- | --- | --- |
| OpenAI Marin | 0.996 (wakes) | 0.996 (wakes) |
| OpenAI Ash | 0.34 | 0.996 (wakes) |
| macOS Daniel (English) | 0.21 | 0.966 |
| Piper Lisa | 0.93 | 0.69 |
| Hillevi | 0.86 | 0.86 |
| Mattias, Sofie, Piper NST, Cedar | 0.00–0.35 | 0.00–0.35 |

**Findings:**
- At the default cutoff, only 7 of 40 hej/hey clips woke the model. At a cutoff of 0.85 it was 15 of 40.
- The negatives ("Hej Javier", "Hej Marcus", an everyday sentence) never woke it. "Jarvis" on its own woke it twice, with noise added.
- The Swedish voices probably say "Jarvis" with a Swedish j, which is why most synthetic voices failed. That makes these clips a poor stand-in for a person.

**Joel's own voice** (`--count` mode). On 2026-10-21 he was recorded with the Mac's microphone, close up in a quiet room. He said each phrase 4 times:

| Phrase | Woke | Peak per try |
| --- | --- | --- |
| "Hey Jarvis" | 4 of 4 | 1.0, 1.0, 1.0, 1.0 |
| "Hej Jarvis" | 2 of 4 | 0.99, 0.43, 0.49, 0.99 |

**Decision (2026-10-21): the wake word is "Hey Jarvis" with the stock `hey_jarvis` model.** The misses on "hej" peaked at only 0.4–0.5, so a lower cutoff would not fix them without waking on other things. A custom "hej Jarvis" model is not worth it now. Revisit after a week in the hallway if the panel's microphones miss often, or if others in the home say "hej" by habit.

**Before that decision:** test with Joel's real voice. If "hej Jarvis" still misses, there are two options:
- lower `probability_cutoff` for the model in ESPHome
- train a custom "hej Jarvis" microWakeWord model, using synthetic Swedish samples plus Joel's own recordings

## Whole conversation through Jarvis `/voice` (measured from the Mac)

`tools/voice_loop.py` does on the Mac what the panel will do: it listens for "Hey Jarvis", records until 0.8 s of silence, sends the WAV to `/voice` on Jarvis and plays the reply. On Jarvis, `/voice` runs Home Assistant Cloud speech to text, Luna with the real calendar, then Mattias with trimmed pauses (`server/voice.py`). Jarvis's firewall (ufw) keeps port 8765 closed to the LAN, so the Mac goes through one shared ssh connection to `127.0.0.1:8765`.

On 2026-10-21 a test recording stood in for the microphone (`--mic-file`): macOS Alex and Samantha said "Hey Jarvis", then Alva asked "Vad har jag i kalendern på fredag?" and the follow-up "Och på lördag?".

| Step | Question 1 | Question 2 |
| --- | --- | --- |
| Wait for 0.8 s of silence | 0.8 s | 0.8 s |
| Speech to text (HA Cloud) | 0.92 s | 0.47 s |
| Luna with the calendar | 3.82 s | 2.60 s |
| Mattias (HA Cloud) | 0.69 s | 0.50 s |
| Trim pauses | 0.02 s | 0.02 s |
| ssh | 0.07 s | 0.07 s |
| **End of speech to start of reply** | **6.4 s** | **4.5 s** |

**Findings:**
- The wake word fired on both. The follow-up was understood from the history ("På lördag, 24 oktober, har du inget i kalendern").
- Luna is two thirds of the wait. Home Assistant caches speech it has already made, so a repeated reply costs 0 s.
- Mattias reads "09.55–10.10" clumsily: read back through speech to text it came out as "nio och 55 till 1010". "9:55 och 10:10" came back as "nio och 55 och tio och tio". Speech to text is a poor judge of pronunciation, so Joel's ears decide whether replies need times rewritten before speech.

## Through Home Assistant's pipeline "Jarvis" (measured on Jarvis)

Since 2026-10-21 the panel's voice goes only through Home Assistant: Home Assistant Cloud speech to text, then `conversation.jarvis` (Luna on Jarvis), then `tts.jarvis_mattias` (Mattias with shortened pauses from the Jarvis server). No port was opened. `ha/ha_tool.py ask` runs the pipeline over Home Assistant's websocket API with a recorded question, the same way the panel's ESPHome connection does. The times below are inside Jarvis. They leave out the 0.4–0.5 s of ssh and `docker exec` that the Mac adds and the panel will not have.

| Question | Speech to text | Agent | Mattias (incl. MP3 for the Mac) | Total |
| --- | --- | --- | --- | --- |
| "Vad har jag i kalendern imorgon?" (first after start) | 0.66 s | 3.69 s | 1.16 s | 5.53 s |
| "Vad har jag i kalendern på fredag?" | 0.56 s | 3.06 s | 0.76 s | 4.39 s |
| "Vad har jag i kalendern imorgon?" | 0.51 s | 2.47 s | 0.78 s | 3.76 s |
| "Och på lördag?" (follow-up) | 0.52 s | 2.25 s | 1.40 s | 4.18 s |
| "Vad är klockan?" (Home Assistant answers itself) | 0.36 s | 0.01 s | 0.69 s | about 1.1 s |

**Findings:**
- Going through Home Assistant costs about as much as the direct `/voice` path (3.4–5.2 s for the same questions). The text-to-speech step is 0.1–0.4 s slower because Home Assistant converts the WAV for the client.
- Follow-up questions work: the server gives Luna the last 6 hours of history whatever conversation id Home Assistant uses (30 minutes since 2026-10-23).
- "Prefer handling commands locally" works. Home Assistant answered the time itself in about a second, and Luna was never asked.
- With the Mac's microphone loop (`--mic-file`), the time from end of speech to the reply was 4.3–6.8 s. That includes the 0.8 s wait for silence and the Mac's 0.4–0.5 s of ssh.

## Streaming the reply (measured on Jarvis)

On 2026-10-21 the Jarvis integration started streaming: Luna's text goes to Home Assistant as she writes it, and each finished sentence is spoken at once (`tts.jarvis_mattias` with `async_stream_tts_audio`). `ha/ha_tool.py ask` fetches the audio as soon as Home Assistant announces the stream, like the panel's media player. The time is from the end of the recording to the first audio byte. Six runs each, three questions, same session:

| Question (reply length) | Before streaming | With streaming |
| --- | --- | --- |
| "Vad har jag i kalendern imorgon?" (about 110 characters, one sentence) | 4.53 s, 3.18 s | 3.39 s, 3.30 s |
| "Vad har jag i kalendern på fredag?" (about 100 characters) | 3.20 s, 3.85 s | 3.25 s, 2.80 s |
| "Vad har jag i kalendern resten av veckan?" (about 175 characters, two sentences) | 4.44 s, 4.63 s | 3.61 s, 2.49 s |
| **Mean** | **3.97 s** | **3.14 s** |

**Findings:**
- Streaming saves about 0.8 s on average. Long replies gain most, because the first sentence plays while the rest is still being written and spoken.
- Luna's time varies by up to 1.5 s between runs of the same question, so six runs give only a rough mean.
- The streamed audio is complete. A 175-character reply streamed as 13.8 s of audio, against 14.3 s when spoken whole. Read back through speech to text, it held the whole reply.
- What is left is mostly Luna's time to her first word, about 2 s. That includes one calendar lookup over MCP.
- The panel firmware with the speaker media player (`board.py --voice`, `jarvis_orb.py --remote`) builds for the ESP32-P4: 1.80 MB of flash (22.2 % of a slot), against 1.78 MB with the bare speaker. Static RAM is 120.6 kB, as before.

## The coming week's calendar in the prompt (measured on Jarvis)

On the wake word the panel calls `jarvis_agent.warm`, and the warm-up has always read the calendar to wake its connection to iCloud. Since 2026-10-21 it reads today and the next 7 days, and for 2 minutes the server puts that list in Luna's prompt. A question about those days then needs no calendar lookup, so one model round trip and one tool call are gone. Any calendar change drops the list (`tests/test_agenda.py`). Other days, searches, event details and all changes still go through the tools.

Same three questions, each with a warm-up 2 s before the question, as when Joel says "Hey Jarvis" and then asks. Time from the end of the recording to the first audio:

| Question | Without the list | With the list |
| --- | --- | --- |
| "Vad har jag i kalendern imorgon?" | 3.77 s, 2.61 s | 2.29 s, 1.49 s |
| "Vad har jag i kalendern på fredag?" | 3.37 s, 3.23 s | 1.26 s, 1.26 s |
| "Vad har jag i kalendern resten av veckan?" | 3.36 s, 2.69 s | 2.53 s, 2.35 s |
| **Mean** | **3.17 s** | **1.86 s** |

**Findings:**
- The first audio comes 1.3 s sooner on average. Luna's own time on the two short questions fell from 2.1–2.5 s to 0.9 s.
- The answers named the same events as the answers that used the tool.
- The long question gains least: Luna writes a longer answer, and the first sentence ends later.
- Home Assistant's speech cache was left on in these runs, so a sentence that had been spoken before came from the cache. The comparison is fair, since both columns had it; the absolute times are a little better than for a reply never heard before (see below).

## Groq gpt-oss-20b against Luna (measured on Jarvis)

On 2026-10-21 `tools/brain.sh groq` switched the brain to Groq's `openai/gpt-oss-20b` (Chat Completions, low reasoning, the same calendar tools, prompt and prefetched week). Same procedure as above: a warm-up 2 s before each question, time from the end of the recording. Groq ran with 30 s between questions because of its rate limit (below).

| Question | Groq: agent | Groq: first audio | Luna: agent | Luna: first audio |
| --- | --- | --- | --- | --- |
| "imorgon" | 0.58 s, 1.58 s | 1.90 s, 2.34 s | 0.92 s, 0.95 s, 2.14 s | 2.29 s, 1.49 s, 3.30 s |
| "på fredag" | 0.43 s, 0.33 s | 1.84 s, 2.13 s | 0.94 s, 0.91 s, 0.96 s | 1.26 s, 1.26 s, 2.37 s |
| "resten av veckan" | 0.75 s, 0.87 s | 6.34 s, 6.41 s | 2.30 s, 1.60 s, 2.35 s | 2.53 s, 2.35 s, 3.30 s |
| "den första november" (needs a lookup) | 0.48 s, 1.31 s | 1.73 s, 2.64 s | 2.70 s, 2.33 s | 4.09 s, 2.99 s |

**Findings:**
- Groq writes faster: 0.3–1.6 s against Luna's 0.9–2.7 s. With short answers the first audio comes at about the same time, because Mattias reading the first sentence then takes most of the wait.
- gpt-oss-20b follows the voice rules worse. Asked about the rest of the week, it wrote 700 characters of bulleted markdown both times, even with "inga listor, ingen markdown" in the prompt, so the first audio came after 6.3 s. It also says "09:55" and odd phrases ("Skywave-tiden").
- It answered wrongly once. The first time it was asked about 1 November, it said there was nothing in the calendar; the second time it named the two events Luna also found.
- **Rate limit.** Groq's free tier allows 8 000 tokens per minute for this model, and one question uses about 2 800. Questions 10–20 s apart failed with 429 ("Jag kunde inte svara just nu."). A question that needs a tool sends two requests.
- Luna stays the default. Groq would need its paid Dev Tier for the limit, and probably the larger `openai/gpt-oss-120b` (`JARVIS_BRAIN_MODEL`) for the voice rules. Neither is tested.

**gpt-oss-120b on Groq** (`tools/brain.sh groq openai/gpt-oss-120b`), same procedure, 30 s apart:

| Question | Agent | First audio |
| --- | --- | --- |
| "imorgon" | 0.34 s, 0.56 s | 2.17 s, 1.99 s |
| "på fredag" | 0.70 s, 0.62 s | 2.12 s, 2.31 s |
| "resten av veckan" | 0.98 s, 0.98 s | 4.15 s, 1.89 s |
| "den första november" | 1.55 s, failed (429) | 3.39 s |

- It follows the voice rules much better than 20b: no lists or markdown, and the right events every time it answered. "Resten av veckan" was still long, one 396-character run-on sentence.
- It writes times oddly, "9 55–10 10" with spaces. Joel's ears decide whether Mattias reads that well.
- The rate limit still bites. The second lookup question, which sends two requests, failed with 429 even at 30 s apart.
- It got a date wrong. In the Mac test below, asked "Och på lördag?", it answered "På lördag den 29 oktober" (Saturday is 24 October); Luna answered the same follow-up with the right date.

## Streaming the microphone too (measured from the Mac)

Since 2026-10-21 `tools/voice_loop.py` streams the microphone to Home Assistant from the wake word, as the panel does, and Home Assistant's own voice detection decides when Joel is done (0.7 s of silence). Before, the Mac recorded until 0.8 s of silence and then sent the whole recording. Two questions from the test recording (`--mic-file`), brain gpt-oss-120b:

| Step | "Vad har jag i kalendern på fredag?" | "Och på lördag?" |
| --- | --- | --- |
| End of speech to Home Assistant's "done" | 1.3 s | 1.2 s |
| Speech to text after that | 0.03 s | 0.04 s |
| Agent and Mattias to the first audio | 1.63 s | 1.30 s |
| **End of speech to first audio** | **2.9 s** | **2.6 s** |

**Findings:**
- Speech to text is nearly free now: Home Assistant Cloud transcribes while Joel talks, so the text is ready 0.03–0.04 s after he stops, against 0.5–0.9 s when the whole recording was sent afterwards.
- Home Assistant needed 1.2–1.3 s to call the speech done, measured from the Mac's own guess at when the speech ended. 0.7 s of that is its silence setting. The rest is mostly the Mac's ssh and `docker exec` start-up, which delays the audio. The panel streams straight to Home Assistant, so it should be closer to 0.7 s.

## A short first sentence (measured on Jarvis)

On 2026-10-21 the prompt started asking for a short first sentence with the gist ("På fredag har du två saker."), with the details in the next one. Since speech is streamed sentence by sentence, the first sentence can then play while the rest is read. Home Assistant's speech cache was emptied before every question (`"fresh_tts"` in `ha/ha_tool.py`), so no sentence came from the cache. Same warm-up as above. Time to the first audio:

| Question | Luna, old prompt | Luna, short first sentence |
| --- | --- | --- |
| "imorgon" | 2.41 s, 3.99 s | 2.56 s, 2.85 s |
| "på fredag" | 4.43 s, 3.94 s | 2.23 s, 2.64 s |
| "resten av veckan" | 2.19 s, 1.95 s | 2.12 s, 1.87 s |
| "den första november" (needs a lookup) | 3.74 s, 3.77 s | 3.82 s, 3.28 s |
| **Mean** | **3.30 s** | **2.67 s** |

With the cache on, as in daily use, the short first sentences repeat ("I morgon har du en sak.") and come from the cache: 1.44–2.56 s, 2.07 s on average over five runs.

**Findings:**
- Luna follows the rule: every answer opened with a short sentence. gpt-oss-120b ignored it and kept writing one long sentence (2.0 s on average before, and up to 4.3 s after on "resten av veckan").
- The gain is uncertain. Luna's own time varied between 1.0 and 2.7 s for the same question, and the old-prompt runs were the slow ones. A short reply still waits for Luna's first word (about 1 s) and for Home Assistant Cloud to read the first sentence (about 0.6 s, mostly fixed cost).
- The headline sentences repeat, so in daily use they are often read from the cache. That is where the short opening helps most.
- With a tool log on, Luna looked up the calendar only for 1 November, which is outside the prefetched week. Once, before the prompt said "never say you lack the calendar", she answered "Den 1 november har jag ingen kalenderlista här" instead of looking it up.
- **A stalled warm-up.** One answer took 27 s: the warm-up's calendar read hung for about 22 s on iCloud, and the question waited for it. The warm-up now gives the calendar 4 s at most; after that the question looks it up itself.

## Prompt caching (measured on Jarvis)

On 2026-10-21 the prompt was split in two. The fixed part (who Jarvis is and the rules) goes first as a developer message with a prompt-cache breakpoint, with the tools before it. The changing part (time, the prefetched week, today's conversation, the question) comes after. OpenAI then keeps the fixed 1 390 tokens in its cache for 12 hours (`prompt_cache_options`, `ttl: 12h`). For gpt-6-luna a cache read costs 0.1× the input price and a 12-hour write 2.5×. Earlier the time stood near the top of the prompt, so nothing could be reused.

| Question | Before | With the cache |
| --- | --- | --- |
| Inside the prefetched week | 2 355 tokens in, $0.00051 | 2 390 in, of them 1 390 cached, $0.00025–0.00034 |
| Needs a calendar lookup (two requests) | 4 849 in, $0.00102 | 4 930–5 020 in, of them 2 780 cached, $0.00054–0.00058 |
| The first question after 12 hours without one | – | writes the cache, $0.00093 |

- Questions cost about half. Prices checked on OpenAI's pricing page on 2026-10-21, gpt-6-luna in Fast mode (context up to 272K tokens), per million tokens: input $0.20, cached input $0.02, 12-hour cache write $0.50, output $1.00. Standard mode is half of each. The costs in the table were logged with output at $0.75, so they are about $0.00001 low per question.
- With those prices, 30 questions a day cost about $0.32 a month (≈ 3 kr), and 100 a day about $1.05 (≈ 11 kr), including about one cache write a day. Without the cache it would be about $0.55 and $1.85.
- The cache is OpenAI's, so it survives a restart of the server.
- Time to the first word was 0.64–0.97 s on cached questions, about as before. The cache saves money here, not time.
- With the list marked "komplett för de dagarna" in the changing part too, Luna answered five questions about the coming week without a lookup.

## Parallel API briefing (2026-10-23, real Sonnet on Jarvis)

After parallelizing flight route/model lookups and calendar/model warm-up, one real `/converse`
streaming request asked for nearby aircraft and their routes/models, ISS, weather, Stadshagen metro,
home air quality, the next calendar event, SVT news and a Wikipedia fact about an aircraft model.
The same production brain answered: `anthropic claude-sonnet-5-5`. No calendar writes or notifications
were requested. This was a text request, excluding microphone recording, the silence threshold,
speech recognition and text-to-speech; it is not a first-audio measurement or a before/after comparison.

| Result | Measured |
| --- | --- |
| First streamed reply text | 8.90 s |
| Complete reply | 8.93 s |
| Model rounds | 3 |
| `hemmet` (cached home/weather/metro) | 4 ms |
| `flygplan` | 468 ms |
| `nyheter` | 506 ms |
| `iss` | 1,473 ms |
| `wikipedia` | 362 ms |
| Tool errors | 0 |
| Model cost | $0.027208 |

The reply covered the requested topics and explicitly said the closest aircraft's route was missing.
The calendar came from the warm-up snapshot. All five tool calls succeeded. The API timings overlap
within model rounds and must not be added together to infer total waiting time. There is no measured
speedup percentage from this single run. The targeted offline regression suite passed 42 tests,
including barrier-based checks that the independent calls actually start together.

## Anthropic brain: Opus, Sonnet and Haiku against gpt-6-luna (measured on the Mac)

On 2026-10-21 the brain `anthropic` (`server/agents/anthropic_agent.py`) ran the five cases of the integration test inside the server, at effort `low`, with the same mock tools and prompt as Luna (`tests/integration_test.py --agent anthropic --model <model>`, SDK `anthropic` 2.0.0). The Luna column is the in-process run from 12:18 the same day. Each cell is first word / total, then the cost.

| Request | claude-opus-5-5 | claude-sonnet-5-5 | claude-haiku-5-5 | gpt-6-luna fast |
| --- | --- | --- | --- | --- |
| Lägg till köp kvarg idag kl 17 i kalendern | 7.45 / 7.45 s, $0.0107 | 3.01 / 3.02 s, $0.0042 | 5.48 / 5.75 s, $0.00055 | 1.99 / 2.31 s, $0.00024 |
| Boka tandläkaren på fredag klockan tio | 5.54 / 5.55 s, $0.0081 | 0.67 / 2.33 s, $0.0034 | 3.26 / 3.47 s, $0.00022 | 1.97 / 2.14 s, $0.00024 |
| Vad har jag i kalendern i morgon? | 3.58 / 3.58 s, $0.0057 | 1.87 / 1.87 s, $0.0031 | 3.24 / 3.40 s, $0.00021 | 1.76 / 2.09 s, $0.00025 |
| Stäng av tv:n | 3.44 / 3.46 s, $0.0053 | 1.97 / 1.99 s, $0.0029 | 1.93 / 2.10 s, $0.00015 | 1.59 / 1.88 s, $0.00024 |
| Hur många dagar är det kvar till julafton? | 1.12 / 1.77 s, $0.0038 | 1.35 / 1.65 s, $0.0016 | 1.42 / 1.66 s, $0.00017 | 0.69 / 0.99 s, $0.00012 |
| Passed | 5 of 5 | 5 of 5 | 4 of 5 (see below) | 5 of 5 |

- **Prompt cache.** The tools and the fixed instructions are 1 213 tokens. Every request read them from the cache (2 426 tokens for a question with one tool step), and no question wrote any: the server's warm-up wrote them with a `max_tokens: 0` request at start, as it will on every wake word. The cache lives 5 minutes. A read costs 0.05× the input price on Opus 5.5 and 0.1× on Sonnet and Haiku, a write 1.25×. `tests/integration_test.py --agent anthropic` now fails a question that reads nothing from the cache.
- **Speed.** Sonnet is the nearest to Luna: its first word came after 0.67–3.0 s, Luna's after 0.69–2.0 s. Opus took 3.4–7.5 s on questions with a tool. Opus 5.5 always thinks; `low` is its lowest effort.
- **Cost per question.** Opus about 20–45 times Luna, Sonnet 12–17 times, Haiku about the same. At 30 questions a day that is about $6 a month for Opus, $2.70 for Sonnet and $0.25 for Haiku, plus the warm-ups. These test prompts have no prefetched week, so real questions cost a little more.
- **Haiku's failed check** was the test's wording, not the reply: it said "Det gick tyvärr inte att boka tandläkaren, eftersom kalendern inte svarade", which is honest. The check now also accepts "gick tyvärr inte" and "inte svarade".
- Opus and Haiku looked the day up before adding "köp kvarg", one extra round trip, as Claude Code did.
