#!/usr/bin/env python3
"""Reproducible, silent device UI benchmark. No model calls or settings changes."""
import argparse
from datetime import datetime, timezone
import hashlib
import http.client
import json
import math
from pathlib import Path
import statistics
import sys
import time

from panel_remote import PanelClient, ROOT
from panel_metrics import clean_status, percentile, private_write, restarted

POLICY = ROOT / 'tests/performance/responsiveness.json'
BASELINE = ROOT / '.panel-ci/baselines/responsiveness.json'
SERIES = ('refresh_work', 'refresh_interval', 'remote_press', 'remote_release', 'physical_press', 'physical_release')
# Optional diagnostics; older firmware lacks some. empty_refresh_work is never gated.
PHASE_SERIES = ('layout_sync', 'render_work', 'flush_wait', 'flush_submit', 'vsync_latency', 'empty_refresh_work')
PROTOCOL = 'tabs-20-orb-10s-v2'


def telemetry(raw):
    value = raw.get('responsiveness')
    if not isinstance(value, dict) or value.get('schema') != 1:
        raise RuntimeError('Firmware lacks responsiveness telemetry; install the checked instrumentation build')
    result = {}
    for key in (*SERIES, *(key for key in PHASE_SERIES if key in value)):
        item = value.get(key, {})
        total, values = item.get('total'), item.get('us')
        if type(total) is not int or total < 0 or not isinstance(values, list) or \
                len(values) != min(total, 128) or any(type(v) is not int or not 0 <= v < 2**32 for v in values):
            raise ValueError('Invalid responsiveness series: ' + key)
        result[key] = {'total': total, 'us': values}
    return result


def presentation(raw):
    """Frame-boundary and presentation counters from the display interrupt; None on older firmware."""
    value = raw.get('presentation')
    if value is None:
        return None
    interval = value.get('presented_interval') if isinstance(value, dict) else None
    if (not isinstance(value, dict) or value.get('schema') != 1 or not isinstance(interval, dict) or
            any(type(value.get(key)) is not int or not 0 <= value[key] < 2**32
                for key in ('vsync_total', 'presented_total', 'vsync_age_us')) or
            type(interval.get('total')) is not int or not 0 <= interval['total'] < 2**32 or
            not isinstance(interval.get('us'), list) or len(interval['us']) > min(interval['total'], 128) or
            any(type(v) is not int or not 0 <= v < 2**32 for v in interval['us']) or
            value['presented_total'] > value['vsync_total']):
        raise ValueError('Invalid presentation telemetry')
    return {'vsync_total': value['vsync_total'], 'presented_total': value['presented_total'],
            'vsync_age_us': value['vsync_age_us'], 'total': interval['total'], 'us': list(interval['us'])}


def presentation_delta(before, after):
    """Fresh presentation intervals (us) between two records; the interrupt ring keeps the newest 128."""
    a, b = before['presentation'], after['presentation']
    count = b['total'] - a['total']
    if restarted(before['status'], after['status']) or count < 0 or b['vsync_total'] < a['vsync_total'] or \
            b['presented_total'] < a['presented_total']:
        raise RuntimeError('Panel restarted or presentation counters reset')
    if count > len(b['us']):
        raise RuntimeError('Presentation samples overwritten; decrease polling interval')
    return b['us'][-count:] if count else []


def delta(before, after, key):
    a, b = before['timing'][key], after['timing'][key]
    count = b['total'] - a['total']
    if restarted(before['status'], after['status']) or count < 0:
        raise RuntimeError('Panel restarted or responsiveness counters reset')
    if count > len(b['us']):
        raise RuntimeError('Responsiveness samples overwritten; decrease polling interval')
    return b['us'][-count:] if count else []


def stats(values):
    if not values:
        raise ValueError('No fresh timing samples')
    return {'n': len(values), 'min': min(values), 'median': statistics.median(values),
            'p95': percentile(values, .95), 'p99': percentile(values, .99), 'max': max(values)}


