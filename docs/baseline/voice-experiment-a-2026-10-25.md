# Voice experiment A: remove avoidable model and snapshot waits

Existing voice evidence collected read-only on 2026-10-25 at 13:38 Stockholm time.
No new paid model, STT or TTS evaluation was started. The checked production deployment
retains the service's ordinary startup warm-up; that is not a free model benchmark.

## Evidence and bottleneck

The three-day collection contains 153 joined records: 114 have known question text and
127 have a text-to-first-server-audio measurement. These span development versions,
synthetic requests and real questions; do not treat their pooled percentiles as a
production benchmark. HA retained only one pipeline run after its last restart. No
panel log was supplied, so the collection cannot establish acoustic latency.

The 11 question records from October 25, before this change, show:

| Measurement | n | p50 | Maximum |
| --- | --- | --- | --- |
| Text ready to first text | 11 | 1,635 ms | 10,869 ms |
| Text ready to first server audio | 11 | 2,686 ms | 11,441 ms |
| Agent lock queue | 11 | 0 ms | 0 ms |

These are heterogeneous questions and versions, not an after-change cohort or an
estimate of population p95. The largest recurring wait is before first text. The
slowest booking includes a 6,267 ms calendar tool call; the conflict-query range
was already narrowed in the earlier voice work, so this old outlier does not prove
the current calendar path still has that delay.

The most recent weather/umbrella question, at 13:28, used one model round:
2,180 ms from text ready to first text, then 765 ms to first server audio.
Its first sentence's measured server work was 663 ms TTS, 1 ms trim and 15 ms tone.
Further trimming optimizations cannot remove the dominant model/provider waits.

## Change

- Clock and already-fresh calendar answers run before waiting for unrelated warm-up
  reads. They use no new network request. A missing calendar still gets the existing
  bounded wait and a second opportunity to use the completed snapshot.
- Exact Swedish calendar questions now also support tomorrow and an unambiguous
  upcoming weekday. Only a fully parsed snapshot at most 30 seconds old qualifies.
  Compound questions, follow-ups, same-weekday ambiguity, all-day/unknown formats,
  empty source text and dates outside the conservatively covered horizon still
  use the agent. The snapshot's final day is reserved for lookup because its
  collection could have started just before midnight. Silent topics remain excluded.
- Model instructions, calendar writes, speech pause and speech generation are unchanged.

## Reproducible free verification

New regression cases failed on the original implementation, then passed after the
change. The checked deployment passed 512 local tests, ESPHome configuration
validation and 56 tests against actual HA classes with fake providers. Four isolated
tests importing the installed server on Jarvis verified direct clock/today/tomorrow/
Friday answers with snapshot waits and model calls forbidden. Both installed runtime
file SHA256 values matched the local candidate.

An offline microbenchmark interleaved 10 before/after pairs per question on the Mac.
It uses the real `ToolAgent.wait_for_snapshot` against an unfinished Future and the
configured 0.4-second bound. Calendar input is synthetic and already fresh.

| Case | Before p50 / p95 | After p50 / p95 |
| --- | --- | --- |
| Clock | 405.699 / 410.396 ms | 0.135 / 0.355 ms |
| Today's calendar | 403.385 / 405.394 ms | 0.036 / 0.192 ms |
| Upcoming Friday | Model required, deliberately blocked | 0.027 / 0.110 ms, direct |

This proves removal of a bounded software wait under that condition. It is not a
claim of 400 ms saved on every ordinary turn. The blocked old Friday path is not a
completed answer and has no comparable answer-completion time. No acoustic improvement
or one-second target has been established.

Private evidence is retained in ignored `.panel-ci/voice-a-20261025/`: `raw.json`,
`joined.json`, `direct-bench.json`, the original source copies, `bench_direct.py`,
installed-file verification, check/deploy logs and responsiveness reports.
Replay the original collection without any network/provider request:

```sh
tools/panel voice-latency --raw .panel-ci/voice-a-20261025/raw.json --since 2026-10-22T00:00:00+02:00 --turns 0
HALLPANEL_OFFLINE_TESTS=1 PYTHONPATH="$PWD/tests/offline" .venv/bin/python .panel-ci/voice-a-20261025/bench_direct.py
```

## UI release gate

Both runs passed the unchanged `tabs-20-orb-10s-v2` policy and existing measured
baseline, and restored the original page. Firmware was not changed. The following
before/after differences are workload variation, not a UI speedup from this patch.

