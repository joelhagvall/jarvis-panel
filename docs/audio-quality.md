# Shared 48 kHz audio and 16 kHz voice consumers

The ES8311 DAC and ES7210 ADC share physical MCLK/BCLK/LRCLK pins. The microphone
I2S port drives those clocks continuously; the separate speaker port remains
secondary. `mic_clock` retains its existing always-active clock-holder role.
Changing only media_pipeline.sample_rate cannot improve playback correctly while
the physical clock and either codec remain configured for 16 kHz.

`board.py` configures the physical microphone, ES7210, ES8311 and output speaker
at 48 kHz/16-bit mono. Music uses HA's standard 48 kHz mono FLAC proxy to preserve
music bandwidth without 768 kbps raw WAV network traffic. Mono reflects the one
physical speaker. All voice/wake-word consumers retain the `mics` ID and exactly
16 kHz mono; the physical I2S source is now `mics_raw`.

`components/panel_audio` adapts that raw source through ESPHome's managed ART
resampler, with a 128-tap/16-phase antialias filter and unity microphone gain.
Conversion runs in the existing microphone task, outside the LVGL/main loop.
It handles arbitrary even-sized input fragments, consumes every input frame,
uses bounded/reserved output storage and fails on format/progress errors. Start
and stop forward the existing consumer reference counts to the physical mic.
10-second numeric logs report observed input/output frame counts, processing
wall time, clipping and errors; no microphone audio is stored or transmitted by
this diagnostic.

Speech/announcements and built-in sounds retain their 16 kHz WAV format. The
standard ESPHome resampler speaker converts them to the shared 48 kHz mixer;
its additional task stack lives in PSRAM. Existing ducking still targets
`media_spk`. The stock resampler applies its normal 3 dB headroom when converting
speech; preserving the selected panel volume does not establish identical
acoustic loudness. No new paid model/STT/TTS evaluation is part of installation.

A starved reply logs nothing by itself: ESPHome's I2S task pads a DMA buffer with
silence whenever the decoder, resampler or mixer has nothing ready. The board
therefore hooks `speaker_out`'s audio output callback (`bench/speaker_gaps.h`)
and logs every hole of more than 5 ms inside one sound as
`[W][audio_gap]: speaker starved N ms at M ms into the sound`, at most once a
second. Silence longer than 1.5 s starts a new sound. Spoken pauses are real
samples, so they never count. The same numbers go to Home Assistant as the
diagnostic sensors "Ljudglapp" (count since boot) and "Senaste ljudglapp" (ms),
so a stutter at any hour can be dated from HA's history without a running log;
tests/test_speaker_gaps.py. An `assist_satellite.announce` without
`"preannounce": false` adds one gap between its chime and the speech; that is
two sounds, not a stutter. Since 2026-10-30 the listening chime marks a new sound
(`new_sound`, on_listening): a "Hey Jarvis" while a reply still played logged a
200 ms gap that was the chime replacing the reply, not starvation. Holes within
1 s of that mark start a new sound.

On 2026-10-27 the same cached reply starved the 100 ms speaker ring for 20-80 ms
about 2 s into every announcement shown on the voice page, and never when played
on the dashboard (`media_player.play_media` with `announce`). `speaker_out` now
has a 500 ms ring (PSRAM); the same test then gave no gap in three runs. A real
streamed Jarvis turn was not part of that test.

The repeatable test, audible in the hallway at the panel's volume (repeats come from HA's TTS cache):

```sh
sh ~/.claude/skills/hallpanel/scripts/panel.sh logs 30 &   # audio_gap and media_player lines
sh tools/jarvis_ssh.sh user@jarvis.local 'curl -s -X POST -H "Authorization: Bearer $(cat ~/.config/homeassistant/token)" \
  -H "Content-Type: application/json" -d "{\"entity_id\":\"assist_satellite.hallpanel_assist_satellite\",\"message\":\"Hej Joel. Det här är ett kort test.\",\"preannounce\":false}" \
  http://127.0.0.1:8123/api/services/assist_satellite/announce'
```

`media_player.play_media` with `"announce": true` and a WAV under `/local/hallpanel/` plays the same
sound on the dashboard, without the voice page, to tell drawing load from the stream.

