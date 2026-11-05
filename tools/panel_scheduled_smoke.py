#!/usr/bin/env python3
"""Opt-in idle-only physical smoke. Unsupported idle telemetry means no interaction."""
import argparse
from datetime import datetime
import json
from pathlib import Path
import sys

from panel_remote import PanelClient, ROOT
from panel_ui_test import run
from panel_ci import prune_runs


def idle_reason(status, seconds):
    if status.get('page') not in ('page_glass_v2', 'page_hemmet', 'page_musik', 'page_tavla'):
        return 'not an idle tab'
    if status.get('test_mode') != 'idle':
        return 'test mode busy or unknown'
    for field in ('voice_active', 'alarm_active', 'announcement_active', 'physical_touch_active'):
        if status.get(field) is not False:
            return field + ' busy or unknown'
    if status.get('boot_healthy') is not True:
        return 'boot health unknown'
    age = status.get('physical_touch_idle_ms')
    if type(age) not in (int, float) or age < seconds * 1000:
        return 'recent or unknown physical touch'
    if status.get('internal_free', 0) < 98304 or status.get('psram_free', 0) < 4194304:
        return 'memory below safety floor'
    return None


def scheduled(client, root, idle_seconds=300):
    if not 60 <= idle_seconds <= 86400:
        raise ValueError('Idle guard must be 60–86400 seconds')
    if (ROOT / '.panel-ci/quarantine.json').exists():
        return {'ok': True, 'performed': False, 'reason': 'unresolved physical CI failure'}
    reason = idle_reason(client.status(), idle_seconds)
    if reason:
        return {'ok': True, 'performed': False, 'reason': reason}
    with client.exclusive(require_device_lease=True):
        reason = idle_reason(client.status(), idle_seconds)
        if reason:
            return {'ok': True, 'performed': False, 'reason': reason}
        root.mkdir(parents=True, mode=0o700, exist_ok=True)
        prune_runs(root, keep=7, max_bytes=128 * 1024 * 1024)
        folder = root / datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')
        report = run(client, folder)
        return {'ok': report['ok'], 'performed': True, 'report': str(folder / 'report.json')}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://hallpanel.local:8080')
    parser.add_argument('--idle-seconds', type=int, default=300)
    parser.add_argument('--out', type=Path, default=ROOT / '.panel-ci/scheduled')
    args = parser.parse_args()
    try:
        with PanelClient(args.url, persistent=True) as client:
            result = scheduled(client, args.out, args.idle_seconds)
        print(json.dumps(result))
        return 0
    except (OSError, RuntimeError, ValueError, AssertionError) as error:
        print('Scheduled smoke stopped: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
