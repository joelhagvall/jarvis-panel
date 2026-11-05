# Maintain the harness when a real lesson appears

This is an event-driven review during ordinary work, not a scheduled background
agent or a requirement to produce a new diary entry for every edit. AGENTS.md
makes it part of each agent's workflow. Most discoveries belong in an existing
helper/test/runbook, not in another document.

## Smallest useful feedback loop

1. Reproduce the failure or check the disputed assumption. Preserve failed/slow
   evidence in the existing private run; do not replace it with a green rerun.
2. Fix the owning helper. Add a targeted free regression when it can detect a
   real recurrence; do not test incidental text or implementation shape alone.
3. Update the existing canonical how-to and link its evidence. Keep fact,
   inference and unsupported measurement separate. A source fix is not an optical
   or acoustic measurement.
4. Before finishing, say what protection changed and how it was verified. If
   nothing new was learned, add nothing. Editing an unrelated global skill or
   adding a new paid/physical test is not implied by this review.

## Where this session's lessons already live

| Lesson | Owning protection / canonical guide |
| --- | --- |
| The public copy must not carry private history, personal strings or uncommitted parallel work | tools/publish_public.py (committed tree only, ignored denylist, blocks on a hit, never pushes), tests/test_publish_public.py; docs/public-export.md |
| Parallel sessions change the same file: commit only this session's hunks | tools/stage_hunks.py (`--list`, then `FILE:N,...`), tests/test_stage_hunks.py |
| Pixel-preserving native optimization and negative memory results | tests/test_qoi.py, tests/test_blur.py; docs/baseline/qoi-blur-2026-10-25.md |
| Shared audio clock requires both codecs and an explicit 16 kHz voice adapter | tests/test_panel_audio.py; actual ART spectral/continuity tests; docs/audio-quality.md |
| Microphone on-after-off skipped wake start because the optimistic switch still held its old state inside turn_on_action | post-publication on_turn_on/off in board_config/voice.py; tests/test_mic_resume.py exercises actual TemplateSwitch ordering; separate mic_enabled/wake_running diagnostics and mic_state logs; docs/voice-plan.md, docs/remote-panel.md |
| On-demand media artwork must not enter the boot download chain or block the UI while fetching/decoding | components/async_artwork, tests/test_async_artwork.py, tests/test_panel_performance.py; docs/now-playing.md |
| Lower UI wait time and a good average hid missed record images; metadata and feedback competed for the same frame; an idle navigation phase lost its intended background load | tools/vinyl_flow.py, tools/panel_music_load.py, tests/test_vinyl_flow.py, tests/test_music_feedback.py, tests/test_panel_music_load.py, tests/test_vinyl_line.py, tests/test_vinyl_damage.py; docs/now-playing.md |
| Static detail updates repainted unchanged content/full screens, competed with input and AI value boxes crossed their parent bounds | tools/panel_detail_load.py checks fresh updates, real loaded taps, drain after restoration and clipping; tests/test_ui_priority.py, tests/test_panel_detail_load.py, tests/test_static_ui_updates.py (exact pixels, real pointer/page ordering, unchanged-data and capacity checks); docs/responsiveness.md |
| A fixed reconnect timer could announce success without a successful HA action; HA's notification handler then swallowed backend failure replies | output_feedback.h tracks tickets/actual state/bounded responses; HA select/button return ServiceValidationError; tests/test_output_feedback.py, tests/test_media_ha.py; docs/bluetooth-speaker.md |
| A JPEG encoder failure also broke its JPEG clip fallback | panel_remote.py captures raw RGB565 as PNG only for that specific error; tests/test_panel_remote.py; docs/remote-panel.md |
| A null LVGL transition descriptor crashed only when a real music button was pressed | generated-config rejection in tests/test_panel_performance.py; decoded crash and owning API notes in docs/now-playing.md |
| A wrapped song title moved the playback buttons; a restore error could hide an ambiguous input timeout | fresh semantic control resolution and original-error retention in tools/vinyl_flow.py, tests/test_vinyl_flow.py; docs/now-playing.md |
| Firmware rolled back during a gate and ESPHome reconnected inside its log process | build identity after every gate and reconnect-aware crash parsing, tests/test_panel_gates.py, tests/test_panel_crash.py; docs/restart-crash.md |
| Night SL status is right-aligned live content, not a permanently fixed walking-time label | explicit two-value right-edge contract, immutable-PNG policy rebind and tests/test_panel_visual.py; docs/visual-regression.md |
| A prior real reply's smaller fitted font leaked into the synthetic voice screenshots | fixed fixture font with ownership-guarded restoration in tools/panel_test_codegen.py, tests/test_panel_test_mode.py; docs/visual-regression.md |
| HTTP formatter temporary stack use | tests/test_panel_status_format.py; docs/baseline/boot-fast-2026-10-25.md |
| Never satisfy HA freshness by publishing cached states | tests/test_boot_cache.py, tests/test_boot_render_fence.py; docs/baseline/boot-cache-2026-10-26.md |
| No forced PWM blackout / premature ready marker / splash over dashboard | tests/test_boot_experience.py, tests/test_boot_render_fence.py; docs/baseline/boot-reveal-2026-10-26.md |
| Real wakeup legitimately changed 20% to 65% | Read back originals and provenance; preserve the selected day volume (20% is night preference), restore only still-owned changes; docs/wakeups.md |
| Nested chat objects must not own the viewport drag | tests/test_panel_chat.py; physical chat_scroll fixture in tools/panel_ui_test.py; docs/language-switching.md |
| Slow or flaky SL responses must not block panel status, duplicate requests or erase the metro board | tests/test_home_status.py, tests/test_sl_resilience.py (replayed real SL mix, fixtures in tests/fixtures/sl/); one shared asynchronous minute cache, bounded ten-minute absolute departures, spurious empty replies never erase trains, Retry-After handling; live: `tools/panel sl-check`, and every 5 minutes with phone pushes on change via `hallpanel-sl-check.timer` (which also pushes panel health and crashes, server/panel_health.py); docs/voice-plan.md |
| Temporary mDNS failure is not a device crash | wait_boot retries read-only construction/reads, preserves identity/uptime checks; tests/test_panel_boot_wait.py |
| Absent/reassigned USB must stop before opening/resetting | tools/panel preflight; tests/test_panel_preflight.py; checked flash defaults to Wi-Fi |
| Compressed container size does not include extraction headroom | tools/install_music_assistant.py rejects low space before Docker; tests/test_spotify.py; docs/spotify.md |
| ESPHome has no title metadata and ANNOUNCING is not music playback | tests/test_media_ha.py and tests/test_now_playing.py; tests/test_media_ha.py (stale covers, cache invalidation, bounded decoding and public-read isolation); docs/now-playing.md |
| HA tests can authenticate while plain deployment SSH fails | tools/jarvis_ssh.sh is shared by deployment/staging/rsync; tests/test_jarvis_ssh.py; docs/autonomous-ci.md |
| A transient iCloud refusal must not latch until the password changes | bounded fresh discovery after auth backoff; tests/test_calendar_sync.py; docs/calendar-live.md |
| SR splits programme title and presenter into separate fields | compose the full heading while retaining current song/artist precedence; tests/test_radio_metadata.py; docs/now-playing.md |
| Host-latency gate failures can be the Mac's link, not the panel (Mac on the panel's 2.4 GHz channel 6; waiting for a "calm network" did not help) | `tools/panel host-link` (read-only; ping before the Wi-Fi profiler, which disturbs the radio); responsiveness `hint` on host round-trip failures; tests/test_panel_host_link.py, tests/test_panel_responsiveness.py; docs/responsiveness.md |
| A host-link run beside a latency gate failed that gate (status p95 108 ms, 59 ms when rerun alone) | host-link refuses while the device lock is held; tests/test_panel_host_link.py |
| Post-upload gates were retyped by hand, blocked by night mode and reported "exit 0" when the flash failed; after night gates the panel stayed lit (the 120 s idle trigger was used up) | `tools/panel gates` (night exit by real tap, Natt tab restore, `--restore-night` from the flash's pre-upload `--night-flag`, `--from` for diagnosis only), `FLASH OK`/`FLASH FAILED at <stage>` last line; tests/test_panel_gates.py, tests/test_panel_visual.py |
| Lock contention found only after minutes of checks, with no holder named; restricted process inspection then mislabeled a live holder dead | early device-lock probe in flash_panel.sh; `tools/panel lock status/wait`, holder pid/command in the error; EPERM means the PID exists and unavailable `ps` never means dead; tests/test_panel_gates.py |
| Nobody knew which tree state ran on the panel ("unversioned") | flash_panel.sh stamps CI's build id and saves `.panel-ci/last-flash.json` with the shipped uncommitted files |
| NASA's credit "Večerník" showed boxes on Tavla's plaque | plaque fonts carry Latin-1 + Latin Extended-A (gen.py PLAQUE_GLYPHS), server/agents/background.py plaque_text folds the rest; tests/test_plaque_glyphs.py keeps both sets equal |
| A too-small scratch in voice_wave.h overflowed the panel's heap; the plain test missed it | tests/test_voice_wave.py compiles with AddressSanitizer at the panel's exact scratch size; tools/check_orb_prefetch.py runs under ASan too |
| A wave image missed by the worker was drawn on the UI task (~20 ms), starving the main loop | the last finished wave is shown again (`orb_wave_held`); docs/responsiveness.md |
| An announcement settled the orb at once while Jarvis was still speaking it | voice_done also waits while the player is ANNOUNCING; tests/test_panel_performance.py |
| The voice page starved the 100 ms speaker ring about 2 s into each reply | `speaker_out` buffer 500 ms; tests/test_speaker_gaps.py; docs/audio-quality.md |
| The orb jumped between states and changed speed abruptly; then cross-fades and syllable-paced speed looked disjointed | orb_director.h (seamless entries at shared drawn frames, slewed speed, voice-driven wave), atlas v6; tests/test_orb_director.py, optional tools/check_orb_prefetch.py; docs/responsiveness.md |
| Joel heard replies stutter and lose words; the server's cached audio was intact and the panel logged nothing | `audio_gap` warnings from bench/speaker_gaps.h on speaker_out; tests/test_speaker_gaps.py; docs/audio-quality.md, docs/voice-latency.md (exact played bytes from the speech cache) |
| Three `/transcribe` 503s on 2026-10-26 left no reason in the journal | server logs `transcribe unavailable: <reason>` (no transcript, key or provider body); voice-latency shows it in the note; tests/test_transcription.py, tests/test_voice_latency.py |
| Failed recognitions and silent wakes left no lasting trace: the Pi's journal held one day, 98 % of hallpanel's lines were polls, HA keeps 10 runs | `voice_turns` in history.db via `POST /turn` and the turn id on `/transcribe`/`/converse`; polls kept out of the access log (`quiet_request`); `tools/panel turns`; tests/test_server.py, tests/test_show_turns.py, tests/test_panel_voice.py (VoiceTurnReportTest); docs/voice-latency.md |
| The build id as a global `-D` build flag rebuilt all ~2000 objects on every flash (compile 126–156 s; 34 s incremental once moved) | the id is the `panel_debug` `build_id` setting and lands in main.cpp only; tests/test_panel_ci.py rejects it in `build_flags` |
| `--restart-ha` left to memory; deploy silently shipped other sessions' files | deploy_jarvis.sh compares HA files by content and restarts only then, lists uncommitted files; tests/test_deploy_restart.py |
| Ad-hoc prompts against the production `/converse` wrote a real calendar event | `tests/integration_test.py --prompt/--prompts` with a read-only tool allow-list; tests/test_eval_prompts.py; docs/evals.md |
| `tools/brain.sh` used plain ssh, which fails from the Mac | brain.sh uses tools/jarvis_ssh.sh; tests/test_jarvis_ssh.py rejects plain ssh in tools/*.sh |
| Boots aborted in operator new / I2S DMA after a flash; the rolled-back build's crash overwrote the candidate's and could not be decoded | 5 ms boot-heap sampler and `tools/panel boot-heap`/`restart`, `logs --older`, ELF archive per build in tools/flash_panel.sh, 16 kB TCP window in board_config/hardware.py, PSRAM status reply; tests/test_panel_diagnostics.py, tests/test_panel_status_format.py; docs/remote-panel.md "Diagnostics" |
| The first diagnostics build sent no clip frames (PPA output size not on a cache line), a full-screen clip halved the frame rate it measured, and night times arrived percent-encoded | aligned PPA output and `observer_note` in tools/panel_diag.py; `form_body` keeps `:`; tests/test_panel_diagnostics.py; docs/remote-panel.md "Diagnostics" |

Do not copy those implementations or their numeric thresholds here. The linked
boot reports are historical evidence, not instructions to preserve an obsolete
UI badge or to claim the latest running version from an old report header.

## Quick preflight, no hardware action

```
tools/panel preflight
tools/panel preflight --usb
tools/panel preflight --require-usb --device /dev/cu.SELECTED --serial-number KNOWN_SERIAL
```

The first uses the project venv and selects the network target without contacting
it. The second only lists USB metadata. A list is not proof that the panel is
present. The third fails if that explicit USB port is absent, ambiguous or has a
different known serial. None opens a port or resets a board. USB presence/serial
does not establish its ESP32 model or network identity; check target provenance.
Do not infer current settings from a previous chat or a framebuffer screenshot.

The checked flash script validates its selected target before build and again
before upload. Without DEVICE it uses hallpanel.local, even when USB is plugged
in. An explicit absent USB target never silently falls back to Wi-Fi. The main
CI already uses explicit OTA targets only. Its boot wait retries transient
read-only resolution/transport failures within the existing wait budget; it
never scans, falls back to a remembered IP, retries input or accepts a wrong
firmware/new restart as success. OS DNS calls retain their own resolver timeout.

## Keep it small

Use existing files first. Merge duplicate/superseded guidance where it is used.
Keep long logs, screenshots and one-use probes private and ignored. Add a shared
script only for demonstrated reuse or a concrete repeatable risk. Keep standing
preferences out of measurement reports as supposed permanent telemetry. Preserve
existing limits/quarantine/approval boundaries; maintenance cannot declare its
own changed reference or skipped check to be a pass.
