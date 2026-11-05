#!/usr/bin/env python3
"""The Spelas nu record's frame rate, without music: a release gate (Joel: "säkerställ" 60 fps, 2026-10-28).

    tools/panel turntable-fps [--url URL] [--seconds 5]

It puts the panel in the synthetic "turntable" test mode (Spelas nu, the record turning as if music played),
measures redraws a second and ms each from the lifetime render counters, and restores. It fails below MIN_FPS
redraws a second or above MAX_MS a redraw: one record image a display frame is 60 a second at about 6.5-7.5 ms
(measured 2026-10-28; a plinth picture under the record once made it 34 a second at 14.9 ms). Not run while voice,
night mode or settings are in use: the test mode refuses then.
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_remote import PanelClient  # noqa: E402
from render_cost import cost, totals  # noqa: E402

MIN_FPS, MAX_MS = 55.0, 12.0
IDLE = ("page_glass_v2", "page_hemmet", "page_tavla")


def verdict(result):
    failures = []
    if result["redraws_per_s"] < MIN_FPS:
        failures.append(f"{result['redraws_per_s']} redraws/s is below {MIN_FPS}")
    if result["ms_each"] > MAX_MS:
        failures.append(f"{result['ms_each']} ms a redraw is above {MAX_MS}")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default="http://hallpanel.local:8080")
    parser.add_argument("--seconds", type=float, default=5.0)
    args = parser.parse_args(argv)
    with PanelClient(args.url, persistent=True) as client, client.exclusive():
        page = client.status()["page"]
        if page not in IDLE:
            print(json.dumps({"ok": False, "error": f"start from an idle tab page, not {page}"}))
            return 2
        try:
            client.test_mode("turntable", duration_ms=int((args.seconds + 5) * 1000))
            time.sleep(1.5)                       # the page is in and the record has settled
            shown = client.status()["page"]
            before = totals(client)
            time.sleep(args.seconds)
            after = totals(client)
        finally:
            client.test_mode("restore", 100)
    result = {"page": shown, **cost(before, after)}
    failures = verdict(result) + ([] if shown == "page_musik" else [f"the test showed {shown}, not page_musik"])
    print(json.dumps({"ok": not failures, **result, "failures": failures}, ensure_ascii=False))
    return 0 if not failures else 1


if __name__ == "__main__":
    sys.exit(main())