| Metric, ms | Before p50 / p95 / p99 / max | After p50 / p95 / p99 / max |
| --- | --- | --- |
| Remote release to redraw, 20 taps | 59.360 / 60.731 / 62.284 / 62.284 | 63.193 / 64.831 / 65.719 / 65.719 |
| Orb refresh completion interval | 40.982 / 42.086 / 43.752 / 45.273 | 40.956 / 42.275 / 43.940 / 46.288 |
| Presentation interval, 25 fps fixture | 33.127 / 49.688 / 49.690 / 49.694 | 33.127 / 49.688 / 49.689 / 49.694 |
| Host status HTTP | 19.414 / 70.442 / 72.920 / 72.920 | 18.388 / 69.782 / 95.312 / 95.312 |

Refresh jank over 50 ms was 0% in both runs. UI-task status snapshot p95/max was
432/479 microseconds before and 438/476 after; HTTP-worker p95/max was 2,811/4,124
and 2,801/3,924 microseconds. All raw samples, including slower host responses,
remain in the reports. Software redraw/presentation counters do not measure
finger-to-photon or acoustic latency.

The next actual spoken questions can establish whether the new direct calendar
path is used and how much of the first-audio wait remains. Read them with
`tools/panel voice-latency`; do not relabel this historical collection as new results.

## First real follow-up: recurrence metadata blocked the fast path

At 13:56:30 Joel asked the exact Friday question. It still used one model round:
1,706 ms from text ready to first text and 2,771 ms to first server audio. No tool
call or agent queue was involved. The 3,750 ms listening interval includes his own
speech, segmentation and STT; it is not a post-speech latency measurement.

A free read of the same eight-day agenda reproduced the blocker: two repeated
occurrences on November 1 had date-only rows ending in `| repeats, occurrence
2026-11-01`. The original synthetic tests omitted that mixture. Because every row
had to match the timed-row grammar, unrelated Sunday rows also blocked Friday.

The parser now recognizes this exact recurrence metadata and checks its date
against the row's date. A date-only occurrence on the requested day still requires
the agent; a verified occurrence on another single date does not invalidate the
week. Unknown/unscoped rows and multi-day spans still require the agent. New
regressions first reproduced the failure and then passed, including mismatched
occurrence dates and unknown times on the requested day.

Replaying the real captured agenda produced the correct Friday event in 0.316 ms
of local parsing with no model/provider call. This is neither a whole-request nor
an acoustic timing. Private follow-up captures and verification are retained in
`.panel-ci/voice-a-followup-20261025/`.

The calendar formatter's `row()` implementation confirms that a date-only row is
a single-day all-day occurrence; multi-day all-day events use `start .. end` and
remain rejected. The corrected checked deployment passed 515 local and 56 HA
tests. An isolated process importing the installed server and reading a fresh real
agenda returned the Friday answer as `brain=direct`, zero snapshot wait, in 0.948 ms.
Its model and wait methods raised on use. Installed runtime hashes matched the
candidate and the service remained active. This still excludes STT, TTS and sound.

The follow-up UI gate passed the same unchanged baseline and restored the page:

| Metric, ms | Before p50 / p95 / p99 / max | After p50 / p95 / p99 / max |
| --- | --- | --- |
| Remote release to redraw | 66.047 / 72.190 / 72.403 / 72.403 | 65.801 / 71.080 / 71.290 / 71.290 |
| Orb refresh completion interval | 40.974 / 42.328 / 43.461 / 45.158 | 40.959 / 42.023 / 43.358 / 47.133 |
| Presentation interval, 25 fps fixture | 33.127 / 49.688 / 49.690 / 49.694 | 33.127 / 49.688 / 49.691 / 49.692 |
| Host status HTTP | 21.178 / 80.309 / 211.207 / 211.207 | 19.653 / 75.817 / 78.234 / 78.234 |

Refresh jank over 50 ms remained 0%. UI-task status p95/max was 423/479 microseconds
before and 436/470 after; worker p95/max was 2,942/3,944 and 3,903/4,609 microseconds.
These are unchanged-firmware control measurements, not a rendering improvement.

## Confirmed real direct turn, 14:07

Joel repeated the Friday calendar question at 14:07:35. The joined HA/journal/history
record reports `brain=direct`, zero model rounds and no tool calls. Text ready to
first text was 3 ms, versus 1,706 ms at 13:56; text ready to first server audio was
828 ms, versus 2,771 ms. The server's answer computation itself took 1 ms. Speech
work was 701 ms cloud TTS, 1 ms trim and 30 ms tone (732 ms total).

The 1,943 ms reduction (70%) is an observed comparison of two individual turns,
with different spoken wording and provider variation, not a population percentile
or a controlled acoustic benchmark. The 4,047 ms listening interval includes Joel's
own question, silence detection and STT. No panel log or external recording exists
for this turn, so the one-second end-of-speech-to-audible-answer goal remains unproven.
Private raw and joined data are in `.panel-ci/voice-a-latest-20261025/`.
