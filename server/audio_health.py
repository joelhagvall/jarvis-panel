#!/usr/bin/env python3
"""A cheap sample of the speaker's audio health every 5 minutes, so a stutter or a dead Spotify can be looked
back on instead of guessed (docs/spotify.md, "Logs"). hallpanel.service runs it in a thread; nothing is pushed.

Each sample is one JSON line in ~/.local/state/hallpanel/audio.jsonl (the last 2016, a week): BlueALSA's
"PCM drop" lines since the previous sample (each is an underrun the speaker may have played as a click), the
JBL sink's error count in PipeWire, whether the speaker is connected, and Soloist's state and version. A sample
with drops is also printed to the journal (`journalctl --user-unit hallpanel | grep audio:`).

  python3 server/audio_health.py            the last day: samples, drops per hour, Soloist's state
  python3 server/audio_health.py --sample   take one sample now and print it
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import time

LOG = Path.home() / ".local/state/hallpanel/audio.jsonl"
KEEP = 2016
EVERY_S = 300
SOLOIST = Path.home() / ".local/share/soloist/bin/soloist"


def _run(args, timeout=8):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def drops(since):
    out = _run(["journalctl", "-u", "bluealsa", "--since", f"@{int(since)}", "--no-pager", "-o", "cat"])
    return None if out is None else sum(1 for line in out.splitlines() if "PCM drop" in line)


def sink_errors(top=None):
    """The ERR column of PipeWire's `jbl` node (counted since PipeWire started), or None."""
    top = top if top is not None else _run(["pw-top", "-b", "-n", "2"], timeout=6)
    rows = [line.split() for line in (top or "").splitlines() if line.rstrip().endswith(" jbl")]
    try:
        return int(rows[-1][8]) if rows else None
    except (ValueError, IndexError):
        return None


def soloist():
    ws = os.environ.get("JARVIS_SOLOIST_WS") or "127.0.0.1:9431"
    status = _run([str(SOLOIST), "ctl", "--ws", ws, "status"]) or ""
    version = (_run([str(SOLOIST), "--version"]) or "").split(" build")[0].strip()
    # With --ws, ctl says "soloist: reachable" instead of "running (pid N)" (Soloist 1.3.10, 2026-10-28).
    return {"running": status.startswith(("soloist: running", "soloist: reachable")), "logged_in": "logged in: yes" in status,
            "active": "active device: yes" in status, "version": version or None}


def speaker_connected():
    out = _run(["bluetoothctl", "devices", "Connected"])
    return None if out is None else bool(re.search(r"^Device ", out, re.M))


def sample(since, now=None):
    now = time.time() if now is None else now
    return {"time": int(now), "drops": drops(since), "sink_errors": sink_errors(),
            "speaker": speaker_connected(), "soloist": soloist()}


def append(entry, path=LOG, keep=KEEP):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = path.read_text().splitlines()[-(keep - 1):] if path.exists() else []
    tmp = path.with_suffix(".tmp")
    tmp.write_text("\n".join(lines + [json.dumps(entry, separators=(",", ":"))]) + "\n")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def run(stop, path=LOG, every=EVERY_S, log=None):
    """Sample until stop is set. Never raises: a broken sample is skipped, the server keeps running."""
    log = log or (lambda text: print(f"audio: {text}", file=sys.stderr, flush=True))
    since = time.time()
    while not stop.wait(every):
        try:
            now = time.time()
            entry = sample(since, now)
            since = now
            append(entry, path)
            if entry["drops"]:
                log(f"{entry['drops']} BlueALSA drop(s) in the last {every // 60} min, JBL sink errors "
                    f"{entry['sink_errors']}, Spotify {'logged in' if entry['soloist']['logged_in'] else 'logged out'}")
        except Exception as e:      # noqa: BLE001 - a health sample must never take the server down
            log(f"sample failed: {type(e).__name__}: {e}")


def watch(stop):
    threading.Thread(target=run, args=(stop,), name="audio-health", daemon=True).start()


def summary(path=LOG, hours=24, now=None):
    now = time.time() if now is None else now
    entries = [json.loads(line) for line in (path.read_text().splitlines() if path.exists() else []) if line.strip()]
    recent = [e for e in entries if e.get("time", 0) >= now - hours * 3600]
    if not recent:
        return f"No audio samples in the last {hours} h ({path})."
    per_hour = {}
    for e in recent:
        hour = time.strftime("%m-%d %H:00", time.localtime(e["time"]))
        per_hour[hour] = per_hour.get(hour, 0) + (e.get("drops") or 0)
    last = recent[-1]
    lines = [f"{len(recent)} samples in the last {hours} h; drops {sum(per_hour.values())} in all.",
             f"Now: JBL sink errors {last.get('sink_errors')}, speaker connected {last.get('speaker')}, Soloist {last.get('soloist')}."]
    lines += [f"  {hour}  {count} drop(s)" for hour, count in per_hour.items() if count]
    return "\n".join(lines)


if __name__ == "__main__":
    if "--sample" in sys.argv:
        print(json.dumps(sample(time.time() - EVERY_S), indent=1))
    else:
        print(summary())