`tests/test_panel_audio.py` compiles the actual firmware microphone converter and
its managed ART library on the host. It checks fragmented versus whole input,
exact sample continuity/counts, malformed frame rejection, voice-band gain and
alias rejection. Tested 100–6500 Hz tones differ by at most 0.034 dB. A 9 kHz
tone is suppressed by 69.3 dB. Higher tested tones fall below this 16-bit test's
measurement floor, not a claim of infinite physical attenuation. The generated
configuration contract keeps both codecs/clock at 48 kHz while wake word/STT
remain 16 kHz. Dependencies must already be resolved by the panel build; missing
managed audio sources fail checks instead of skipping them.

Installation keeps all ordinary visual, responsiveness, 60 fps, soak, memory and
crash gates. Retain before/after samples in `.panel-ci/audio-48k-20261026`, read
back the actual selected volume, verify physical playback and announcement
compatibility with free audio, and inspect actual microphone cadence/error logs.
Software/digital checks do not establish acoustic frequency response or real
spoken wake-word recognition accuracy. Those need physical observation.


## Physical result, October 26

The installed build is `795a3642f429d977d2df980abc5cf856` on boot `912256187`.
`.panel-ci/run-20261026-135258-955972/report.json` passed 626 free local tests,
configuration, linked guarded restart, OTA/identity/crash logs, physical smoke,
synthetic modes, fourteen unchanged visual scenes, memory/animation references,
responsiveness, production 60 fps capacity, 120-second soak and the new observed
audio-clock gate. No references or release thresholds were replaced.

The initial config validation found a schema function returning None; the wrapper
now returns its config and the ordinary configuration gate catches this contract.
The first firmware compile found a namespace collision between the DSP core and
ESPHome's generated component lookup. DSP now has a distinct namespace; the native
probe also uses generated-main-style namespace lookup to catch a recurrence.
Both failed pre-installation reports remain private under `.panel-ci`.

Before versus after release-to-redraw p50/p95/p99/max was
52.996/60.653/61.701/61.701 versus 52.727/57.471/59.345/59.345 ms, with zero
animation jank. The before host status measurement failed only host RTT
(p95 270.894 ms); the retained after measurement passed (p50/p95/p99/max
20.775/70.699/74.910/74.910 ms). These host observations do not measure physical
finger-to-photon latency or prove the audio change caused an improvement.

The device's encrypted native API advertises FLAC/48000/mono for media and
WAV/16000/mono for announcements. Twenty-two runtime audio windows observed
47966.4–48076.8 raw frames/s and 15988.8–16025.6 filtered frames/s, with zero
conversion errors and zero clipped output samples. An additional actual P3 music
run passed the unchanged production throughput reference: 60.35 presentations/s,
0 repeated frames out of 603 presentation intervals, zero jank and presented
interval p50/p95/p99/max 16.562/16.568/16.573/16.622 ms. During music, host status
RTT p50/p95/p99/max was 20.456/35.061/57.740/57.740 ms; minimum free memory was
222064 internal bytes and 15612300 PSRAM bytes. Audio cadence remained 48000 to
16000 frames/s with zero errors/clipping under that load.

A real touch on Settings' volume control at the existing 65% played the compiled
16 kHz listen sound, observed ANNOUNCING, returned to idle, retained 65% and
restored the original page. The microphone cadence stayed valid through this
announcement test. No paid STT/model/TTS was invoked. P3 was stopped after testing.
Artifacts and the final proof index are in `.panel-ci/audio-48k-20261026/`.

A sentence followed by a long wait (Jarvis's tools) lost its end until more audio came: "Självklart, jag tittar på tvättstugan." stopped at "tvättstu" for 10-20 s (2026-11-01), the rest held by ffmpeg in HA and the panel's 24 KiB reads. `speech.WavStream.flush` now sends about 0.8 s of silence 0.3 s after a sentence when nothing follows (`FLUSH_AFTER_S`, `FLUSH_BYTES` in `ha/custom_components/jarvis_agent/speech.py`; tests/test_speech.py and test_panel_voice.py in HA's runtime). A slow synthesis of a sentence whose text is already there is not flushed.
