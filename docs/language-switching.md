# Chat readability and per-turn speech language

The dashboard's user bubble keeps its blue background. Jarvis's reply now has a
92% opaque charcoal background and both have an 18% white outline. This improves
contrast on arbitrary pictures without computing backdrop blur on the ESP32.
The bubble layout, text limits and real history remain unchanged.

`tools/panel test-mode chat --duration-ms 10000` shows two bounded synthetic chat
bubbles. It never speaks, calls a model, changes history or writes HA state. Its
expiry, lease cleanup and explicit `restore` return the original page and texts.
If a real message arrives, cleanup preserves that new text and its visibility.
The regular `tools/panel smoke --test-mode --out NEW_DIR` also captures this case.

The chat viewport owns pointer input; its nested layout objects and bubbles are
not clickable. It scrolls full current question/reply text without moving the
card, clear button or microphone. New text follows only when already at the end.
`tools/panel test-mode chat_scroll --duration-ms 30000` provides an overflowing
local fixture. The regular physical suite drags over the bubble both ways and
requires real label-coordinate movement with the dashboard still active.
`tests/test_panel_chat.py` also runs the generated subtree with native LVGL pointer
input and checks that updates preserve a reader's position. Neither test uses
voice, models, real conversation history or changes HA states.

Since 2026-10-30 the chat holds the whole conversation, not only the newest pair: a second question used to
replace the first, so there was nothing to scroll back to. `sensor.jarvis_chat`'s `earlier` attribute
(`ha/custom_components/jarvis_agent/chat_view.py`) carries up to six messages before the newest question, from the
same 10-minute window and only since the last Rensa, and `panel_chat::earlier` (`styles/glass-v2/chat_scroll.h`)
fills six bubbles above the newest two, blue on the right for Joel and dark on the left for Jarvis. A real finger's
drags can be checked afterwards with `tools/panel touches --summary --last-seconds 600` (each gesture with
its start, end and duration).

## Swedish and English in the same conversation

Previously the panel passed every recording to Swedish-only Cloud STT. Its HA
client also omitted language from `/converse` and `/speak`. The Mac-only Groq
path already supported automatic recognition and an English voice.

Both paths now use authenticated local `POST /transcribe` on Jarvis. This calls
the existing Groq account with Whisper Large V3 Turbo, a multilingual
[transcription model](https://console.groq.com/docs/speech-to-text). No fixed
language or translation request is supplied. Its response must identify Swedish
or English. Since 2026-10-28 a transcript in another language is asked for once more with the
language set to Swedish (four questions on 2026-10-27 came back as Polish and failed); if that fails
too, the turn fails with the language first heard. Other failures are not retried.
Since 2026-10-30 every request also sends `VOCABULARY` in `server/transcription.py` as Groq's `prompt`: names Whisper heard as ordinary words ("Spela Anna Book" came back as "annan bok" twice, and Kent was played once). It is names only, so if the prompt is read back over silence it is not a command. A reading that is four or more of its words in order counts as no speech (log: `transcription: the reading was the vocabulary prompt`). When `tools/panel turns` shows a name misheard again, add it to that list.
A transcript heard as English is read once more as Swedish (`_english_or_swedish`): the surer reading wins, but
when both readings are the same words the words decide, since forced Swedish reads English speech just as surely
("What's up, my man?"). Since 2026-11-01 such a reading is Swedish when it holds a Swedish-only word or å/ä/ö
(`SWEDISH_WORDS`): "Spela Coldplay" was the same both ways, Swedish surer (-0.251 vs -0.33), and was answered in
English. Log: `transcription: heard as English (sure …), as Swedish (sure …), chose …`. Add a word to
`SWEDISH_WORDS` when `show_turns.py` shows a Swedish command marked `(en)`.
The Groq key is read only on Jarvis from `~/.config/hallpanel/groq.env` or
`GROQ_API_KEY`; it is never sent to HA, the Mac, the ESP32 or logs.

The existing `stt.jarvis_cloud` entity ID and Assist pipeline are retained.
microVAD keeps the selected silence threshold, five-second false-wake limit and
60-second recording maximum. False wakes without detected speech make no
external recognition call. Groq requires the complete bounded WAV, so recognition
starts after recording, rather than streaming the recording to Cloud STT.
Audio stays in memory. There are at most two recognition requests in flight.
Authentication refusals stop until credentials change; rate limits pause requests
for a minute. `GET /speech/status` is authenticated, passive and reports readiness
without making a paid call or exposing credentials.

HA's default pipeline awaits STT in its run task, then creates intent/TTS tasks
from that context. A `ContextVar` carries the detected language through those
tasks. Each STT invocation clears and replaces it. The conversation client sends
it explicitly in `/converse`; TTS captures it once and sends it with every
sentence to `/speak`. No mutable entity-wide "current language" is used.
Thus Swedish → English → Swedish keeps the same conversation ID and history.
Independent concurrent runs cannot change one another's voice.

Swedish uses Mattias, English uses the existing British Ryan voice. English never
falls back to the Swedish Piper voice. Streaming replies, sentence order,+cancellation, pause settings and HA local intents are preserved. Local intents
still use the pipeline's configured Swedish matching; unmatched English requests
reach Jarvis. The legacy `/voice` diagnostic remains a separate Swedish path.

## Free verification

```sh
.venv/bin/python -m unittest tests.test_transcription tests.test_groq_voice tests.test_panel_test_mode
.venv/bin/python tools/check.py --ha user@jarvis.local
tools/panel ci --url http://hallpanel.local:8080 --device hallpanel.local --visual-baseline REVIEWED_GOLDEN_DIRECTORY
```

HA candidate tests use real HA classes and its background-task scheduler, fake
transcription/replies/WAVs, three turns on the same conversation ID and concurrent
Swedish/English runs. Provider protocol tests use fake HTTP. These establish
routing, isolation, framing and failure behavior, not microphone recognition
accuracy, the live model's answer language or acoustic voice quality. No paid
STT, model or TTS call is made by these checks.
