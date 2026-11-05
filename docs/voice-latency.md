# Voice latency: where a turn's time goes

`tools/panel voice-latency` prints one line per "Hey Jarvis" turn with the time each step took,
read from the logs that already exist. Run it right after talking to the panel. It changes nothing,
calls no model, speech or voice service, and needs only ssh to Jarvis.

```sh
tools/panel voice-latency                       # the last 10 turns of the past 24 h
tools/panel voice-latency --since 3d --turns 0  # every turn of the last three days
tools/panel voice-latency --json                # the joined records with every stamp
tools/panel voice-latency --panel-log "$TMPDIR/hallpanel-logs/net-093000.log"
tools/panel voice-latency --save-raw /tmp/voice-raw.json   # keep the collected data; --raw FILE re-reads it offline
```

## What is joined

One stdlib-only collector runs on Jarvis over ssh (`python3 -I -`) and returns one JSON document.
Everything it reads is read-only:

| Source | Clock | What it gives |
| --- | --- | --- |
| Home Assistant's Assist pipeline debug (`assist_pipeline/pipeline_debug/list` and `get` over the local websocket) | Jarvis, µs | `stt-start`, `stt-end` with the text, `intent-start`, every streamed delta (`intent-progress`), `tts_start_streaming`, `intent-end`, `tts-start`, errors. Only the last 10 runs of the "Jarvis" pipeline, and only since Home Assistant last started. |
| `journalctl --user-unit hallpanel` | Jarvis, µs (journald's receive stamp) | `POST /transcribe` (text ready; a 503 is preceded by `transcribe unavailable: <reason>`, shown in the turn's note), `POST /converse` (the question arrived; with streaming the line is written before the agent runs), the reply line `brain: first word N ms, total N ms, tokens in/out (cached, cache write), rounds, $cost, tools`, each `POST /speak` (a sentence's audio is done), `/warm`, restarts. |
| `~/.local/share/hallpanel/history.db` (opened `mode=ro`) | Jarvis, whole seconds | The question and reply texts, model, first token, total, tokens, cache, cost, tools, rounds, `request_to_first_text_ms`. Since 2026-10-30 each tool also has `result`, the first 200 characters of what the model was told. It stays on Jarvis: the server strips it from the reply it streams to Home Assistant. Use it when a reply says something no code contains, which happened on 2026-10-28 and could not be traced. The only source that survives journal rotation and HA restarts. |
| `docker logs homeassistant` | Jarvis, ms (local time) | `jarvis_agent.warm … rejected` errors and, when the integration logs at INFO, `First TTS audio ready in N ms`. |
| A saved `esphome logs` capture (`--panel-log FILE`, optional) | The Mac's clock as each line arrived over Wi-Fi; the `latency` line is a duration on the panel's own clock | Wake word, `Speech recognised as`, `Intent progress` (the audio URL arrived), `State changed to ANNOUNCING`, `Announcement finished playing`, `STT text -> player announcing N ms`. |

The HA token is read from `~/.config/homeassistant/token` on Jarvis for the websocket login and is
never printed, copied or kept in a variable. Media URLs and tokens in the debug events are redacted
before they leave Jarvis.

Turns are matched by time: a server turn (the `/converse` line) picks up the history pair whose
user row is within 2.5 s and whose `total_ms` agrees, the HA run whose `stt-end` is within 3 s and
whose text is the same, and the panel run with the same recognised text. Requests without
streaming log their line after the reply; their arrival is taken as `total_ms` earlier. Local
commands Home Assistant answers itself, failed transcriptions and pipeline errors are rows too.
Whatever a source does not have stays `-`; nothing is estimated.

## Columns

All times are ms. The `time` column is Europe/Stockholm.

| Column | Meaning | Stamped by |
| --- | --- | --- |
| `time` | Text ready: HA's `stt-end`. Falls back to the server's `/transcribe` line, then the `/converse` arrival, then the history row (whole seconds). | Jarvis |
| `src` | Which sources the row has: `H` HA debug, `J` journal, `D` history, `P` panel log. | |
| `listen` | `stt-start` → `stt-end`: from the wake word (the panel starts streaming) to the text. It contains your own sentence, the configured pause (`number.jarvis_talpaus`, Joel selected 0.7 s on 2026-10-25), the upload and the transcription. | Jarvis |
| `>tok` | Text ready → the agent's first word reaches Home Assistant. `*` = the server's `request_to_first_text_ms` (from the `/converse` arrival) when HA has no run; `m` = the agent's `first_token_ms` (from the model request) when that is all the history has. | Jarvis |
| `>tts` | First word → Home Assistant starts the speech stream: at the first complete sentence, after 60 characters, or when text is followed by a real tool call. `w` = the reply was shorter than 60 characters and was spoken whole after it was complete. | Jarvis |
| `>audio` | Speech stream start → the first sentence's audio is done on the server (the first `/speak` 200). This includes the panel fetching the stream, splitting the first sentence, Home Assistant Cloud reading it and the trim. | Jarvis |
| `text>audio` | Text ready → first sentence's audio done. The wait you notice after the pause, minus the pause itself. | Jarvis |
| `total` | Text ready → reply complete (`intent-end`). `*` = measured to the server's reply line, `a` = the agent's own `total_ms`. | Jarvis |
| `panel` | From the panel log: recognised text → player `ANNOUNCING`. `--json` also has the panel's own `latency` line, URL → announcing, how long the reply played, and `first_audio_to_announcing_ms`, derived as the panel leg minus `text>audio` (two clocks, both starting at the recognised text, so Wi-Fi jitter of some 10 ms). | Mac / panel |
| `model`, `r`, `cost` | The brain, its model requests for this answer and the cost from its usage. `direct` is the server's own answer without a model: a read-only question (`server/quick_reply.py`) or, since 2026-10-27, an exact radio-off or volume command that runs its one tool (`server/quick_command.py`, tests in `tests/test_quick_command.py`); `HA local` a command Home Assistant handled itself. | |
| `cache` | Prompt cache: `hit`, `cold` (written, nothing read: the first question after more than 5 minutes), `hit+write` (a tool round added to the cache), `none`. | |
| `warm` | The wake word's `/warm`: `ok` reached the server before the question, `rejected` Home Assistant refused the panel's `jarvis_agent.warm` action ("Allow the device to perform Home Assistant actions" is off), `none` no warm seen. | |
| `tools / note` | Tools with their ms, or the error. `early -N` first: the lookups started while the model still wrote (`EARLY_TOOLS`) took N ms off the wait for their rounds' tools, computed from the history's `at_ms`/`early_ms` (since 2026-10-30) as each round's slowest tool started at the round's end minus the slowest as it ran. It assumes a round's tools run side by side; the calendar's tools share one MCP session and queue. | Jarvis |

Below the table a `median` row and a `sum` row (cost) follow when there are at least two turns.

## A slow turn with one quick tool

When `voice-latency` shows `>tok` of several seconds, one model round and a tool that took milliseconds, the time
is the model's (2026-10-28 11:54: "Kan du stänga av musiken?", Sonnet 9.3 s for a 50 ms Spotify pause; cache hit,
115 tokens out). The server's line in `journalctl --user-unit hallpanel` says it as `model N ms`. If the command
is exact and common, give it to `server/quick_command.py` so it skips the model (find such questions with
`tools/panel turns --model-tools [--days 14]`: model answers with at most one kind of tool, by tool) (radio off, volume, and since then
music off, next, previous, pause and resume), with its sentences in `tests/test_quick_command.py`. Since 2026-11-04 `server/quick_router.py`, a tiny network whose
weights Claude wrote by hand from the turns in history.db (no training), runs in shadow beside both: one
`quick-router:` journal line per question says the class it would have chosen, what the server did and whether
they agree, and it decides nothing. On the 246 distinct questions up to then it chose right for 99.2 % against the
regexes' 95.9 %, both tuned on those same questions, so judge it on new turns:
`python3 ~/hallpanel-live/server/show_router.py [--days N]` on Jarvis (counts, then each disagreement). Tests in `tests/test_quick_router.py`.

