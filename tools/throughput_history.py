#!/usr/bin/env python3
"""The throughput gate's earlier runs side by side, to tell one noisy run from a regression (docs/responsiveness.md).

    tools/panel throughput-history [--last 14]

One line per run in .panel-ci/throughput/, newest first: the frame interval's median, p95 and max, and
status_roundtrip_ms's p95. Only reads the saved reports; no device.
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
METRICS = (("animation.refresh_interval_ms", ("median", "p95", "max")), ("status_roundtrip_ms", ("p95",)))


def row(report):
    """{'run', 'animation.refresh_interval_ms.max', ...} from one throughput.json, rounded to 0.1 ms."""
    summary = report.get("summary") or {}
    out = {}
    for metric, stats in METRICS:
        for stat in stats:
            value = (summary.get(metric) or {}).get(stat)
            out[f"{metric}.{stat}"] = round(value, 1) if isinstance(value, (int, float)) else None
    return out


def history(folder, last):
    runs = sorted((p for p in folder.glob("*/throughput.json")), key=lambda p: p.parent.name, reverse=True)[:last]
    rows = []
    for path in runs:
        try:
            rows.append({"run": path.parent.name, **row(json.loads(path.read_text()))})
        except (OSError, ValueError):
            continue
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--last", type=int, default=14)
    parser.add_argument("--dir", type=Path, default=ROOT / ".panel-ci/throughput")
    args = parser.parse_args(argv)
    rows = history(args.dir, args.last)
    names = [f"{m}.{s}" for m, stats in METRICS for s in stats]
    print("run".ljust(36) + "  ".join(n.replace("animation.refresh_interval_ms", "frame").replace(
        "status_roundtrip_ms", "status") for n in names))
    for r in rows:
        print(r["run"].ljust(36) + "  ".join(str(r[n]).rjust(len(n.replace("animation.refresh_interval_ms", "frame")
              .replace("status_roundtrip_ms", "status"))) for n in names))


if __name__ == "__main__":
    main()
