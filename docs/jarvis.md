# Jarvis

The Jarvis side: talking to it from the Mac, switching brains, what it says unasked, and its server code and tests. Moved from the README on 2026-10-30.

Jarvis itself runs on the Pi (Jarvis, `user@jarvis.local`). The Mac only talks to it and switches it. Since 2026-10-21 the brain is `anthropic` with `claude-sonnet-5-5`; `tools/brain.sh` shows what is on now.

**Talk to him** (from the repo folder, needs `uv` and `ffmpeg`):

```sh
uv run tools/voice_loop.py                 # say "Hey Jarvis", ask; stops after 3 questions or 5 minutes
uv run tools/voice_loop.py --turns 10      # 10 questions
uv run tools/voice_loop.py --enter         # press Enter instead of saying "Hey Jarvis"
uv run tools/voice_loop.py --mute          # only print the replies
uv run tools/voice_loop.py --file q.wav    # send a recorded question
uv run tools/voice_loop.py --endpoint 0.5  # the Mac ends your turn after 0.5 s of quiet (faster; 0.3 cut off)
uv run tools/voice_loop.py --stt groq --turns 10  # automatically understand and answer in Swedish or English
```

When a reply ends in a question, he listens again without the wake word ("Jarvis väntar på ditt svar"). Each time he starts listening you hear the chime "Ekolod" (`assets/sounds/listen.wav`), as on the panel. The panel's Jarvis pipeline waits for 0.8 s of silence by default and allows 60 s recordings through `stt.jarvis_cloud`, which segments audio before forwarding it to Home Assistant Cloud. **Jarvis talpaus** (`number.jarvis_talpaus`) adjusts the silence to 0.3–2 s and survives restarts. It uses the same microVAD detector as HA, with its own per-question state and a 5 s timeout if nobody starts speaking. HA's core code and other assistants are unchanged. The panel inherits these limits when it is adopted and assigned the Jarvis pipeline. `--endpoint 0.5` can still end Mac turns sooner. The path is Home Assistant's pipeline "Jarvis", the agent on Jarvis, Mattias with the AI tone.

**Swedish/English experiment (`--stt groq`).** The Mac records until 1.5 s of silence (adjust with
`--endpoint`), allowing up to 60 s per question (`--max-speech`). For a demo with longer thinking pauses,
use `--stt groq --endpoint 2 --max-speech 90`. Increasing the pause also increases the wait after the last
word. Then it sends the recording over ssh. `ha/ha_tool.py ask-groq` runs in the Home Assistant
container, transcribes it with Groq `whisper-large-v3-turbo` without a fixed language, and passes the
detected Swedish/English language to the existing Jarvis agent. It streams each sentence through the
same trimmed Cloud voice and AI tone: Mattias for Swedish, British Ryan for English. A question at the
end of the answer keeps the microphone listening without another wake word. `--enter`, `--file` and
`--mute` also work. The terminal shows the detected language and timings.

The key stays on Jarvis in `~/.config/hallpanel/groq.env` (`GROQ_API_KEY=...`); no new package, account or
key is needed when that file already exists. The request uses that key's existing Groq plan; it does not
enable billing or change plans. The experiment skips HA's local intent handler and uses Jarvis's tools
instead. It does not select a different brain or update the panel's Swedish Assist pipeline. English
speech requires Cloud; the offline Piper voice is Swedish only. Offline checks:
`uv run --with aiohttp python -m unittest tests/test_groq_voice.py tests/test_voice.py tests/test_server.py`.

**Switch the brain** (the server restarts in about 2 s):

```sh
tools/brain.sh                                # which brain is on
tools/brain.sh anthropic claude-sonnet-5-5    # Claude Sonnet 5.5 (now)
tools/brain.sh anthropic                      # Claude Opus 5.5
tools/brain.sh luna                           # OpenAI gpt-6-luna
tools/brain.sh groq                           # gpt-oss-20b on Groq
```

The keys live on the Mac in `~/.config/hallpanel/openai.env`, `groq.env` and `anthropic.env`. The first switch to a brain copies its key to Jarvis.

**Install a change on Jarvis**: `tools/deploy_jarvis.sh` (add `--restart-ha` when the Home Assistant integration changed). Afterwards `sh tools/panel_fields.sh [KEY ...]` prints what `GET /panel` gives the panel now (read on Jarvis, the token never printed), so a new field can be checked before HA's 15 s read or a flash.

**Look on Jarvis**:

