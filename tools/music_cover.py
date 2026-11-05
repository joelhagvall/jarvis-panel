#!/usr/bin/env python3
"""Gate: when music plays, the day page's music row shows its cover (not only its title).

2026-10-29: after a reboot the row came up with the title and no cover, and the Spelas nu record stayed black,
because a cover key that arrived before the song's text was dropped. No other gate looked at the row. This reads
the real widget tree: the row's title sits 44 px further right when its 40 px cover is shown. Read-only, no input.
`tools/panel music-cover [--url URL] [--wait S]`; exit 0 with no music or with the cover, 1 without it.
"""
import argparse
import json
from pathlib import Path
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parent))
from panel_remote import PanelClient

COVER_PX = 40


def music_row(ui):
    """(title, cover_shown) of the visible music row on the current page, or None without one."""
    widgets = {w['path']: w for w in ui.get('widgets', [])}
    for path, row in widgets.items():
        art, title = widgets.get(path + '/4'), widgets.get(path + '/0')
        if (row.get('type') == 'object' and not row.get('hidden') and art and title and title.get('type') == 'label'
                and art.get('type') == 'object' and art.get('width') == COVER_PX and art.get('height') == COVER_PX
                and widgets.get(path + '/2', {}).get('type') == 'button'):
            return title.get('text', ''), title['x'] - row['x'] >= COVER_PX
    return None


def check(client, wait_s=15.0, clock=time.monotonic, sleep=time.sleep):
    until = clock() + wait_s
    while True:
        row = music_row(client.ui())
        if row is None:
            return {'ok': True, 'music': False}
        title, cover = row
        if cover:
            return {'ok': True, 'music': True, 'title': title, 'cover': True}
        if clock() >= until:
            artwork = client.status().get('artwork', {})
            return {'ok': False, 'music': True, 'title': title, 'cover': False, 'artwork': artwork,
                    'failure': f'music row without its cover for {wait_s:.0f} s'}
        sleep(0.5)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url')
    parser.add_argument('--wait', type=float, default=15.0)
    args = parser.parse_args()
    with PanelClient(url=args.url) if args.url else PanelClient() as client:
        result = check(client, args.wait)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