def prefetch_summary(records):
    fields = ('orb_prefetch_hits', 'orb_prefetch_fallbacks', 'orb_prefetch_decoded',
              'orb_prefetch_errors', 'orb_prefetch_bytes')
    if not any(any(key in r['status'] for key in fields) for r in records):
        return {'supported': False}
    previous = None
    for entry in records:
        status = entry['status']
        if any(type(status.get(key)) is not int or not 0 <= status[key] < 2**32 for key in fields):
            raise ValueError('Incomplete or invalid orb prefetch counters')
        if previous and (status[fields[-1]] != previous[fields[-1]] or
                         any(status[key] < previous[key] for key in fields[:-1])):
            raise ValueError('Orb prefetch counters reset or buffer residency changed')
        previous = status
    result = {'supported': True, 'enabled': previous[fields[-1]] > 0,
              'resident_bytes': previous[fields[-1]], 'lifetime_errors': previous['orb_prefetch_errors']}
    animation = [r['status'] for r in records if r['stage'] == 'animation']
    if len(animation) >= 2:
        for key in fields[:-1]:
            result[key.removeprefix('orb_prefetch_')] = animation[-1][key] - animation[0][key]
        total = result['hits'] + result['fallbacks']
        result['hit_percent'] = 100 * result['hits'] / total if total else None
        if all('orb_source_changes' in r for r in animation):
            changes = [r['orb_source_changes'] for r in animation]
            if any(type(v) is not int or v < 0 for v in changes) or any(b < a for a, b in zip(changes, changes[1:])):
                raise ValueError('Orb source-change counter invalid or reset')
            result['source_changes'] = changes[-1] - changes[0]
            seconds = (animation[-1]['uptime_ms'] - animation[0]['uptime_ms']) / 1000
            if seconds <= 0:
                raise ValueError('Orb source-change observation interval invalid')
            result['source_changes_per_s'] = result['source_changes'] / seconds
    return result


def summarize(records, policy):
    result = {}
    for stage in ('navigation', 'animation'):
        entries = [r for r in records if r['stage'] == stage]
        if len(entries) < 2:
            continue
        for key in (*SERIES, *(key for key in PHASE_SERIES if all(key in r['timing'] for r in entries))):
            values = [v / 1000 for a, b in zip(entries, entries[1:]) for v in delta(a, b, key)]
            if key == 'refresh_interval':
                values = values[1:]  # First interval crosses the pre-workload idle boundary.
            if values:
                result[stage + '.' + key + '_ms'] = stats(values)
                if stage == 'animation' and key == 'refresh_interval':
                    budget = policy['frame_budget_ms']
                    result['animation.jank_percent'] = stats([100 * sum(v > budget for v in values) / len(values)])
                    result['animation.fps'] = stats([1000 / statistics.mean(values)])
            if stage == 'navigation' and key == 'remote_release':
                # Per target page: which page change got slower (docs/responsiveness.md, 2026-10-29 → 30).
                pages = {}
                for a, b in zip(entries, entries[1:]):
                    pages.setdefault(b['status']['page'], []).extend(v / 1000 for v in delta(a, b, key))
                for page, page_values in sorted(pages.items()):
                    if page_values:
                        result[f'navigation.remote_release_to_{page}_ms'] = stats(page_values)
        if stage == 'animation' and all(r.get('presentation') for r in entries):
            # What the display showed: boundaries without a presentation repeated an image.
            intervals = [v / 1000 for a, b in zip(entries, entries[1:]) for v in presentation_delta(a, b)]
            first, last = entries[0]['presentation'], entries[-1]['presentation']
            boundaries = last['vsync_total'] - first['vsync_total']
            presented = last['presented_total'] - first['presented_total']
            seconds = (entries[-1]['status']['uptime_ms'] - entries[0]['status']['uptime_ms']) / 1000
            if intervals and boundaries and seconds > 0:
                result['animation.presented_interval_ms'] = stats(intervals)
                # One late presentation is one stall; its length says how many frames repeated.
                result['animation.late_presentations'] = stats([sum(v > 24 for v in intervals)])
                result['animation.repeated_frames'] = stats([boundaries - presented])
                result['animation.repeated_percent'] = stats([100 * (boundaries - presented) / boundaries])
                result['animation.vsync_per_s'] = stats([boundaries / seconds])
                result['animation.presented_per_s'] = stats([presented / seconds])
    return result


