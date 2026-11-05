#!/usr/bin/env python3
"""Medians of the panel's page_draw log lines (bench/page_draw.h), per target page and screen_swap area budget.

    tools/panel logs | .venv/bin/python tools/page_draw_summary.py [--page page_hemmet]

Reads stdin; repeated lines (logs fetched more than once) count once. Only reads text, never the panel.
"""
import argparse
import re
import statistics
import sys

PARTS = ('decision', 'frame wait', 'label', 'image', 'fill')


def rows(lines):
    seen, kept = set(), []
    for line in lines:
        if line not in seen:
            seen.add(line)
            kept.append(line)
    out = []
    for i, line in enumerate(kept):
        head = re.search(r'\[page_draw:\d+\]: to (\S+) ([\d.]+) ms: \[', line)
        if not head:
            continue
        row = {'page': head[1], 'total': float(head[2]), 'budget': None, 'areas': None, 'reduce': None}
        for part in PARTS:
            m = re.search(rf'\b{part} ([\d.]+)(?: ms)?', line)
            row[part] = float(m[1]) if m else 0.0
        swap = re.search(r'swap budget (\d+) -> (\d+) areas; decision: layout [\d.]+ collect [\d.]+ reduce ([\d.]+)',
                         kept[i + 1]) if i + 1 < len(kept) else None
        if swap and int(swap[2]):   # 0 areas: screen_swap did not apply, and its budget line is the last one's
            row.update(budget=int(swap[1]), areas=int(swap[2]), reduce=float(swap[3]))
        out.append(row)
    return out


def summary(found, page=None):
    groups = {}
    for row in found:
        if page and row['page'] != page:
            continue
        groups.setdefault((row['page'], row['budget']), []).append(row)
    result = []
    for (name, budget), items in sorted(groups.items(), key=lambda kv: (kv[0][0], -(kv[0][1] or 0))):
        med = {k: round(statistics.median(r[k] for r in items), 1)
               for k in ('total', *PARTS) if all(r[k] is not None for r in items)}
        if all(r['reduce'] is not None for r in items):
            med['reduce'] = round(statistics.median(r['reduce'] for r in items), 1)
        result.append({'page': name, 'budget': budget, 'n': len(items), **med})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--page')
    args = parser.parse_args()
    for row in summary(rows(sys.stdin.read().splitlines()), args.page):
        print(row)


if __name__ == '__main__':
    main()