## Every turn and how it ended: `tools/panel turns`

`voice-latency` joins logs that fade: the Pi's 50 MB journal held about one day on 2026-10-27 and
Home Assistant keeps 10 runs in memory. Since 2026-10-27 each turn also gets a row in the
`voice_turns` table of `history.db`, kept like the chat history:

```sh
tools/panel turns              # the last 20 turns, then a count per day and outcome (7 days)
tools/panel turns 50 --days 30
```

The STT entity gives each turn a 16-hex id (`speech.TURN_ID`, carried like `TURN_LANGUAGE`). The
client sends it with `/transcribe` (header `X-Hallpanel-Turn`) and `/converse` (`"turn"`), and the
STT and TTS entities report how their stage ended with `POST /turn`, in the background:

| Stage | Who writes | Columns |
| --- | --- | --- |
| Recognition | HA STT entity | `stt` (`text`, `no_speech` = false wake or nothing said after a reply, `empty`, `failed`, `invalid`, `error`, `stopped` = the panel aborted it (a tap on the voice page or the mic button, Mikrofon off), `lost` = the panel was unavailable or HA stopping 2 s later, `cancelled` = no satellite to tell), `listen_ms`, `speech_ms`, `early`, `language` |
| Transcription | server `/transcribe` | `recognitions` (requests, the early one included), `recognition_ms`, `stt_error` (the same short reason as the journal) |
| Reply | server `/converse` | `reply_id` (the `messages` row with times, tokens, cost), `reply_ok` |
| Speech | HA TTS entity | `tts` (`complete`, `stopped` = the panel stopped reading the stream, `error`), `sentences`, `sentences_failed`, `first_audio_ms`, `audio_ms` |