def evaluate(report, baseline=None, policy=None):
    policy = json.loads(POLICY.read_text()) if policy is None else policy
    failures, comparisons = [], []
    if report.get('schema') != 1 or not report.get('complete') or report.get('protocol') != PROTOCOL:
        failures.append('Incomplete or incompatible responsiveness measurement')
    summary = report.get('summary', {})
    for field, minimum in policy['minimum_samples'].items():
        if summary.get(field, {}).get('n', 0) < minimum:
            failures.append('Insufficient fresh samples: ' + field)
    for field, rules in policy['limits'].items():
        for statistic, limit in rules.items():
            value = summary.get(field, {}).get(statistic)
            if type(value) not in (int, float) or not math.isfinite(value) or value > limit:
                failures.append(f'{field}.{statistic} exceeds {limit} or is unavailable')
    fps = summary.get('animation.fps', {}).get('median', 0)
    if fps < policy['minimum_fps']:
        failures.append('Animation below minimum redraw rate')
    for entry in report.get('records', []):
        status = entry['status']
        if 'pixel_dma_errors' in status and (type(status['pixel_dma_errors']) is not int or status['pixel_dma_errors'] != 0):
            failures.append('Pixel DMA copy error reported')
        if 'pixel_ppa_errors' in status and (type(status['pixel_ppa_errors']) is not int or status['pixel_ppa_errors'] != 0):
            failures.append('Pixel PPA copy error reported')
        for field, floor in (('internal_free', 98304), ('psram_free', 4194304)):
            if status.get(field, 0) < floor:
                failures.append(field + ' below safety floor')
                break
    try:
        prefetch = prefetch_summary(report.get('records', []))
        if prefetch.get('lifetime_errors', 0):
            failures.append('Orb prefetch reported a decode error')
    except ValueError as error:
        failures.append(str(error))
    if baseline is not None:
        if not baseline.get('complete') or baseline.get('protocol') != report.get('protocol') or \
                baseline.get('policy_sha256') != report.get('policy_sha256'):
            failures.append('Baseline protocol/policy differs; preserve it and review a new reference')
        for field, rule in policy['regressions'].items():
            statistic = rule['statistic']
            a = baseline.get('summary', {}).get(field, {}).get(statistic)
            b = summary.get(field, {}).get(statistic)
            if type(a) not in (float, int) or type(b) not in (float, int):
                failures.append('Comparable baseline metric unavailable: ' + field)
                continue
            margin = max(rule['absolute'], abs(a) * rule['relative'])
            loss = a - b if rule.get('direction') == 'lower' else b - a
            comparisons.append({'metric': field, 'statistic': statistic, 'baseline': a, 'current': b,
                                'allowed_change': margin, 'ok': loss <= margin})
            if loss > margin:
                failures.append(field + ' regressed against measured baseline')
    return {'ok': not failures, 'failures': sorted(set(failures)), 'comparisons': comparisons,
            'baseline_checked': baseline is not None}


HOST_LATENCY_FIELDS = ('navigation.command_roundtrip_ms', 'status_roundtrip_ms')


def host_latency_hint(failures):
    """Host round-trips measure the Mac's link too; name the cheap check before blaming the panel."""
    if any(f.startswith(HOST_LATENCY_FIELDS) for f in failures):
        return ('Host latency failed: run tools/panel host-link first (a Mac on the panel\'s 2.4 GHz '
                'channel measures the network, not the panel)')
    return None


def guard(raw, original, mode='idle'):
    if raw.get('boot_id') != original.get('boot_id') or raw.get('build_id') != original.get('build_id'):
        raise RuntimeError('Firmware or boot changed during benchmark')
    for key in ('voice_active', 'alarm_active', 'announcement_active', 'physical_touch_active'):
        if raw.get(key) is not False:
            raise RuntimeError('Benchmark preempted or activity unknown: ' + key)
    if raw.get('test_mode') != mode or raw.get('wifi_connected') is not True:
        raise RuntimeError('Benchmark workload interrupted or Wi-Fi disconnected')
    # A real finger between two status samples also invalidates the run.
    if raw['uptime_ms'] - raw['physical_touch_idle_ms'] > original['uptime_ms']:
        raise RuntimeError('Physical touch preempted benchmark')


def load_baseline(path=BASELINE):
    path = Path(path)
    if not path.is_file():
        raise ValueError('Measured responsiveness baseline missing; collect and review an explicit first reference')
    baseline = json.loads(path.read_text())
    if baseline.get('policy_sha256') != hashlib.sha256(POLICY.read_bytes()).hexdigest() or not evaluate(baseline)['ok']:
        raise ValueError('Responsiveness baseline is incomplete or incompatible with the current protocol/policy')
    return baseline