```sh
ssh user@jarvis.local python3 hallpanel-live/server/show_history.py 20       # the latest messages, with model, times and tokens
ssh user@jarvis.local python3 hallpanel-live/server/show_history.py --tool resa   # one tool's calls: args, result, failures
tools/panel turns                                                         # every voice turn and how it ended, per day too
ssh user@jarvis.local python3 hallpanel-live/server/show_memory.py           # what Jarvis remembers: the profile and the notes
ssh user@jarvis.local python3 hallpanel-live/server/agents/home_status.py    # what Jarvis sees of the home now
python3 server/agents/lookup.py wikipedia "Anna Book"                          # a lookup, also from the Mac
python3 server/agents/lookup.py news sport
python3 server/agents/lookup.py search AIK senaste match
ssh user@jarvis.local journalctl --user-unit hallpanel -n 50                 # the server log
```

**NASA's picture of the day** is baked at 07:30 (`hallpanel-apod.timer`) and the panel fetches it at 07:45. Run it by hand with `ssh user@jarvis.local systemctl --user start hallpanel-apod`; `apod.json` next to `bg.png` says which picture it is.

**Radio on the panel** needs the panel's media player in Home Assistant: once it is adopted, add `JARVIS_RADIO_PLAYER=media_player.<its id>` to `server.env` and restart the server.

**Jarvis speaks unasked** (`server/watch.py`, since 2026-10-22). What he says, and where:

| What | When | Where |
| --- | --- | --- |
| Reminders Joel asked for ("påminn mig klockan sex att ...", "säg till om tjugo minuter") | When due | The panel when he is home, otherwise the phone; any hour |
| Smoke, a leak | At once, again every 2 minutes | The panel, any hour (Home Assistant already pushes them to the phone) |
| The front door open 10 minutes | Once per opening | The panel, by day, when he is home |
| PM2.5 over 35 for 5 minutes | Once, again only after it has been under 25 | The panel, by day, when he is home |
| The laundry slot | 30 minutes before; a slot before 06:30 at 21:30 the evening before | The panel, by day, when he is home |
| A calendar event with a time | 15 minutes before | The panel, by day, when he is home |
| The internet lost, and back | After 2 failed checks a minute apart | The panel, by day, when he is home |

Night is the panel's night mode: its **Natt börjar** and **Natt slutar** in Home Assistant (`time.hallpanel_natt_borjar` and `time.hallpanel_natt_slutar`; `JARVIS_NIGHT_START` and `JARVIS_NIGHT_END` if the ids turn out different), 22:00 to 06:00 until the panel is adopted; `JARVIS_QUIET_HOURS=22-6` overrides them; "home" is `person.joel`. Until the panel is adopted, add nothing: reminders go to the phone and the rest is only logged (`journalctl --user-unit hallpanel | grep notify`). Once it is adopted, add `JARVIS_PANEL_SATELLITE=assist_satellite.<its id>` to `server.env` and restart the server. `JARVIS_NOTICE_CALENDAR=0` turns the calendar notices off. Every announcement is also kept in the chat history as Jarvis's, so he can ask what Jarvis said.

**Timers** are Home Assistant's own: "sätt en timer på tio minuter" is understood locally in about a second and the panel counts down and rings. A timer phrased so that it reaches Jarvis becomes a reminder in that many minutes.

**To the phone**: Jarvis offers to send long answers (a recipe, a list, a link) to the phone, and sends what Joel asks for with the tool `till_telefonen` (`JARVIS_PHONE=mobile_app_demo_phone`, set by the deploy). Like the memory's tools, it runs only when Joel just asked for it or said yes to the offer.

**Silent topics**: calendar events Jarvis never mentions, announces, briefs or sends to the phone, while they still count as busy. They are set in `server.env` on Jarvis, not in the code: `JARVIS_SILENT_TOPICS` (a regex matched as whole words, case-insensitive) and `JARVIS_SILENT_TOPICS_NAME` (how the prompt names them). Unset hides nothing. Quote both values in single quotes (`JARVIS_SILENT_TOPICS='a\w*|b'`): `deploy_jarvis.sh` also reads server.env as sh, where `|` and `(` break it, and it now stops before copying when the file does not parse. `server/silent_topics.py`, tests/test_silent_topics.py.

**Without the internet**, speech to text and the agent cannot work, but the panel still speaks: `server/voice.py` reads with Piper on Jarvis (`sv_SE-nst-medium`, 0.2–0.7 s a sentence after a 3.5 s first load) when Home Assistant Cloud does not answer within 8 s, and tries the cloud again after a minute. So reminders, alarms and "Nätet är nere" are still said, and the panel shows that the net is down.

