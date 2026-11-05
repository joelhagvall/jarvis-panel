# Release checks and evals

The free release check is automatic in `tools/deploy_jarvis.sh` and `tools/flash_panel.sh`.
A failed test, unexpected skip, missing dependency or invalid ESPHome configuration stops the
script before it copies application files, compiles/uploads firmware or restarts a service.
Direct `esphome upload` and the external skill's older `panel.sh flash` bypass this gate; use
`tools/flash_panel.sh` for checked firmware installs. This is a local release gate, not a GitHub
Actions workflow or a scheduled job.

After a successful firmware upload, `flash_panel.sh` also runs `tools/panel visual check`
when the private `.panel-visual/reference/reference.json` exists. A visual failure exits
nonzero and keeps the reference intact; the candidate firmware is already installed at that
point. With no reference, the script explicitly reports `Visual regression NOT CHECKED`.
See [visual regression](visual-regression.md) for coverage and report commands. This live
hardware check is separate from `tools/check.py` and never runs paid voice/model calls.

## Free checks

From the repository root, using the existing panel virtual environment:

```sh
uv pip install --python .venv/bin/python -r tests/requirements.txt
.venv/bin/python tools/check.py
.venv/bin/python tools/check.py --ha user@jarvis.local
```

The machine also needs a C++ compiler and ffmpeg. ESPHome is already in the panel's `.venv`.
`--tests-only` runs tests without validating the ESPHome configuration.

The local suite runs every `tests/test_*.py` module except the two HA-runtime modules. Missing
packages and skipped tests are failures. Python test processes and their Python children block
external DNS/connections; loopback servers remain available for HTTP integration tests. This is
not an OS sandbox for arbitrary native programs. Exported production agent settings and API keys
are removed from the test environment. The write guard/profile use temporary paths.

Each local module runs in its own process, as many at a time as the Mac has cores (`--jobs N` on
`tools/offline_tests.py`; `--jobs 1` is the old single process). Slowest modules start first, by the
previous run's times in ignored `.panel-ci/test-durations.json`. Each module gets its own write-guard and
profile files. On 2026-10-26 this took the 648 tests from 85 s to 12–15 s on 12 cores; the longest single
module (about 12 s under load) is now the floor. A failing, erroring, skipped or crashing module still fails
the run (`tests/test_offline_runner.py`), and its full output is kept in ignored `.panel-ci/test-failures/`:
`test_apod` failed once in a deploy on 2026-10-27 and its trace was lost to a filtered log (0 failures in 25
solo and 6 parallel reruns since). The HA-container run stays sequential.

Two per-machine caches keep deploys short without changing what is checked or shipped. `tests/test_bake.py`
keeps the frozen reference renderer's pixels in `~/.cache/hallpanel/reference-bake/`, keyed by
`reference_bake.py`, `gen.py`, the input image, NumPy, Pillow, Python and the CPU; the candidate renderer always
runs. This was most of the 27 s native stage on Jarvis. `server/stage.py` keeps the built orb atlases in
`~/.cache/hallpanel/orb-atlas/`, keyed by `orbs/atlas.py`, every orb frame, lz4, Pillow, Python and byte order;
a kept atlas was byte-identical to a fresh build (11.4 s → 0.3 s on the Mac). A damaged cache entry is rebuilt.
Set `HALLPANEL_REFERENCE_CACHE=` or `HALLPANEL_ATLAS_CACHE=` (empty) to always rebuild. `tests/test_bake.py` and
`tests/test_stage_cache.py` check the keys and exact reuse.

With `--ha`, candidate integration files and the two HA test modules are sent to a temporary
directory in the existing Home Assistant container. The tests import the actual installed HA
classes with fake speech/network services. The directory is removed afterwards. Nothing is
installed in `/config`, no HA token is needed and Home Assistant stays running. These checks do
not produce audio or change panel brightness/volume.

Checked installs:

```sh
sh tools/deploy_jarvis.sh                  # local checks + candidate HA tests before deployment
sh tools/deploy_jarvis.sh --restart-ha     # same, plus HA restart: only when ha/custom_components or ha/*.yaml changed
sh tools/flash_panel.sh                   # local checks + config + build + USB/OTA upload
sh tools/flash_panel.sh /dev/cu.usbmodem101
```

Flashing restarts the panel and can click in the speaker. The flash script saves the currently
built image in ignored `backup/` before compiling. Use the existing hardware baseline/compare
workflow after firmware changes; config validation does not measure physical touch or sound.

## Model evals (explicit paid step)

The default release check never invokes a model. Preview the cases for free:

```sh
.venv/bin/python tests/integration_test.py --list
```

There are 14 scenarios / 15 question turns. They cover calendar adds, failed adds, tomorrow,
contextual followups, empty/unavailable/slow calendars, TV on/off, explicit target calendar,
missing time, English, an outing requiring approval and date arithmetic. Synthetic mock tools
are used; nothing reaches the real calendar or TV. Existing unit tests separately cover cancelled
metro departures, stale weather/calendar data, recording limits and the saved-volume regression.

A paid run requires both `--live` and a question-turn budget, checked before reading keys or
starting a server. Example commands, to run deliberately when changing the prompt/model/tool loop:

