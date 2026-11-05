#!/usr/bin/env python3
"""Does the hey_jarvis wake-word model wake on Swedish "Hej Jarvis"? Runs the same model the
panel runs (ESPHome micro_wake_word, here through pymicro-wakeword) over speech clips.

Clips are named <voice>__<pos|neg>-<phrase>.<ext> in one folder. Each clip is converted to
16 kHz mono, padded with silence, and tested clean and with background noise added (white noise
at about -32 dBFS, a busy hallway rather than a quiet one). Needs ffmpeg and pymicro-wakeword.

  python tests/wake_word_test.py <clips-folder>   ->  table + tests/last-wake-word-run.json
  python tests/wake_word_test.py --count <recording>   how many times it wakes in one recording,
                                                        with the time of each wake and the peaks
"""
import json
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

from pymicro_wakeword import MicroWakeWord, MicroWakeWordFeatures, Model

ROOT = Path(__file__).resolve().parent.parent


def pcm(path, noise):
    """16 kHz mono s16 with 1 s of silence before and after, optionally mixed with noise."""
    pad = "adelay=1000|1000,apad=pad_dur=1"
    if noise:
        filt = (f"[0:a]aformat=sample_rates=16000:channel_layouts=mono,{pad}[s];"
                "anoisesrc=color=white:amplitude=0.025:sample_rate=16000[n];"
                "[s][n]amix=inputs=2:duration=first:normalize=0[o]")
        args = ["-filter_complex", filt, "-map", "[o]"]
    else:
        args = ["-af", f"aformat=sample_rates=16000:channel_layouts=mono,{pad}"]
    return subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(path), *args,
                           "-ar", "16000", "-ac", "1", "-f", "s16le", "-"], check=True, capture_output=True).stdout


def wakes(audio):
    """True if the model fires anywhere in the clip, and the highest probability it saw."""
    mww, feats = MicroWakeWord.from_builtin(Model.HEY_JARVIS), MicroWakeWordFeatures()
    probe, pfeats = MicroWakeWord.from_builtin(Model.HEY_JARVIS), MicroWakeWordFeatures()
    fired, best = False, 0.0
    for i in range(0, len(audio), 320):                       # 10 ms chunks
        chunk = audio[i:i + 320]
        for f in feats.process_streaming(chunk):
            fired = bool(mww.process_streaming(f)) or fired
        for f in pfeats.process_streaming(chunk):
            p = probe.process_streaming_prob(f)
            if p is not None:
                best = max(best, float(p))
    return fired, round(best, 3)


def count(path):
    audio = pcm(path, False)
    mww, feats = MicroWakeWord.from_builtin(Model.HEY_JARVIS), MicroWakeWordFeatures()
    probe, pfeats = MicroWakeWord.from_builtin(Model.HEY_JARVIS), MicroWakeWordFeatures()
    wakes_at, peaks, last, current, above_t = [], [], -9.0, 0.0, -9.0
    for i in range(0, len(audio), 320):
        t = i / 32000 - 1.0                                   # seconds into the recording (1 s pad)
        chunk = audio[i:i + 320]
        for f in feats.process_streaming(chunk):
            if mww.process_streaming(f) and t - last > 1.5:
                wakes_at.append(round(t, 1))
                last = t
        for f in pfeats.process_streaming(chunk):
            p = probe.process_streaming_prob(f)
            if p is not None and p > 0.3:
                if t - above_t > 0.5 and current:            # a new utterance starts
                    peaks.append(round(current, 2))
                    current = 0.0
                current, above_t = max(current, float(p)), t
    if current:
        peaks.append(round(current, 2))
    print(f"woke {len(wakes_at)} times, at {wakes_at} s")
    print(f"peak per utterance (above 0.3): {peaks}  (it wakes when a 5-step average reaches 0.97)")


def main():
    if sys.argv[1] == "--count":
        return count(Path(sys.argv[2]))
    folder = Path(sys.argv[1])
    rows = []
    for clip in sorted(folder.iterdir()):
        if "__" not in clip.stem:
            continue
        voice, phrase = clip.stem.split("__")
        for noise in (False, True):
            fired, best = wakes(pcm(clip, noise))
            rows.append({"voice": voice, "phrase": phrase, "noise": noise, "fired": fired, "max_prob": best})
    (ROOT / "tests" / "last-wake-word-run.json").write_text(json.dumps(rows, ensure_ascii=False, indent=1))

    by = defaultdict(list)
    for r in rows:
        by[(r["phrase"], r["noise"])].append(r)
    print(f"{'phrase':16} {'clean':>12} {'with noise':>12}   voices that missed (clean)")
    for phrase in sorted({r["phrase"] for r in rows}):
        c, n = by[(phrase, False)], by[(phrase, True)]
        miss = [r["voice"] for r in c if (not r["fired"]) == phrase.startswith("pos")]
        label = "missed" if phrase.startswith("pos") else "woke by mistake"
        print(f"{phrase:16} {sum(r['fired'] for r in c):>5}/{len(c):<6} {sum(r['fired'] for r in n):>5}/{len(n):<6}   "
              f"{label}: {', '.join(miss) or '-'}")


if __name__ == "__main__":
    main()