def collect(client, folder, policy=None):
    from panel_ui_test import select_tab
    policy_bytes = POLICY.read_bytes()
    policy = json.loads(policy_bytes) if policy is None else policy
    folder = Path(folder).expanduser().absolute()
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    folder.chmod(0o700)
    report = {'schema': 1, 'protocol': PROTOCOL, 'captured_at': datetime.now(timezone.utc).isoformat(),
              'policy_sha256': hashlib.sha256(policy_bytes).hexdigest(), 'complete': False,
              'records': [], 'commands': [], 'summary': {},
              'notes': {'device': 'Input sampling -> actual LVGL redraw completion/submission; excludes optical scanout.',
                        'remote': 'Remote input bypasses GT911 scan/I2C; physical and remote series remain separate.',
                        'command': 'HTTP roundtrip includes the deliberate 60 ms press; excludes client settle sleeps.',
                        'percentiles': 'Nearest rank. p99 with fewer than 100 samples equals the observed maximum.',
                        'animation': '10 s steady silent orb, no framebuffer captures; first interval excluded.',
                        'targets': '25 fps current workload; 60 fps/16.67 ms is an aspiration, not a measured Tesla comparison.'}}
    original, animation_started, navigated = None, False, False
    started = time.monotonic()
    try:
        original = client.status()
        guard(original, original)
        telemetry(original)
        if original['page'] not in ('page_glass_v2', 'page_hemmet', 'page_musik', 'page_tavla'):
            raise RuntimeError('Start from an idle normal tab')
        # Capture once before timing; it proves which controls are visible and avoids measurement load.
        client.inspect(folder / 'before')
        navigated = True
        select_tab(client, 'page_glass_v2')

        def read(stage, mode='idle'):
            begin = time.perf_counter_ns()
            raw = client.status()
            elapsed = (time.perf_counter_ns() - begin) / 1e6
            guard(raw, original, mode)
            item = {'stage': stage, 'status': clean_status(raw), 'timing': telemetry(raw),
                    'status_roundtrip_ms': elapsed, 'transport': dict(getattr(client, 'last_transport', {}))}
            shown = presentation(raw)
            if shown is not None:
                item['presentation'] = shown
            report['records'].append(item)
            return item

        read('navigation')
        for index in range(20):
            page, label = ('page_hemmet', 'Hemmet') if index % 2 == 0 else ('page_glass_v2', 'Gå hemifrån')
            # Fresh tree and guarded source page, never stale fixed coordinates.
            ui = client.ui()
            if ui.get('truncated') or ui.get('page') != ('page_glass_v2' if index % 2 == 0 else 'page_hemmet'):
                raise RuntimeError('Navigation source page/tree changed')
            labels = [w for w in ui['widgets'] if w.get('text') == label]
            if len(labels) != 1:
                raise RuntimeError('Navigation target ambiguous')
            w = labels[0]
            previous = report['records'][-1]
            begin = time.perf_counter_ns()
            client.tap(w['x'] + w['width'] // 2, w['y'] + w['height'] // 2, duration=60, settle=0)
            command_ms = (time.perf_counter_ns() - begin) / 1e6
            command_transport = dict(getattr(client, 'last_transport', {}))
            deadline = time.monotonic() + 2
            while True:
                current = read('navigation')
                release_count = len(delta(previous, current, 'remote_release'))
                if current['status']['page'] == page and release_count == 1:
                    break
                if release_count > 1 or time.monotonic() >= deadline:
                    raise RuntimeError('Navigation redraw not acknowledged exactly once')
                time.sleep(.02)
            report['commands'].append({'page': page, 'roundtrip_ms': command_ms,
                                       'observed_ms': (time.perf_counter_ns() - begin) / 1e6,
                                       'transport': command_transport})

        animation_started = True
        client.test_mode('animation_live', 30000)
        time.sleep(.3)  # Exclude entry render and its interval from steady animation.
        read('animation', 'animation_live')
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            time.sleep(.25)
            entry = read('animation', 'animation_live')
            if entry['status']['page'] != 'page_orb':
                raise RuntimeError('Animation page interrupted')
        report['summary'] = summarize(report['records'], policy)
        for name, values in (
                ('navigation.command_roundtrip_ms', [c['roundtrip_ms'] for c in report['commands']]),
                ('navigation.observed_ms', [c['observed_ms'] for c in report['commands']]),
                ('status_roundtrip_ms', [r['status_roundtrip_ms'] for r in report['records']])):
            report['summary'][name] = stats(values)
        for field in ('status_main_us', 'status_worker_us', 'status_radio_us', 'status_main_heap_us'):
            report['summary'][field] = stats([r['status'][field] for r in report['records']])
        queue = [r['status']['status_queue_us'] for r in report['records'] if 'status_queue_us' in r['status']]
        if queue:
            report['summary']['status_queue_us'] = stats(queue)
        for name, entries in (('status', report['records']), ('command', report['commands'])):
            for phase in ('request_ms', 'headers_ms', 'body_ms'):
                values = [e['transport'][phase] for e in entries if phase in e['transport']]
                if values:
                    report['summary'][name + '.transport.' + phase] = stats(values)
        report['orb_prefetch'] = prefetch_summary(report['records'])
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        try:
            if original is not None and (animation_started or navigated):
                current = client.status()
                mode = current.get('test_mode')
                if animation_started and mode not in ('idle', 'animation_live'):
                    raise RuntimeError('Animation cleanup no longer owns the active fixture')
                guard(current, original, mode if animation_started else 'idle')
                if animation_started and mode == 'animation_live':
                    client.test_mode('restore', 100)
                select_tab(client, original['page'])
                restored = client.status()
                guard(restored, original)
                if restored['page'] != original['page']:
                    raise RuntimeError('Original page restoration failed')
                report['restored'] = True
            report['complete'] = 'error_type' not in report and report.get('restored') is True
        except Exception as error:
            report['complete'] = False
            report['cleanup_error_type'] = type(error).__name__
            raise
        finally:
            report['duration_s'] = round(time.monotonic() - started, 3)
            private_write(folder / 'responsiveness.json', report)
    return report


def lowest(raw, key):
    """The lowest `key` anywhere in a raw responsiveness.json (status samples), or None."""
    found = []
    def walk(node):
        if isinstance(node, dict):
            for k, v in node.items():
                if k == key and isinstance(v, (int, float)):
                    found.append(v)
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(raw)
    return min(found) if found else None


def compare_runs(before, after):
    """Release and render-work series of two saved runs side by side, and how close each came to the memory floors
    (a gate once failed on internal RAM only, 2026-10-30); reads files only."""
    rows = {}
    for label, folder in (('before', before), ('after', after)):
        summary = json.loads((Path(folder) / 'report.json').read_text())['summary']
        for name, series in summary.items():
            if 'release' in name or 'render_work' in name:
                rows.setdefault(name, {})[label] = {k: series.get(k) for k in ('n', 'median', 'p95', 'max')}
        raw = Path(folder) / 'responsiveness.json'
        if raw.exists():
            data = json.loads(raw.read_text())
            for key in ('internal_free', 'psram_free'):
                rows.setdefault(f'lowest.{key}', {})[label] = lowest(data, key)
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--compare', nargs=2, type=Path, metavar=('BEFORE_RUN', 'AFTER_RUN'),
                        help='Print two saved runs\' release and render-work series side by side; no device access')
    parser.add_argument('--url', default='http://hallpanel.local:8080')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--baseline', type=Path, default=BASELINE)
    parser.add_argument('--check', type=Path, help='Check saved report without device access')
    parser.add_argument('--record-baseline', type=Path, help='Explicit new baseline path; never overwrites')
    parser.add_argument('--wait-ready', type=float, default=0, help='Passive boot wait after checked OTA, at most 120 seconds')
    args = parser.parse_args(argv)
    if args.compare:
        print(json.dumps(compare_runs(*args.compare), indent=1))
        return 0
    try:
        if not 0 <= args.wait_ready <= 120:
            raise ValueError('--wait-ready must be 0–120 seconds')
        # Only explicit first-reference creation may proceed without an existing reference.
        baseline = load_baseline(args.baseline) if args.baseline.exists() or not args.record_baseline else None
        if args.check:
            report = json.loads(args.check.read_text())
        else:
            if (ROOT / '.panel-ci/quarantine.json').exists():
                raise RuntimeError('Physical benchmark stopped by unresolved CI quarantine; saved reports remain readable')
            root = ROOT / '.panel-ci/responsiveness'
            if args.out is None:
                from panel_ci import prune_runs
                root.mkdir(parents=True, mode=0o700, exist_ok=True)
                prune_runs(root, keep=20, max_bytes=128 * 1024 * 1024)
            folder = args.out or root / datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')
            with PanelClient(args.url, persistent=True) as client:
                if args.wait_ready:
                    deadline = time.monotonic() + args.wait_ready
                    while True:
                        try:
                            if client.status().get('boot_healthy') is True:
                                break
                        except (OSError, http.client.HTTPException):
                            pass  # Passive reads only; never retry an input.
                        if time.monotonic() >= deadline:
                            raise RuntimeError('Panel boot readiness timed out')
                        time.sleep(1)
                with client.exclusive():
                    report = collect(client, folder)
        result = evaluate(report, baseline)
        if hint := host_latency_hint(result['failures']):
            result['hint'] = hint
        if args.record_baseline:
            if not result['ok']:
                raise ValueError('Cannot record failed responsiveness measurement')
            path = args.record_baseline.expanduser().absolute()
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            with path.open('x') as output:
                path.chmod(0o600)
                json.dump(report, output, indent=2, allow_nan=False)
        result['summary'] = report['summary']
        result['report'] = str(args.check or folder / 'responsiveness.json')
        if not args.check:
            private_write(folder / 'report.json', result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except (OSError, http.client.HTTPException, RuntimeError, ValueError, KeyError, AssertionError) as error:
        print('Responsiveness benchmark failed: ' + type(error).__name__ + ': ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