No transcript is stored here; the question and reply are the `messages` rows. A turn Home Assistant
answers itself has text and speech but no `reply_id`. Announcements have no turn. `complete` means the
speech was handed to the panel's player, not that it was heard. Turns from before the change, and
Mac requests without a turn id, are not in the table.
A Wi-Fi drop ESPHome has not noticed within the 2 s reads as `stopped`; the panel health monitor reports Wi-Fi losses separately.

Do not count turns from journal lines: `/warm` arrives twice per turn (the panel's action and the STT
entity), and `/transcribe` can come twice (the early request plus the final one). Count rows here.

## What is not measured

- **The end of your speech is not stamped anywhere.** HA's VAD events are off because
  `stt.jarvis_cloud` owns the segmentation, and the server logs `/transcribe` when the text is
  ready, not when the recording arrived. The configured pause plus the transcription sit between your
  last word and `time`. Logging the `/transcribe` arrival or its duration in the server would
  close that gap; this tool only reads.
- **Nothing here is finger-to-ear.** The first `/speak` is audio on the server, not sound in the
  hallway. The panel leg needs a panel log, captured with the hallpanel skill's
  `panel.sh logs 60` while you talk (it does not restart the panel), then `--panel-log FILE`.
  With the production logger at INFO that capture holds only `[S]` state lines (2026-10-30): no wake word,
  `Speech recognised` or `latency` line, and `media_player >> PLAYING` comes before the first audio exists,
  so it does not time the sound either. Since 2026-10-30 the firmware logs that leg itself, into the log ring
  that `tools/panel logs` reads (it holds the INFO `latency` lines the API stream drops):
  `Reply first audio out: N ms after announcing, M ms after STT text`. Its moment is when the first buffer of
  the reply's real audio finished playing out of I2S (`bench/speaker_gaps.h`, `FirstReplyAudio`), not a
  microphone in the hallway. Read it right after talking: `tools/panel logs | grep latency`. With music on the
  panel's own speaker no new sound starts, so no line is written (given up after 15 s).
  The first three lines (2026-10-30) showed the panel's own wait: "Hur mår du?" played 1.37 s after its
  first sentence was ready on Jarvis. Two buffers sit on the way: Home Assistant converts every TTS stream for
  the panel with ffmpeg, and ffmpeg 8.1 writes nothing until more than 64 KiB reached its stdin (any WAV
  header; `tools/ha_ffmpeg_hold.py` measures it with HA's own command: rerun it after a Home Assistant update); the panel's audio reader then hands on only
  full 24 KiB reads and starts at 1 s. A first top-up to 48 KiB, sized for the panel alone, changed nothing
  (1.7 s on the next turn). When more speech follows, the TTS stream now tops up to 66 KiB with silence at
  once (`speech.PANEL_START_BYTES`, `WavStream.top_up`, tests/test_speech.py and the HA test
  `test_a_reply_that_goes_on_is_topped_up_...`): the first sentence plays as soon as it is ready, and the
  pause after a short first sentence grows to at most about 1.1 s (Joel chose this, 2026-10-30). A reply of
  one sentence ends the stream, which flushes both buffers, and gets no extra silence.
- **Heard audio cut or garbled?** Until `hallpanel.service` restarts, each cloud sentence sits in the
  server's speech cache. POST the same sentence (split as HA's `speech.sentences` splits the
  `--json` reply) to `/speak` on Jarvis and the journal says `source cache`: those are the exact
  bytes played, at no cloud cost. Intact bytes put the fault between HA and the panel's player.
