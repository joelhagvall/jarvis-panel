#!/usr/bin/env python3
"""What the screen as it is now costs to draw: redraws a second, ms each and share of one core, over a window.

    tools/panel render-cost [--seconds 5] [--label "Spelas nu"]

The status's render_avg_ms is a minute's average and does not tell an open sheet from a closed one, so this reads
the lifetime counters (render_frames_total, render_busy_total_us) before and after the window instead. Put the
panel in the state to measure first (tools/panel tap-text ...), and for Spelas nu's record make sure the music
plays: a paused record draws nothing (measured 0.0 redraws/s on 2026-10-28 and taken for a fault). It only reads.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_remote import PanelClient  # noqa: E402


def totals(client):
    status = client.status()
    return int(status["render_frames_total"]), int(status["render_busy_total_us"]), time.monotonic()


def cost(before, after):
    frames, busy, seconds = after[0] - before[0], after[1] - before[1], after[2] - before[2]
    return {"redraws_per_s": round(frames / seconds, 1) if seconds > 0 else 0.0,
            "ms_each": round(busy / 1000 / frames, 2) if frames else 0.0,
            "core_percent": round(busy / 1e4 / seconds, 1) if seconds > 0 else 0.0,
            "seconds": round(seconds, 1)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--label", default="")
    parser.add_argument("--url")
    args = parser.parse_args(argv)
    client = PanelClient(args.url) if args.url else PanelClient()
    before = totals(client)
    time.sleep(args.seconds)
    result = {"label": args.label, "page": client.status().get("page"), **cost(before, totals(client))}
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
