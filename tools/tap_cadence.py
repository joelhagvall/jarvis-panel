#!/usr/bin/env python3
"""Redraws per interval before and after a real tap, to tell a freeze from a slow animation (docs/now-playing.md).

    tools/panel tap-cadence X Y [--seconds 3.5] [--before 1.0] [--again-after 2.5]
    tools/panel tap-cadence --no-tap [--seconds 2]          the cadence without a tap, as a baseline

Polls the status's lifetime render_frames_total as fast as the link allows and prints (ms since start, redraws,
over ms) per poll, the tap's start and end, and redraws a second in 250 ms windows. A tap is a real widget
action (Spelas nu's play/pause pauses the music): --again-after taps the same place once more that many seconds
after the first, in the same session, so a pause is undone. The session's device lease is released at the end: a
second run right after the first answered 409 and left the music paused (2026-10-28).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_remote import PanelClient  # noqa: E402


def windows(rows, width_ms=250):
    """[(window start ms, redraws a second)] from (ms, frames_total) polls."""
    out = {}
    for (t0, f0), (t1, f1) in zip(rows, rows[1:]):
        out.setdefault(t1 // width_ms * width_ms, [0, 0])
        out[t1 // width_ms * width_ms][0] += f1 - f0
        out[t1 // width_ms * width_ms][1] += t1 - t0
    return [(start, round(frames * 1000 / ms, 1) if ms else 0.0) for start, (frames, ms) in sorted(out.items())]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("x", type=int, nargs="?")
    parser.add_argument("y", type=int, nargs="?")
    parser.add_argument("--no-tap", action="store_true")
    parser.add_argument("--seconds", type=float, default=3.5)
    parser.add_argument("--before", type=float, default=1.0)
    parser.add_argument("--again-after", type=float)
    args = parser.parse_args(argv)
    if not args.no_tap and (args.x is None or args.y is None):
        parser.error("give X Y, or --no-tap")
    taps = [] if args.no_tap else [args.before] + ([args.before + args.again_after] if args.again_after else [])
    with PanelClient() as client:
        rows, done, start = [], [], time.monotonic()
        try:
            while time.monotonic() - start < args.seconds or len(done) < len(taps):
                if len(done) < len(taps) and time.monotonic() - start >= taps[len(done)]:
                    a = time.monotonic()
                    client.tap(args.x, args.y)
                    done.append((round((a - start) * 1000), round((time.monotonic() - start) * 1000)))
                rows.append((round((time.monotonic() - start) * 1000), int(client.status()["render_frames_total"])))
        finally:
            # The second tap undoes the first: never leave it out after a failure in between.
            if len(done) == 1 and len(taps) == 2:
                client.tap(args.x, args.y)
                done.append("again after a failure")
        print(json.dumps({"page": client.status().get("page"), "taps_ms": done, "per_second": windows(rows)}))


if __name__ == "__main__":
    main()