```sh
.venv/bin/python tests/integration_test.py --live --agent openai --max-turns 15
.venv/bin/python tests/integration_test.py --live --max-turns 2 --cases calendar-followup
.venv/bin/python tests/integration_test.py --live --max-turns 45 --repeat 3
```

`--agent anthropic --model <model>` selects that backend. API brains always use the real in-process
server/agent loop. For the scenarios its eval-only allow-list is the three mock tools plus the screen.
The optional Claude Code backend uses only its strict mock MCP tools and disables built-in write/network tools.
Production connector settings and the real profile are excluded. Each scenario resets history,
mock events and the duplicate-write guard; followups retain their own preceding turns.

**The budget bounds question turns, not dollars or individual API requests.** Tool loops can use
multiple model rounds. In-process startup may also warm the model. Reported costs come from answer
metadata, can be estimates and exclude separate warm-up requests. Missing cost metadata is `null`,
not zero. No model evals were run while adding this release gate.

## Try your own prompts (explicit paid step)

**Never POST to the production `/converse` (or `/voice`, `/warm`, `/clear` or any other live Jarvis
endpoint) to test the brain. Use this mode.** On 2026-10-23 ad-hoc POSTs to Jarvis created a real
iCloud event, tried a push to Joel's phone and put test questions into his day history.

When Joel says "Sonnet", "Jarvis" or "hjärnan" in this repo, he means the live Jarvis brain
(`JARVIS_BRAIN`/`JARVIS_BRAIN_MODEL`/`JARVIS_BRAIN_EFFORT` in `server.env` on Jarvis; `sh tools/brain.sh`
shows them), not a generic model subagent. Pass the same brain, model and effort here:

```sh
.venv/bin/python tests/integration_test.py --list --agent anthropic --model claude-sonnet-5-5 \
  --prompt "Vad blir vädret i helgen?" --prompts prompts.txt
.venv/bin/python tests/integration_test.py --live --max-turns 4 --agent anthropic --model claude-sonnet-5-5 \
  --prompt "Vad blir vädret i helgen?" --prompts prompts.txt
```

- `--prompt TEXT` repeats; `--prompts FILE` takes one prompt per line and skips blank lines and `#`
  comments. `--prompt` values run first, then the files. Each prompt gets a fresh temporary history;
  `--chain` runs them as one conversation, for followups such as "ja, skicka det".
- Each prompt (times `--repeat`) is one question turn. `--live --max-turns N` is required exactly as
  for the scenarios. `--list` is free: it prints the prompts and the tool policy. Only the in-process
  API brains (`--agent openai|anthropic`) are supported.
- Real tools are the allow-list `PROMPT_READ_TOOLS` in `tests/eval_cases.py`: `vader`, `resa`,
  `flygplan`, `iss`, `nyheter`, `wikipedia`, `polisen`, `webbsok` (with a Jina key) and `skarm`.
  They call the public services from the Mac. `calendar_list_events` is the synthetic mock calendar,
  not iCloud. There is no `hemmet` and no real profile or notes.
- Every other tool the brain is offered is a recording stub (`tests/eval_server.py`). That covers
  `calendar_add_event`, `tv_power`, `radio`, `kom_ihag`, `glom`, `paminnelse`, `till_telefonen` and any
  tool added later. The model sees the tool. The call and its arguments are recorded, the model gets
  a "Testläge … kördes inte" error, and nothing runs. A reply after a write therefore shows how the
  brain handles a refused tool, not its booking confirmation; the mock-calendar scenarios cover that.
- Output per prompt: the reply, tools run, `BLOCKED writes` with arguments, rounds, HTTP/first-token/
  total latency and cost. The JSON report is `tests/results/*-prompts.json` (`tools_tried`, `blocked`).
  Nothing contacts Jarvis, the panel or Home Assistant. `tests/test_eval_prompts.py` checks this offline.

## Results and performance gates

Each paid run writes a separate ignored JSON report in `tests/results/`. It contains the synthetic
questions/replies, tool arguments, behavioural failures, model identity, question-turn budget,
per-turn timings/cost metadata, and p50/p95 text-first-token and HTTP roundtrip timings. Transport
failures fail the run and partial reports are preserved. Failed checks return a nonzero exit code.

Opt-in latency limits apply to every measured question turn:

```sh
.venv/bin/python tests/integration_test.py --live --max-turns 15 \
  --max-first-token-s 5 --max-wall-s 15
```

Those numbers are example thresholds, not measured panel targets. Establish a baseline for the
chosen model, then choose limits. Use repeated comparable cases for a useful p95; one run with
15 varied turns is only a rough sample. Anthropic cache-hit enforcement is opt-in with
`--require-cache`, because the synthetic tool/prompt set may be below a model's cache minimum.

Text-first-token and HTTP completion exclude microphone/STT, TTS and the physical speaker. The
hardware metrics remain in `docs/baseline/`; actual finger-to-visible-change and end-of-speech-to-
first-sound measurements require further instrumentation or a physical test.

When a real bug appears, add a minimal reproducer to the relevant free test module. For model
behaviour, add a case with synthetic inputs and checks of facts/actions. Avoid exact sentence
matches; regex-based answer checks still need human review when a correct paraphrase fails.
