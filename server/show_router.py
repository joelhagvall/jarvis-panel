#!/usr/bin/env python3
"""What quick_router.py would have done beside what the server did, from the `quick-router:` journal lines
(shadow mode, docs/voice-latency.md). Read on Jarvis:

  python3 ~/hallpanel-live/server/show_router.py              the last 7 days: counts, then every disagreement
  python3 ~/hallpanel-live/server/show_router.py --days 30
  journalctl --user-unit hallpanel -g 'quick-router:' -o cat | python3 show_router.py -   from a pipe or file

Note `--user-unit hallpanel`: `journalctl --user -u hallpanel` prints nothing on Jarvis (2026-11-04).
"""
import re
import subprocess
import sys
from collections import Counter

LINE = re.compile(r"quick-router: net=(\S+) (-?[\d.]+) took=(\S+) (agree|disagree) text=(.*)$")


def parse(lines):
    rows = []
    for line in lines:
        if m := LINE.search(line):
            rows.append({"net": m[1], "score": float(m[2]), "took": m[3], "verdict": m[4], "text": m[5]})
    return rows


def summary(rows):
    out = [f"{len(rows)} questions, {sum(r['verdict'] == 'agree' for r in rows)} agree, "
           f"{sum(r['verdict'] == 'disagree' for r in rows)} disagree"]
    by = Counter((r["net"], r["took"].split(":")[0]) for r in rows if r["verdict"] == "disagree")
    for (net, took), n in by.most_common():
        out.append(f"  {n:3d}  net={net} took={took}")
    for r in rows:
        if r["verdict"] == "disagree":
            out.append(f"  net={r['net']} {r['score']:.2f} took={r['took']} {r['text']}")
    return "\n".join(out)


def main(argv):
    days = 7
    if "--days" in argv:
        days = int(argv[argv.index("--days") + 1])
    if "-" in argv:
        lines = sys.stdin.read().splitlines()
    else:
        lines = subprocess.run(["journalctl", "--user-unit", "hallpanel", "-g", "quick-router:", "-o", "cat",
                                "--no-pager", "--since", f"-{days}d"], capture_output=True, text=True, check=False
                               ).stdout.splitlines()
    print(summary(parse(lines)))


if __name__ == "__main__":
    main(sys.argv[1:])
