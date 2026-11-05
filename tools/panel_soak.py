#!/usr/bin/env python3
"""Summarise the passive health history into a soak report (read only; never contacts the panel).

hallpanel-health.timer on Jarvis samples the panel's /status every 5 minutes into
~/.local/state/hallpanel/health.json (docs/panel-monitor.md). This answers "how did the panel do this week":
coverage, unreachable stretches, every boot with its reset reason, memory floors and drift per boot,
Wi-Fi signal, render and touch timings, and one row per Stockholm day.

  tools/panel soak                    # history streamed from Jarvis over ssh, nothing written
  tools/panel soak --days 7 --json
  tools/panel soak --history FILE     # a local copy

The numbers are what the firmware reported; they do not measure flicker, sound or finger-to-photon latency.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
sys.path.insert(0, str(ROOT / 'server'))
from panel_metrics import restarted  # noqa: E402
from panel_health import CRASH_REASONS, FLOORS  # noqa: E402  the same crash resets and memory floors as the pushes

JARVIS = os.environ.get('JARVIS_SSH', 'user@jarvis.local')
REMOTE_HISTORY = '.local/state/hallpanel/health.json'
MAX_BYTES = 16 * 1024 * 1024  # the monitor's own safety limit
INTERVAL_S = 300
GAP_S = 15 * 60  # three missed timer runs: the monitor itself did not run
DRIFT_MIN_S = 6 * 3600
DRIFT_SAMPLES = 12
TZ = ZoneInfo('Europe/Stockholm')
ACTIVITY_FIELDS = {'render_avg_ms', 'render_max_ms', 'touch_refresh_p95_ms'}
DETAIL_BOOTS = 15
# esp_reset_reason_t
RESET_NAMES = {'0': 'unknown', '1': 'power-on', '2': 'external pin', '3': 'software', '4': 'panic',
               '5': 'interrupt watchdog', '6': 'task watchdog', '7': 'other watchdog', '8': 'deep sleep',
               '9': 'brownout', '10': 'SDIO', '11': 'USB', '12': 'JTAG', '13': 'eFuse', '14': 'power glitch',
               '15': 'CPU lockup'}


def fetch_from_jarvis(host=JARVIS):
    result = subprocess.run(['sh', str(ROOT / 'tools/jarvis_ssh.sh'), host, f'head -c {MAX_BYTES + 1} {REMOTE_HISTORY}'],
                            capture_output=True, check=True, timeout=60)
    if len(result.stdout) > MAX_BYTES:
        raise ValueError('History exceeds its 16 MiB safety limit')
    return json.loads(result.stdout)


def load(path):
    path = Path(path).expanduser()
    if path.stat().st_size > MAX_BYTES:
        raise ValueError('History exceeds its 16 MiB safety limit')
    return json.loads(path.read_text())


def _pct(values, fraction):
    values = sorted(values)
    return values[min(len(values) - 1, int(round((len(values) - 1) * fraction)))] if values else None


def _values(entries, field):
    values = [e['status'][field] for e in entries if isinstance(e['status'].get(field), (int, float))
              and not isinstance(e['status'].get(field), bool)]
    # 0 there means nothing was drawn or touched in the firmware's window, not an instant frame.
    return [v for v in values if v > 0] if field in ACTIVITY_FIELDS else values


def _min(values):
    return min(values, default=None)


def _reason(status):
    code = str(status.get('reset_reason', ''))
    return code, RESET_NAMES.get(code, code or 'unavailable'), code in CRASH_REASONS


def _day(t):
    return datetime.fromtimestamp(t, TZ).strftime('%Y-%m-%d')


def _clock(t):
    return datetime.fromtimestamp(t, TZ).strftime('%Y-%m-%d %H:%M')


def boots_of(healthy):
    """Consecutive healthy samples grouped by boot, oldest first."""
    boots = []
    for entry in healthy:
        if not boots or restarted(boots[-1][-1]['status'], entry['status']):
            boots.append([])
        boots[-1].append(entry)
    return boots


def _drift(samples, field):
    if len(samples) < 2 * DRIFT_SAMPLES or samples[-1]['time'] - samples[0]['time'] < DRIFT_MIN_S:
        return None
    first, last = _values(samples[:DRIFT_SAMPLES], field), _values(samples[-DRIFT_SAMPLES:], field)
    return statistics.median(last) - statistics.median(first) if first and last else None


def report(history, days=None, now=None):
    if not isinstance(history, dict) or not isinstance(history.get('entries'), list):
        raise ValueError('Invalid health history')
    now = time.time() if now is None else now
    entries = sorted((e for e in history['entries'] if isinstance(e.get('time'), (int, float))), key=lambda e: e['time'])
    if days is not None:
        entries = [e for e in entries if e['time'] >= now - days * 86400]
    if not entries:
        raise ValueError('No samples in the selected window')
    healthy = [e for e in entries if e.get('ok') and isinstance(e.get('status'), dict)]
    span = entries[-1]['time'] - entries[0]['time']

    # Failed reads in a row = panel unreachable; no sample at all for >15 min = the monitor did not run.
    unreachable, run = [], []
    for entry in entries + [{'ok': True}]:
        if entry.get('ok'):
            if run:
                unreachable.append({'from': run[0]['time'], 'to': run[-1]['time'], 'samples': len(run)})
            run = []
        else:
            run.append(entry)
    gaps = [{'from': a['time'], 'to': b['time'], 'minutes': round((b['time'] - a['time']) / 60)}
            for a, b in zip(entries, entries[1:]) if b['time'] - a['time'] > GAP_S]

    boots = []
    for samples in boots_of(healthy):
        first, last = samples[0]['status'], samples[-1]['status']
        code, name, crash = _reason(first)
        boots.append({
            # uptime dates the boot itself; samples are 5 minutes apart.
            'booted_at': samples[0]['time'] - first.get('uptime_ms', 0) / 1000,
            'first_seen': samples[0]['time'], 'last_seen': samples[-1]['time'], 'samples': len(samples),
            'reset_reason': code, 'reset_name': name, 'crash': crash,
            # The first boot in the window may have started before it; its reason is then not a new restart.
            'new_in_window': bool(boots) or first.get('uptime_ms', 0) < 2 * INTERVAL_S * 1000,
            'uptime_h': round(max(_values(samples, 'uptime_ms') or [0]) / 3.6e6, 1),
            'build_id': last.get('build_id'), 'firmware_version': last.get('firmware_version'),
            'internal_min_free': _min(_values(samples, 'internal_min_free')),
            'psram_min_free': _min(_values(samples, 'psram_min_free')),
            'internal_free_drift': _drift(samples, 'internal_free'),
            'psram_free_drift': _drift(samples, 'psram_free'),
        })
        boots[-1]['low_floor'] = any(boots[-1][field] is not None and boots[-1][field] < floor
                                     for field, floor in FLOORS.items())
    for previous, boot in zip(boots, boots[1:]):
        # An upper bound: the panel ran for an unknown part of the 5 minutes after its last sample.
        boot['down_minutes_max'] = max(0, round((boot['booted_at'] - previous['last_seen']) / 60))

    def stats(field):
        values = _values(healthy, field)
        return {'min': min(values), 'p05': _pct(values, .05), 'p50': _pct(values, .5), 'p95': _pct(values, .95),
                'max': max(values), 'samples': len(values)} if values else None

    per_day = {}
    boot_starts = {b['first_seen']: b for b in boots if b['new_in_window']}
    for entry in entries:
        row = per_day.setdefault(_day(entry['time']), {'samples': 0, 'failed': 0, 'boots': 0, 'crashes': 0, '_h': []})
        row['samples'] += 1
        if not (entry.get('ok') and isinstance(entry.get('status'), dict)):
            row['failed'] += 1
            continue
        row['_h'].append(entry)
        if entry['time'] in boot_starts:
            row['boots'] += 1
            row['crashes'] += boot_starts[entry['time']]['crash']
    for row in per_day.values():
        h = row.pop('_h')
        rssi, touch = _values(h, 'wifi_rssi_dbm'), _values(h, 'touch_refresh_p95_ms')
        internal, psram = _values(h, 'internal_free'), _values(h, 'psram_free')
        render_max = _values(h, 'render_max_ms')
        row.update(internal_free_min=_min(internal), psram_free_min=_min(psram), rssi_p50=_pct(rssi, .5),
                   rssi_min=_min(rssi), touch_p95_p50=_pct(touch, .5), render_max_ms=max(render_max, default=None))

    new_boots = [b for b in boots if b['new_in_window']]
    return {
        'window': {'from': entries[0]['time'], 'to': entries[-1]['time'], 'days': round(span / 86400, 2),
                   'samples': len(entries), 'expected': int(span // INTERVAL_S) + 1,
                   'coverage': round(len(entries) / (int(span // INTERVAL_S) + 1), 3)},
        'failed_reads': len(entries) - len(healthy),
        'unreachable': unreachable, 'monitor_gaps': gaps,
        'restarts': len(new_boots), 'crashes': sum(b['crash'] for b in new_boots),
        'low_floor_boots': sum(b['low_floor'] for b in boots),
        'restart_reasons': dict(sorted(Counter(b['reset_name'] for b in new_boots).items(), key=lambda kv: -kv[1])),
        'longest_uptime_h': max((b['uptime_h'] for b in boots), default=None),
        'boots': boots,
        'wifi_disconnected_samples': sum(e['status'].get('wifi_connected') is False for e in healthy),
        'metrics': {field: stats(field) for field in (
            'internal_free', 'internal_min_free', 'psram_free', 'psram_min_free', 'wifi_rssi_dbm',
            'render_avg_ms', 'render_max_ms', 'touch_refresh_p95_ms')},
        'builds': sorted({b['build_id'] for b in boots if b['build_id']}),
        'per_day': per_day,
        'active_alerts': history.get('active_alerts', []),
    }


def _num(v):
    return '-' if v is None else f'{v:.0f}' if float(v).is_integer() else f'{v:.1f}'


def _kib(v):
    return '-' if v is None else f'{v / 1024:.0f} KiB'


def _mb(v):
    return '-' if v is None else f'{v / 1e6:.1f} MB'


def _signed(v, unit):
    return '-' if v is None else (f'{v / 1024:+.0f} KiB' if unit == 'kib' else f'{v / 1e6:+.2f} MB')


def render(r):
    w = r['window']
    out = [f"Soak report {_clock(w['from'])} – {_clock(w['to'])} (Stockholm), {w['days']} days",
           f"Samples {w['samples']} of ~{w['expected']} expected ({w['coverage']:.0%}); failed reads {r['failed_reads']}",
           f"Restarts {r['restarts']}, of which crashes {r['crashes']}; longest uptime {r['longest_uptime_h']} h; "
           f"Wi-Fi disconnected in {r['wifi_disconnected_samples']} samples; boots whose memory nearly ran out "
           f"{r['low_floor_boots']} (under 32 KiB internal or 1 MiB PSRAM since start)",
           f"Active alerts: {', '.join('/'.join(filter(None, a)) for a in r['active_alerts']) or 'none'}", '']
    if r['unreachable']:
        longest = max(r['unreachable'], key=lambda u: u['samples'])
        out.append(f"Unreachable stretches {len(r['unreachable'])}; longest {longest['samples']} reads from "
                   f"{_clock(longest['from'])}")
    if r['monitor_gaps']:
        longest = max(r['monitor_gaps'], key=lambda g: g['minutes'])
        out.append(f"Monitor gaps >15 min {len(r['monitor_gaps'])}; longest {longest['minutes']} min from "
                   f"{_clock(longest['from'])} (Jarvis or the timer did not run)")
    reasons = ', '.join(f'{name} {count}' for name, count in r['restart_reasons'].items())
    out += [f"Restarts by reset reason: {reasons or 'none'}", '',
            'Crashes, low memory floors, long runs (>= 6 h) and the current boot; drift = median of the last vs first hour of the boot, '
            'which depends on what the panel showed:']
    detail = [b for i, b in enumerate(r['boots']) if (b['crash'] and b['new_in_window']) or b['low_floor']
              or b['uptime_h'] >= DRIFT_MIN_S / 3600 or i == len(r['boots']) - 1]
    if len(detail) > DETAIL_BOOTS:
        out.append(f'  ({len(detail) - DETAIL_BOOTS} older ones left out; --json has every boot)')
    for b in detail[-DETAIL_BOOTS:]:
        mark = ('CRASH ' if b['crash'] and b['new_in_window'] else '') + ('LOW MEMORY ' if b['low_floor'] else '')
        down = f", offline up to {b['down_minutes_max']} min before" if b.get('down_minutes_max', 0) >= 10 else ''
        start = '' if b['new_in_window'] else ' (began before the window)'
        out.append(f"  {_clock(b['booted_at'])}  {mark}{b['reset_name']}{start}{down}; up {b['uptime_h']} h; "
                   f"build {b['build_id'] or '-'}; internal min {_kib(b['internal_min_free'])}, "
                   f"PSRAM min {_mb(b['psram_min_free'])}; drift internal {_signed(b['internal_free_drift'], 'kib')}, "
                   f"PSRAM {_signed(b['psram_free_drift'], 'mb')}")
    m = r['metrics']
    out.append('')
    for field, label, fmt in (('internal_free', 'Internal heap free', _kib), ('psram_free', 'PSRAM free', _mb),
                              ('wifi_rssi_dbm', 'Wi-Fi RSSI dBm', _num), ('render_avg_ms', 'Render avg ms', _num),
                              ('render_max_ms', 'Render max ms', _num), ('touch_refresh_p95_ms', 'Touch p95 ms', _num)):
        s = m[field]
        out.append(f"{label:20} " + ('not reported' if s is None else
                                    '  '.join(f"{k} {fmt(s[k])}" for k in ('min', 'p05', 'p50', 'p95', 'max'))
                                    + f"  ({s['samples']} samples)"))
    out += ['', f"{'day':10}  {'samples':>7} {'failed':>6} {'boots':>5} {'crash':>5} {'int min':>9} "
                f"{'PSRAM min':>9} {'RSSI p50':>8} {'RSSI min':>8} {'touch p95':>9} {'render max':>10}"]
    for day, d in sorted(r['per_day'].items()):
        out.append(f"{day:10}  {d['samples']:>7} {d['failed']:>6} {d['boots']:>5} {d['crashes']:>5} "
                   f"{_kib(d['internal_free_min']):>9} {_mb(d['psram_free_min']):>9} "
                   f"{_num(d['rssi_p50']):>8} {_num(d['rssi_min']):>8} {_num(d['touch_p95_p50']):>9} "
                   f"{_num(d['render_max_ms']):>10}")
    current = r['boots'][-1] if r['boots'] else {}
    out.append(f"\nBuilds seen {len(r['builds'])}; current {current.get('build_id') or '-'}")
    return '\n'.join(out)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--history', type=Path, help='Local history file; default streams it from Jarvis')
    parser.add_argument('--days', type=float, help='Only the last N days')
    parser.add_argument('--json', action='store_true')
    args = parser.parse_args(argv)
    try:
        history = load(args.history) if args.history else fetch_from_jarvis()
        result = report(history, args.days)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        print(f'Soak report failed: {type(error).__name__}: {error}', file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2) if args.json else render(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
