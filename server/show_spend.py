#!/usr/bin/env python3
"""What Jarvis's brains have cost per day (Stockholm time): the answers from history.db plus the anthropic
wake-word warm-ups, which are not in history.db and are logged only in the journal (`anthropic warm-up: {...}`).
Until 2026-11-03 the warm-ups wrote the prompt cache and cost 2-11 times the answers, so a sum of history.db
alone undercounted the bill by more than half. These are the server's own estimates (PRICES in
anthropic_agent.py), not Anthropic's invoice. The journal keeps only some days; older warm-ups are missing.

On Jarvis:  ~/hallpanel-live/venv/bin/python ~/hallpanel-live/server/show_spend.py [--days 14]
"""
import argparse
import ast
import collections
import datetime
import sqlite3
import subprocess
import zoneinfo
from pathlib import Path

TZ = zoneinfo.ZoneInfo("Europe/Stockholm")
DB = Path.home() / ".local/share/hallpanel/history.db"
MARK = "anthropic warm-up: "


def parse_warm(line):
    """(Stockholm date, cost in USD, whether it wrote the cache) from a short-iso journal line, or None."""
    if MARK not in line:
        return None
    try:
        info = ast.literal_eval(line.split(MARK, 1)[1].strip())
        day = datetime.datetime.fromisoformat(line.split(" ", 1)[0]).astimezone(TZ).date()
    except (ValueError, SyntaxError):
        return None
    return day, float(info.get("cost_usd") or 0.0), bool(info.get("cache_write"))


def answers(since, db=DB):
    """{(date, brain): [answers, USD]} for Jarvis's replies since the given date."""
    out = collections.defaultdict(lambda: [0, 0.0])
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    for ts, brain, cost in con.execute("select ts, brain, cost_usd from messages where role='jarvis' and cost_usd > 0"):
        day = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(TZ).date()
        if day >= since:
            out[(day, brain)][0] += 1
            out[(day, brain)][1] += cost
    return out


def warmups(since, lines):
    """{date: [warm-ups, cache writes, USD]}."""
    out = collections.defaultdict(lambda: [0, 0, 0.0])
    for line in lines:
        got = parse_warm(line)
        if got and got[0] >= since:
            day, cost, wrote = got
            out[day][0] += 1
            out[day][1] += wrote
            out[day][2] += cost
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=14)
    a = ap.parse_args()
    since = datetime.datetime.now(TZ).date() - datetime.timedelta(days=a.days - 1)
    journal = subprocess.run(["journalctl", "_SYSTEMD_USER_UNIT=hallpanel.service", "-g", MARK.strip(), "--since",
                              since.isoformat(), "--no-pager", "-o", "short-iso"], capture_output=True, text=True).stdout
    ans, warm = answers(since), warmups(since, journal.splitlines())
    first_warm = min(warm) if warm else None
    total = 0.0
    for day in sorted({d for d, _ in ans} | set(warm)):
        parts = [f"{brain}: {n} answers ${usd:.3f}" for (d, brain), (n, usd) in sorted(ans.items()) if d == day]
        if day in warm:
            n, writes, usd = warm[day]
            parts.append(f"warm-ups: {n} ({writes} cache writes) ${usd:.3f}")
        elif first_warm is None or day < first_warm:
            parts.append("warm-ups: not in the journal")
        usd = sum(v[1] for (d, _), v in ans.items() if d == day) + (warm[day][2] if day in warm else 0.0)
        total += usd
        print(f"{day}  ${usd:6.3f}  " + "; ".join(parts))
    print(f"total ${total:.3f} (server estimates; warm-ups only for days the journal keeps)")


if __name__ == "__main__":
    main()