**Rensa** on the panel calls `jarvis_agent.clear`: Jarvis forgets the conversation, and the chat starts over. Nothing is deleted; `/history?all=1` and `show_history.py` still show everything. The same from the Mac, for example before a demo:

```sh
ssh user@jarvis.local '. ~/.config/hallpanel/server.env && curl -s -X POST -H "X-Hallpanel-Token: $HALLPANEL_TOKEN" http://127.0.0.1:8765/clear'
```

**What Home Assistant exposes of the panel** (entity ids such as `light.hallpanel_skarmljus`, the ESPHome actions such as `esphome.hallpanel_show_page`): read it from HA on Jarvis rather than guessing, `. ~/.config/hallpanel/server.env; curl -s -H "Authorization: Bearer $(cat $JARVIS_HOME_TOKEN_FILE)" http://127.0.0.1:8123/api/states` (and `/api/services`), filtered on `hallpanel`. A new firmware action shows up there only after the flash.

**Settings on Jarvis** are in `~/.config/hallpanel/server.env`, read when the service starts (`systemctl --user restart hallpanel`). For example, `JARVIS_VOICE_EFFECT=none` turns the AI tone off, and without `JARVIS_HOME_TOKEN_FILE` he knows nothing of the home.

**Tests**:

```sh
uv pip install --python .venv/bin/python -r tests/requirements.txt
.venv/bin/python tools/check.py                            # free local suite + ESPHome config
.venv/bin/python tools/check.py --ha user@jarvis.local     # also test candidate files in HA's runtime
.venv/bin/python tests/integration_test.py --list           # free model-eval preview
```

`tools/deploy_jarvis.sh` runs both local and HA checks before copying application files or restarting.
`sh tools/flash_panel.sh` runs the local gate before building/uploading firmware. Missing dependencies,
skipped tests and failures stop installation. Use that flash entrypoint; direct ESPHome uploads and the
external skill's older flash command bypass the gate.

`tools/panel visual check` checks the real screen against a private visual reference and writes
an HTML diff report. The checked flash script runs it after upload when the reference exists.
See [visual regression tests](visual-regression.md) for setup, coverage and reference updates.

Model evals require explicit `--live --max-turns N`; they never run as part of the free check. The
expanded suite has 14 scenarios / 15 turns, with isolated histories, behavioural checks, optional
latency limits and separate JSON reports. See [release checks and evals](evals.md) for setup,
budget semantics, commands and measurement limits.

## Server code

