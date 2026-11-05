#!/usr/bin/env python3
"""Jarvis's speaking pace, in characters a second, from his real replies (reply_fit.h's CHARS_PER_S rolls the
voice page's long replies at this pace). Runs on Jarvis; standard library and ffprobe only; reads, never writes:

  tools/jarvis_ssh.sh user@jarvis.local 'timeout 120 python3 -' < tools/speech_rate.py

Home Assistant's TTS cache names each file sha1(text)_<lang>_<options>_<engine>.wav, so hashing history.db's
messages finds their audio, and ffprobe gives its length. Only the cache's newest ~50 files survive, so a run
measures recent replies. 2026-11-01: 23 replies, median 11.3 chars/s (p10 9.6, p90 13.1).
"""
import glob
import hashlib
import os
import sqlite3
import statistics
import subprocess

HISTORY = os.path.expanduser("~/.local/share/hallpanel/history.db")
CACHE = os.path.expanduser("~/docker/homeassistant/config/tts")
ENGINE = "tts.jarvis_mattias"

texts = [t for (t,) in sqlite3.connect(HISTORY).execute("select text from messages") if isinstance(t, str)]
files = {os.path.basename(f).split("_")[0]: f for f in glob.glob(f"{CACHE}/*{ENGINE}*")}
rates = []
for text in texts:
    path = files.get(hashlib.sha1(text.encode()).hexdigest())
    if not path:
        continue
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", path],
                         capture_output=True, text=True).stdout.strip()
    if out and float(out) > 2:          # very short clips are mostly the voice's start and end
        rates.append(len(text) / float(out))
rates.sort()
print(f"{len(rates)} replies matched of {len(files)} cached {ENGINE} files")
if rates:
    print(f"chars/s median {statistics.median(rates):.1f}, p10 {rates[len(rates) // 10]:.1f}, "
          f"p90 {rates[len(rates) * 9 // 10]:.1f}")
