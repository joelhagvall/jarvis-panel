#!/usr/bin/env python3
"""How fast a Spotify skip shows its new cover on Spelas nu, by the panel's own log clock (docs/now-playing.md).

    .venv/bin/python tools/cover_change.py --out NEW_DIR [--sequence next,next,previous,previous,next] [--take-over]
    .venv/bin/python tools/cover_change.py --out NEW_DIR --sequence next,previous,next,previous --wait 2
    .venv/bin/python tools/cover_change.py --out NEW_DIR --flow [--runs 4]     # vinyl-flow's next phase instead

Plays Spotify on Jarvis's Soloist only if it is the active device, or with --take-over (makes it the active
device: Joel's phone then stops). Opens Spelas nu and presses the page's own next/previous buttons, one per step,
then reads the panel log: `music: <action> sent` (the press), `music: now ...` (Home Assistant's new track) and the
record's `turns ...` line. Prints one JSON line per step with both delays in ms, then pauses again if it started
the music and goes back to Gå hemifrån. --flow runs `tools/panel vinyl-flow --next-only --log` per run instead
(frame pacing too, but host-polled timing). Audible; costs no model calls.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
PANEL = str(ROOT / 'tools/panel')
SOLOIST = '$HOME/.local/share/soloist/bin/soloist ctl --ws 127.0.0.1:9431'
JARVIS = 'user@jarvis.local'
ICONS = {'next': '\U000F04AD', 'previous': '\U000F04AE'}
LINE = re.compile(r'^(\d+) \S+ \[\w\]\[[^\]]+\](?:\[[^\]]+\])?: (.*)$')


def jarvis(command):
    return subprocess.run(['sh', str(ROOT / 'tools/jarvis_ssh.sh'), JARVIS, command], capture_output=True, text=True,
                          timeout=60).stdout


def soloist_state():
    try:
        now = json.loads(jarvis(f'{SOLOIST} now --json') or '{}')
    except ValueError:
        return {}
    return {'status': now.get('status'), 'active': now.get('is_active') is True}


def plan(state, take_over):
    """What to do first: 'play' (and pause after), 'none' (already playing here), or None (do not touch)."""
    if state.get('active') and state.get('status') == 'playing':
        return 'none'
    if state.get('active') or take_over:
        return 'play'
    return None


def button(ui, action):
    """The centre of the enabled next/previous button on Spelas nu, from the widget tree."""
    paths = {w['path']: w for w in ui.get('widgets', [])}
    for widget in paths.values():
        if widget.get('text') == ICONS[action]:
            parent = paths.get(widget['path'].rsplit('/', 1)[0], {})
            if parent.get('type') == 'button' and not parent.get('disabled'):
                return parent['x'] + parent['width'] // 2, parent['y'] + parent['height'] // 2
    return None


def delays(lines, action, after_ms=0):
    """{press_ms, now_ms, turns_ms, how} for the first `<action> sent` after after_ms, from panel log lines."""
    events = [(int(m.group(1)), m.group(2)) for m in map(LINE.match, lines) if m]
    sent = next((t for t, text in events if t > after_ms and text == f'{action} sent'), None)
    if sent is None:
        return {}
    now = next((t for t, text in events if t >= sent and text.startswith('now playing')), None)
    turns = next(((t, text) for t, text in events if t >= sent and 'cover turns' in text), None)
    return {'press_ms': sent, 'now_ms': now - sent if now else None,
            'turns_ms': turns[0] - sent if turns else None, 'how': turns[1].split(': ', 1)[-1] if turns else None}


def panel_json(*args):
    out = subprocess.run([PANEL, *args], capture_output=True, text=True).stdout
    try:
        return json.loads(out)
    except ValueError:
        return {}


def timeline(report):
    try:
        rows = json.loads(report.read_text()).get('rows', [])
    except (OSError, ValueError):
        rows = []
    return summarize(panel_json('vinyl-flow', '--explain', str(report)), rows)


def slowest(rows):
    """The slowest render the next phase saw and what it redrew (`responsiveness.slowest`, 2026-10-31)."""
    worst = None
    for row in rows:
        if row.get('stage') != 'next':
            continue
        s = ((row.get('status') or {}).get('responsiveness') or {}).get('slowest')
        if s and (worst is None or s.get('render_us', 0) > worst.get('render_us', 0)):
            worst = s
    return worst


def summarize(explained, rows=()):
    """One before/after line: timing, and the frame pacing and PSRAM of the next phase (2026-10-31: a cover fade
    passed every gate yet doubled the change time and repeated 2-6 frames; only these numbers showed it).
    `cover_changed` false: the skip kept the same picture (inside one album), so it measured no cover change."""
    nxt = (explained.get('stages') or {}).get('next') or {}
    timing = explained.get('next_timeline') or {}
    worst = slowest(rows)
    return {**timing, 'cover_changed': timing.get('new_record_s') is not None if timing else None,
            'album_changed': explained.get('album_changed'),
            'handoff_failures': (explained.get('handoff_verdict_v1') or {}).get('failures'),
            'repeated': nxt.get('repeated'), 'record_max_ms': (nxt.get('record_ms') or {}).get('max'),
            'psram_min': nxt.get('psram_min'),
            'slowest_ms': round(worst['render_us'] / 1000, 1) if worst else None,
            'slowest_rects': worst.get('rects') if worst else None,
            'uptime_s': uptime_s(rows)}


def uptime_s(rows):
    """The panel's uptime when the run's next phase began. 2026-10-31: every run covering uptime ~301-310 s right
    after a flash and its gates held the record 66-116 ms (and status 1.7 s) 5-7 s after the skip; runs later than
    that never did, and a plain restart showed nothing at 300 s. Measure from ~6 min after a flash."""
    for row in rows:
        if row.get('stage') == 'next' and (row.get('status') or {}).get('uptime_ms'):
            return round(row['status']['uptime_ms'] / 1000)
    return None


def run_sequence(steps, out, wait=7.0):
    # Only presses after this run starts: the log ring still holds earlier runs' lines.
    before = subprocess.run([PANEL, 'logs'], capture_output=True, text=True).stdout.splitlines()
    last = max((int(m.group(1)) for m in map(LINE.match, before) if m and m.group(2).endswith(' sent')), default=0)
    for number, action in enumerate(steps, 1):
        ui = panel_json('ui')
        (out / f'step-{number}-ui.json').write_text(json.dumps(ui))
        at = button(ui, action)
        if not at:
            print(json.dumps({'step': number, 'action': action, 'error': 'no enabled button'}))
            continue
        subprocess.run([PANEL, 'tap', str(at[0]), str(at[1])], capture_output=True)
        time.sleep(wait)   # the new track's next cover is fetched and built ahead meanwhile
        lines = subprocess.run([PANEL, 'logs'], capture_output=True, text=True).stdout.splitlines()
        (out / f'step-{number}.log').write_text('\n'.join(lines))
        found = delays(lines, action, last)
        last = found.get('press_ms') or last
        print(json.dumps({'step': number, 'action': action, **found}))


def run_flow(runs, out):
    for run in range(1, runs + 1):
        folder = out / f'run-{run}'
        subprocess.run([PANEL, 'vinyl-flow', '--next-only', '--log', '--out', str(folder)], capture_output=True)
        report = folder / 'report.json'
        line = timeline(report) if report.exists() else {'error': 'no report'}
        print(json.dumps({'run': run, **line}))
        if (line.get('uptime_s') or 999) < 330:
            print('cover_change: the panel started under 330 s ago; a one-off hold ~305 s after a flash can land in '
                  'this run (docs/now-playing.md)', file=sys.stderr)
        if line.get('cover_changed') is False:
            print('cover_change: this skip kept the same cover (one album?); play music with a cover per track, '
                  'e.g. liked songs shuffled', file=sys.stderr)
        time.sleep(8)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--sequence', default='next,next,previous,previous,next')
    parser.add_argument('--flow', action='store_true', help='vinyl-flow next phases instead of button presses')
    parser.add_argument('--runs', type=int, default=4)
    # Spotify's previous restarts the song after ~3 s of it; under that it goes to the previous track.
    parser.add_argument('--wait', type=float, default=7.0, help='seconds after each press (2 to reach the previous track)')
    parser.add_argument('--take-over', action='store_true', help="play on Jarvis even if another device has Spotify")
    args = parser.parse_args()
    steps = [s.strip() for s in args.sequence.split(',') if s.strip()]
    if any(s not in ICONS for s in steps):
        sys.exit('--sequence takes next and previous')
    args.out.mkdir(parents=True)
    first = plan(soloist_state(), args.take_over)
    if first is None:
        sys.exit('Spotify plays on another device (Joel\'s phone?); nothing touched. --take-over to play here.')
    if first == 'play':
        jarvis(f'{SOLOIST} activate; sleep 2; {SOLOIST} play')
        time.sleep(6)
    try:
        subprocess.run([PANEL, 'tap-text', 'Spelas nu'], capture_output=True)
        time.sleep(4)
        run_flow(args.runs, args.out) if args.flow else run_sequence(steps, args.out, args.wait)
    finally:
        if first == 'play':
            jarvis(f'{SOLOIST} pause')
        subprocess.run([PANEL, 'tap', '700', '300'], capture_output=True)   # Spelas nu back to the tab page


if __name__ == '__main__':
    main()