- HA's debug store holds 10 runs per pipeline in memory. After a Home Assistant restart the HA
  columns are `-` until you talk again; the journal and history columns remain.
- `First TTS audio ready` only appears when `custom_components.jarvis_agent` logs at INFO, which
  it does not by default. The `/speak` lines in the journal give the same moment from the server's side.
- The time to the first heard audio is kept per turn in `tools/panel turns` (`1:a ljud`, from the TTS
  entity's report), so it survives HA restarts that empty the `text>audio` column here.
- The model answers `volym` in one round too: its result ("Panelen: 40 %.") is spoken as
  "Panelen står på 40 procent." (`direct_reply` in `server/agents/agent_core.py`).
- History rows are whole seconds. Rows older than 2026-10-23 have no `request_to_first_text_ms`,
  so their `>tok` carries `m`.

## Tests

`tests/test_voice_latency.py` covers the parsers and the correlation with fixture snippets of the
four sources (no network). It also checks that the collector compiles, prints only its JSON
document, opens the database read-only and never keeps the token in a variable.

## Completed voice preparation, 2026-10-25

The installed HA 2026.10.4 pipeline starts streaming at **more than** 60 characters,
or when a tool-call notification follows text. The Jarvis conversation adapter now
sends an external `jarvis_speech_ready` marker to the pipeline listener at a complete
first sentence of at most 60 characters, and its result after the last text delta. They are transport
notifications, not model tools, HA service calls or stored chat entries. Original
text, real tools, final plain replies and follow-up questions are preserved.

The real-HA regression joins the conversation adapter to the streamed voice with a
fake model blocked until its first sentence is synthesized. It proves progress below
the threshold, one synthesis per sentence and the unchanged complete chat reply.
It is free and silent. It does not establish a new acoustic latency number.

The cutoff reported after “Inlagt.” exposed HA's role filtering: sending the marker's
`tool_result` immediately made HA ignore every following content delta. The result
now waits until the conversation finishes. The regression replays a chunk ending
in `Inlagt. L` through the installed HA pipeline's actual callback and checks that
the speech stream equals the entire plain reply, including the rest of “Lördag”.

The voice changes also keep two sentence syntheses in flight, prime a short first
sentence to the speaker's existing one-second input threshold, cache finished cloud
phrases, and compute the existing AI tone in numpy. PCM comparison with ffmpeg allows
up to 8 integer levels at isolated limiter breakpoints; audible equivalence still
needs Joel's listening test. Piper output is not cached.

STT starts its warm-up when audio starts, reuses a Groq connection and makes at most
one speculative recognition per turn. Resumed speech invalidates that transcript;
the completed recording then gets one final recognition. Cancellation cleans up
pending work. This bounds application recognition calls to two even for a question
with many thinking pauses; transport replacement of a stale connection is counted
separately. Joel selected a 0.7-second pause and kept direct answers for simple
single-tool questions. The optional first-clause mode remains off.

`GET /speech/status` is passive and now includes provider request attempts, the last
HTTP status and numeric rate-limit headers from actual requests. These headers report
**daily** request limits, not RPM. `requests_per_minute` remains null because the exact
account RPM is shown in Groq's account Limits page, not in those response headers.
No live model/STT request was made merely to inspect that account limit. HTTP 429
honors `Retry-After` and suppresses further calls during the pause.

Sources: [Groq rate-limit units and headers](https://console.groq.com/docs/rate-limits).
Existing before-change raw data is preserved outside Git. After installation use
`tools/panel voice-latency` after Joel's own questions; do not label historical rows
as new measurements. An HA restart clears its in-memory pipeline debug runs.

## Further latency work, 2026-10-25

- Compound weather questions about today/tomorrow receive the same detailed SMHI
  source data in the first model request when it is already in the 15-minute cache.
  This path performs no network request. Unknown dates, absent/stale/invalid data
  use the ordinary tools. The model still handles the complete question; the source
  keeps its exact dates, area, time points, precipitation intervals and uncertainty.
- Before `add_event`, the fresh conflict query covers the event's actual dates
  across all calendars instead of every day from today. Overlapping occurrences
  beginning earlier remain relevant; the parser now also understands the calendar
  backend's explicit multi-day `start .. end` format. Adjacent events do not collide.
  Writes, alerts, duplicate prevention and iCloud's write confirmation remain in place.
  Calendar tool timing separates queue, collision-check and execution milliseconds.
- `/speak` now uses `voice.speech`, enabling the finished-audio cache that was
  previously bypassed. Swedish `None`/`sv` requests share the same voice cache key.
  The pause trimmer uses integer numpy window sums with unchanged PCM output.
- HA's authenticated `/api/jarvis_agent/tts` view asks the existing HA Cloud
  client for native RAW16 (same Mattias/Ryan voice, language and text), then adds a
  WAV header. This replaces TTS URL generation, WAV24-to-WAV16 ffmpeg conversion
  and a proxy download with one authenticated local request. Native audio keeps
  the existing 16 kHz/16-bit/mono contract; AI tone, pauses and Piper fallback remain.
  Invalid requests and Cloud errors are tested without provider calls. The endpoint
  uses HA's normal authentication and opens no new listening port.

Offline Pi measurements compared the old and candidate trimmers on identical fake
1/3/8-second signals: median 1.061/4.109/11.808 ms became 0.107/0.383/1.309 ms,
with byte-identical WAV output. Three interleaved read-only calendar query pairs
gave median 347.57 ms for the old 7-day range and 289.12 ms for the booking day;
this small sample does not establish tail latency or reproduce the earlier 5.4 s
outlier. Raw samples remain in private `/tmp/hallpanel-*-bench-20261025.json` files.
These component measurements are not a new end-to-end acoustic latency result.
`speech: source ..., tts ... ms, trim ... ms, tone ... ms, total ... ms` in the
hallpanel journal exposes the remaining TTS wait during Joel's own questions.

The removed HA-container ffmpeg conversion took median 45.058/45.846/48.029 ms
for synthetic 1/3/8-second WAVs (five samples each). Wrapping native PCM took
0.060/0.096/0.148 ms. These isolate local conversion, not Cloud synthesis time.
Release checks passed 495 local tests plus 41 tests against real HA classes.
All seven changed deployed runtime files matched their local SHA256 hashes;
the live authenticated native endpoint rejected an empty message with HTTP 400
without invoking the provider. No paid model evaluation or real calendar write
was made for this verification. Joel's next spoken questions establish acoustic
behavior and whether the prepared weather data avoids a model round in practice.

The unchanged `tabs-20-orb-10s-v2` responsiveness workload passed its existing
baseline both before and after deployment, with restoration confirmed. These are
UI control measurements, separate from speech latency. Firmware was not changed.
Before/after variation does not establish a rendering speedup from host changes.

| Metric (ms) | Before p50 / p95 / p99 / max | After p50 / p95 / p99 / max |
| --- | --- | --- |
| Remote release to redraw (20 taps) | 65.393 / 70.498 / 71.410 / 71.410 | 60.782 / 63.577 / 63.627 / 63.627 |
| Orb refresh completion interval | 40.946 / 42.058 / 43.936 / 44.694 | 40.888 / 42.011 / 44.619 / 47.604 |
| Presentation interval | 33.128 / 49.688 / 49.691 / 49.698 | 33.127 / 49.688 / 49.690 / 49.693 |
| Host status request | 31.894 / 87.338 / 92.328 / 92.328 | 22.068 / 73.533 / 92.876 / 92.876 |

Refresh jank over 50 ms was 0% in both runs. Raw reports, including observer
costs, retained slow samples and host transport timings, are preserved at
`/tmp/hallpanel-voice-opt-before-20261025/responsiveness.json` and
`/tmp/hallpanel-voice-opt-after-20261025/responsiveness.json`.

## Experiment A: existing voice data and avoidable waits

The [October 25 experiment report](baseline/voice-experiment-a-2026-10-25.md) separates
historical voice evidence from a free, isolated before/after measurement. Clock and
fresh calendar replies now bypass unrelated warm-up waits; exact tomorrow/weekday
calendar questions can also avoid a model request. The acoustic one-second target
remains unmeasured.