The Jarvis-side server on 127.0.0.1: the agent, Mattias's voice and the chat history, kept forever. Home Assistant's Jarvis integration is its only client (`hallpanel_server.py`, `voice.py`, `stage.py`, `hallpanel.service`). `show_history.py` shows the latest messages with model, times, tokens and tools (`--full` for whole texts, not cut at 70 characters); each message's `tools` column keeps every call's start of `args` and `result` (history.db only, `public_timing` strips both). `show_turns.py` (`tools/panel turns`) shows every "Hey Jarvis" turn and how it ended, failures and silent wakes included (docs/voice-latency.md). `memory.py` is what Jarvis remembers about Joel, apart from `~/agent-memory`: a fixed profile in `~/.config/hallpanel/profile.json` on Jarvis and the notes he asks for by voice ("kom ihåg att ..."), both in every prompt; `show_memory.py` shows them. The agents are in `agents/`: `agent_core.py` holds everything the brains share (tools, the question loop with tools run in parallel, the warm-up), and each brain only talks to its API: `openai_agent.py` (the luna and groq brains, standard library only) and `anthropic_agent.py` (the anthropic brain, which needs the `anthropic` package from `requirements.txt`). Since 2026-11-03 the anthropic brain's wake-word warm-up only opens the connection and the question's first request writes the prompt cache: the cache-writing warm-up paid a full write (24 763 tokens, $0.062 on Sonnet 5.5) on nearly every wake and cost 2-11 times the answers; `JARVIS_ANTHROPIC_WARM_CACHE=1` in `server.env` brings it back. The price is time: the first cold question after the change waited 4.5 s for its first word against 2.5 s for a warm one (one sample, 2026-11-03 11:01, not the 0.5 s guessed); compare more with `tools/panel turns` before calling it settled. The journal logs each warm-up as `anthropic warm-up: {...}` with its cost (`journalctl --user-unit hallpanel -g 'anthropic warm-up'`). `show_spend.py` sums the cost per day from both, answers in history.db and warm-ups in the journal: `~/hallpanel-live/venv/bin/python ~/hallpanel-live/server/show_spend.py --days 14`. `env_files.py` reads the NAME=value files in `~/.config/hallpanel` (`server.env` and the key files) for all of them. `lookup.py` gives both free lookups, read only: Wikipedia (Swedish, else English), news headlines (SVT, SVT Stockholm, SVT Sport, DN, Aftonbladet, Expressen, BBC), the planes over home (adsb.lol, adsbdb), where the ISS is (wheretheiss.at) and the police's events (polisen.se), and web search through Jina when `~/.config/hallpanel/jina.env` holds a key. `radio.py` plays Sveriges Radio on the panel through Home Assistant. `panel.py` gives the tool `panelen` (since 2026-11-04): a page by voice ("gå till hemskärmen", Hemmet, Spelas nu, Tavla, Natt, Väder, Kalender, Påminnelser, AI) through `esphome.hallpanel_show_page`, the firmware action in `board_config/voice.py` that shows it at once, or, while Jarvis is still answering, when the voice page goes back (`voice_to`, also at night); the background, in this process, as Inställningar does; and the day's screen light in percent (`light.hallpanel_skarmljus`). The audio output stays with `hogtalare`. Tests: tests/test_panel_tool.py, tests/test_show_page.py. `reminders.py` keeps the reminders Joel asks for by voice (the tool `paminnelse`), `watch.py` is a thread that looks every 20 s and has Jarvis speak unasked (reminders, smoke, leaks, the door, the air, the laundry slot, the next calendar event, a lost internet), and `notify.py` sends each one to the panel (Home Assistant's `assist_satellite.announce`) or the phone, and gives the agent `till_telefonen`. `voice.py` falls back to Piper on Jarvis when Home Assistant Cloud does not answer. Every call to Home Assistant, from the home status, the radio, the notices and the voice, goes through `agents/hass.py`: one token, and the first refusal stops them all, since five refused logins would get 127.0.0.1 banned (and with it everything else on Jarvis that talks to Home Assistant locally); a new token in the file starts them again. With `JARVIS_TV_MCP` (on since 2026-10-27) the agent also gets a safe subset of the tv skill's connector (play, a channel's newest YouTube video, pause, volume, power, status). `apod.py` (run daily by `hallpanel-apod.timer`) makes NASA's picture of the day the panel's background, baked with `styles/glass-v2/bake.py`; `daily_pictures.py` (`hallpanel-daily-pictures.timer`) keeps the other daily background choices and Växlande (docs/glass-v2.md). `home_status.py` reads the home for both, read only: Home Assistant's door, temperature, air, alarms, sun and laundry slot, SMHI's forecast and SL's metro from Stadshagen. Free check of the vader tool for a day and window on Jarvis: `cd ~/hallpanel-live/server/agents && ~/hallpanel-live/venv/bin/python home_status.py vader 2026-11-07 17:00 19:00`. `bookers.py` gives the tool `automatiska_bokningar`, read only: when the laundry and Gympass booking jobs (cron on Jarvis, ~/tvatt-booker and ~/sats-booker) run next, what they book then, and their last run and canary from `~/.cache/jarvis-alerts/booking-<job>.json`; its schedule mirrors their crontab, so change both together. Free read on Jarvis: `cd ~/hallpanel-live/server/agents && ~/hallpanel-live/venv/bin/python bookers.py`. `mornings.py` gives the tools `morgon_tv` and `vackningar` (read, early) and `morgon_tv_andra` and `vackning_andra` (change, after the whole answer): Morgon-TV's weekday rows (on/off, time, video as a YouTube link or search words, the shared volume) and the wakeups' weekday rows and the one extra wakeup with Joel's own instruction (Egen väckning), all through the same HA helpers the dashboards edit, so the dashboards show what Jarvis set. Each change is read back from HA and logged in the server's journal as `mornings:` (`journalctl --user-unit hallpanel -g mornings:`); the wakeup controller also logs it in vackning.log with the token's HA user. Free read on Jarvis: `cd ~/hallpanel-live/server/agents && ~/hallpanel-live/venv/bin/python mornings.py`. When Jarvis must never say something a tool returns, take it out of the tool's result in code: a rule in the tool's description alone did not stop Sonnet from reading the laundry booker's "en färg eller ingen" warning aloud (2026-11-01; `laundry.book` now drops `obs`, tests/test_laundry.py). Likewise a slow tool (`SLOW_NOTICES` in `agent_core.py`: the laundry's, 15-20 s) gets a fixed spoken sentence when the model writes none before it, since the prompt's sentence-first rule was not followed for one-lookup questions. Since 2026-11-04 every tool round does the same with a short sentence per kind of tool (`TOOL_NOTICES`: "Jag kollar resan.", "Jag kollar kalendern.", "Jag lägger in det.", otherwise "Jag tittar på det."), once a question, never a claim that something is done, and not for radio, volume and Spotify, whose result is said at once. The journal logs `agent: said for tools: <sentence>`. A question that names the laundry ("tvätt") starts the site's read of the next 14 days at once, beside the model (`laundry.prefetch`, called from `ToolAgent.ask`; 2026-11-02): the model's `tvatt` call came 1.6-4.4 s into the question and the read then took another 2.5-3 s. A read within those days is cut from it, a pass's colours always ask the site, and a booking waits for a running prefetch and then drops it. Live on Jarvis the tool waited 0.6 s instead of 2.4 s with its call written 1.8 s in. The journal logs `laundry: prefetch started/read/used/not used` (`journalctl --user-unit hallpanel -g 'laundry: prefetch'`); tests/test_laundry.py. A round that only booked (`tvatt_boka`, all of it went through) is answered by `laundry.done_reply` ([fictional booking confirmation]) instead of one more model round, 2-3 s of a ~20 s booking question (2026-11-01); a failure, a partial booking or a question that also asks for a step after it (calendar, push, reminder: `laundry.MORE_ASKED`) still goes to the model. The journal logs `agent: direct reply for tvatt_boka`. Since 2026-11-03 `tvatt_jarvis.py` itself reuses the last run's login for 15 minutes (`~/.cache/tvatt-booker/jarvis-session.json`, 0600, under an flock; stderr says `reused login` or `logged in`): a 14-day read went from 2.0 s to 0.9-1.0 s on Jarvis. A booking also skips "Fortsätt" and the closing reload of "Min sida"; that path is tested against the fake site only until the next real booking (look for `menu from the booking page failed` in the journal).

## Tests

`test_server.py`: server regression tests with a stub agent and a fake Home Assistant (`python3 -m unittest tests/test_server.py`). `test_speech.py`: how a streamed reply is split into sentences. `test_screen.py`: the cards Jarvis shows on the panel, cut to what fits, and the question ending on them. `test_fallback.py`: the other brain answers when the API fails before anything was done, never after a booking. `test_ha_conversation.py`: the agent's deltas in Home Assistant's real chat log, the sentence before the tools and the answer after them, and the chat sensor's `last_card` and `working` (runs inside the Home Assistant container). `test_agenda.py`: the prefetched calendar reaches the agent only while fresh and unchanged. `test_anthropic_agent.py`: the anthropic brain's tool loop, prompt cache and refusal handling with a fake client (needs the `anthropic` package). `test_home_status.py`: the home status lines and the `hemmet` tool, without the network. `test_lookup.py`: the lookups and the TV allow-list with fakes. `test_agent_core.py`: the shared question loop with a fake brain (parallel tools, rounds), and the calendar's tools with a fake calendar-mcp (the default event allow-list includes reading, adding, editing and deleting; narrower lists still block writes). `test_env_files.py`: how the NAME=value files in `~/.config/hallpanel` are read. `jarvis_env.py`: the calendar the scripts that run on Jarvis take from the server's own `server.env`. `test_memory.py`: the profile's age, notes saved and forgotten only when Joel asks, end dates and the limit. `test_radio.py`: Sveriges Radio with a fake Home Assistant. `test_reminders.py`: reminders only when Joel asks, at a time or in a while, across a restart. `test_watch.py`: what Jarvis says unasked and where it goes (panel, phone, nowhere), with a fake home, calendar and Home Assistant. `test_voice.py`: Piper when the cloud's voice does not answer. `test_hass.py`: one refused token stops every call to Home Assistant. `test_apod.py`: NASA's picture as the background, a video day and a logo-sized picture (needs numpy and Pillow). `test_daily_pictures.py`: the daily background sources, duplicates and Växlande with fake APIs. `integration_test.py`: the whole chain with mocked tools, against Claude Code, gpt-6-luna (`--agent openai`) or the anthropic brain (`--agent anthropic [--model ...]`). `live_jarvis_test.py`: runs on Jarvis against the real calendar-mcp, read only, or with `--write` one test event that is undone at once.
