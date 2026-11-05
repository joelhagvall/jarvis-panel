#!/usr/bin/env python3
"""Bounded production 60 fps measurement, retained samples and explicit comparisons."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
import math
from pathlib import Path
import time

from panel_remote import PanelClient, ROOT
from panel_metrics import clean_status, private_write
from panel_responsiveness import guard, telemetry, summarize, prefetch_summary, presentation, stats
from panel_ui_test import select_tab

BASELINE = ROOT / '.panel-ci/baselines/throughput.json'
PROTOCOL = 'orb-60-virtual-10s-v1'
# Presentation limits come from the display interrupt: a frame boundary without a
# presentation repeated an image. The completion-interval limits stay as they were.
POLICY = {
    'minimum': {'animation.refresh_interval_ms.n': 400, 'animation.fps.median': 58,
                'orb_prefetch.source_changes_per_s': 58, 'animation.presented_interval_ms.n': 400},
    'maximum': {'animation.refresh_interval_ms.p95': 20, 'animation.refresh_interval_ms.max': 33.4,
                'animation.jank_percent.max': 1, 'orb_prefetch.lifetime_errors': 0,
                'animation.repeated_percent.max': .5, 'animation.presented_interval_ms.p95': 17},
    'regressions': {
        'animation.render_work_ms.p95': {'relative': .15, 'absolute': 1},
        'animation.refresh_work_ms.p95': {'relative': .15, 'absolute': 1},
        'animation.refresh_interval_ms.p95': {'relative': .1, 'absolute': 1},
        'animation.vsync_latency_ms.p95': {'relative': .5, 'absolute': 1},
        'orb_prefetch.source_changes_per_s': {'relative': .01, 'absolute': .5, 'lower': True},
        'animation.jank_percent.max': {'relative': 0, 'absolute': .5},
        'status_roundtrip_ms.p95': {'relative': .25, 'absolute': 50}}}


# The soak runs the same production orb for SOAK_SECONDS, long enough that any stall
# recurring every 60 s (a poller on the UI task, for example) appears at least twice.
# One random hiccup is tolerated; two late presentations fail. CI then measures once
# more: a periodic stall reproduces, a single random hiccup does not.
SOAK_SECONDS = 120
SOAK_POLICY = {
    'minimum': {'animation.presented_interval_ms.n': 7000, 'animation.fps.median': 58,
                'orb_prefetch.source_changes_per_s': 58},
    'maximum': {'animation.late_presentations.max': 1, 'animation.repeated_frames.max': 3,
                'animation.vsync_latency_ms.p99': 4, 'orb_prefetch.lifetime_errors': 0,
                'orb_prefetch.fallbacks': 0}}


def policy_digest():
    return hashlib.sha256(json.dumps(POLICY, sort_keys=True).encode()).hexdigest()


def metric(report, name):
    field, statistic = name.rsplit('.', 1)
    return (report.get('orb_prefetch', {}).get(statistic) if field == 'orb_prefetch' else
            report.get('summary', {}).get(field, {}).get(statistic))


def target_failures(report, policy=POLICY):
    failures = []
    for direction, rules in (('minimum', policy['minimum']), ('maximum', policy['maximum'])):
        for name, limit in rules.items():
            value = metric(report, name)
            if type(value) not in (int, float) or not math.isfinite(value) or \
                    (value < limit if direction == 'minimum' else value > limit):
                failures.append(name + ' violates ' + direction + ' ' + str(limit))
    records = report.get('records', [])
    if not records:
        failures.append('Raw samples missing')
    if any(type(r.get('status', {}).get(key)) is not int or r['status'][key] != 0
           for r in records for key in ('pixel_dma_errors', 'pixel_ppa_errors')):
        failures.append('Pixel copy errors or missing counters')
    return failures


def evaluate_soak(report):
    """A soak is a diagnostic gate on stability: never a reference, never compared to one."""
    failures = target_failures(report, SOAK_POLICY)
    if (report.get('schema') != 1 or report.get('protocol') != SOAK_PROTOCOL or
            report.get('method') != 'production' or report.get('complete') is not True or
            report.get('restored') is not True or type(report.get('seconds')) is not int or
            report['seconds'] < SOAK_SECONDS):
        failures.append('Incomplete or incompatible production soak')
    return {'ok': not failures, 'failures': failures, 'seconds': report.get('seconds')}


def evaluate(report, baseline=None):
    failures, comparisons = target_failures(report), []
    if (report.get('schema') != 1 or report.get('protocol') != PROTOCOL or
            report.get('method') != 'production' or report.get('complete') is not True or
            report.get('restored') is not True):
        failures.append('Incomplete or incompatible production measurement')
    if report.get('policy_sha256', policy_digest()) != policy_digest():
        failures.append('Measurement policy differs')
    if baseline is not None:
        if baseline.get('protocol') != report.get('protocol') or baseline.get('policy_sha256') != policy_digest():
            failures.append('Baseline protocol/policy differs; preserve the reference')
        for name, rule in POLICY['regressions'].items():
            a, b = metric(baseline, name), metric(report, name)
            if any(type(v) not in (int, float) or not math.isfinite(v) for v in (a, b)):
                failures.append('Comparable metric unavailable: ' + name)
                continue
            margin = max(rule['absolute'], abs(a) * rule['relative'])
            loss = a - b if rule.get('lower') else b - a
            comparisons.append({'metric': name, 'baseline': a, 'current': b,
                                'allowed_change': margin, 'ok': loss <= margin})
            if loss > margin:
                failures.append(name + ' regressed against measured baseline')
    return {'ok': not failures, 'failures': failures, 'comparisons': comparisons,
            'baseline_checked': baseline is not None}


def load_baseline(path=BASELINE):
    value = json.loads(Path(path).read_text())
    if value.get('policy_sha256') != policy_digest() or not evaluate(value)['ok']:
        raise ValueError('Missing, failed or incompatible measured 60fps baseline')
    return value


SOAK_PROTOCOL = 'orb-60-virtual-soak-v1'


def collect(client, folder, method='production', seconds=10):
    """Ten seconds is the gated measurement. A longer soak re-arms the bounded fixture
    every 20 s (the firmware keeps its running frames) and can never become a reference."""
    if method not in ('production', 'linear', 'staged'):
        raise ValueError('Unsupported interpolation diagnostic method')
    if type(seconds) is not int or not 10 <= seconds <= 900:
        raise ValueError('Measure 10 (gate) to 900 seconds')
    mode = 'animation_60' + ('' if method == 'production' else '_' + method)
    folder = Path(folder).expanduser().absolute()
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    report = {'schema': 1, 'protocol': 'orb-60-stress-10s-v1', 'complete': False, 'records': [],
              'captured_at': datetime.now(timezone.utc).isoformat(),
              'limitations': 'Same 64 source frames played faster; proves update throughput only. No optical latency claim.'}
    original, active = None, False
    try:
        original = client.status()
        guard(original, original)
        if original.get('orb_virtual_frames', 0):
            report['protocol'] = PROTOCOL
            report['limitations'] = 'Virtual intermediate frames at original production duration. No optical latency claim.'
        if method != 'production':
            report['protocol'] += '-' + method
        if seconds != 10:
            report['protocol'] = SOAK_PROTOCOL + ('' if method == 'production' else '-' + method)
            report['seconds'] = seconds
        report['method'] = method
        report['policy_sha256'] = policy_digest()
        client.inspect(folder / 'before')
        active = True
        client.test_mode(mode, 30000)
        time.sleep(.5)
        deadline = time.monotonic() + seconds
        rearm_at = time.monotonic() + 20
        while True:
            if seconds != 10 and time.monotonic() >= rearm_at:
                client.test_mode(mode, 30000)  # same mode: only the 30 s hard timeout moves
                rearm_at += 20
            start = time.perf_counter_ns()
            raw = client.status()
            elapsed = (time.perf_counter_ns() - start) / 1e6
            guard(raw, original, mode)
            if raw['page'] != 'page_orb':
                raise RuntimeError('Throughput page interrupted')
            report['records'].append({'stage': 'animation', 'status': clean_status(raw), 'timing': telemetry(raw),
                                      'status_roundtrip_ms': elapsed, 'transport': dict(client.last_transport)})
            shown = presentation(raw)
            if shown is not None:
                report['records'][-1]['presentation'] = shown
            trace = raw.get('orb_prefetch_trace')
            if trace is not None:
                if (not isinstance(trace, dict) or type(trace.get('total')) is not int or trace['total'] < 0 or
                        not isinstance(trace.get('events'), list) or len(trace['events']) != min(trace['total'], 16) or
                        any(not isinstance(e, list) or len(e) != 9 or
                            any(type(v) is not int or not 0 <= v < 2**32 for v in e) for e in trace['events'])):
                    raise ValueError('Invalid numeric orb prefetch trace')
                report['records'][-1]['orb_prefetch_trace'] = trace
            if time.monotonic() >= deadline:
                break
            time.sleep(.1 if seconds == 10 else .2)
        report['summary'] = summarize(report['records'], {'frame_budget_ms': 25})
        report['summary']['status_roundtrip_ms'] = stats([r['status_roundtrip_ms'] for r in report['records']])
        report['orb_prefetch'] = prefetch_summary(report['records'])
        summary = report['summary']
        report['target_met'] = not target_failures(report)
        report['complete'] = True
    finally:
        try:
            if active:
                current = client.status()
                if current.get('test_mode') not in ('idle', mode):
                    raise RuntimeError('Throughput fixture no longer owns cleanup')
                guard(current, original, current['test_mode'])
                if current['test_mode'] == mode:
                    client.test_mode('restore', 100)
                select_tab(client, original['page'])
                restored = client.status()
                guard(restored, original)
                if restored['page'] != original['page']:
                    raise RuntimeError('Throughput restoration failed')
                if ('orb_interpolation_method' in original and
                        restored.get('orb_interpolation_method') != original['orb_interpolation_method']):
                    raise RuntimeError('Interpolation method restoration failed')
                report['restored'] = True
        except Exception:
            report['complete'] = False
            raise
        finally:
            private_write(folder / 'throughput.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://hallpanel.local:8080')
    parser.add_argument('--out', type=Path)
    parser.add_argument('--method', choices=('production', 'linear', 'staged'), default='production')
    parser.add_argument('--seconds', type=int, default=10, help='10 is the gate; 11-900 is a soak diagnostic that cannot become a reference')
    parser.add_argument('--require-soak', action='store_true', help='Fail unless the soak passes its stability policy; one failed soak is measured once more')
    parser.add_argument('--check', type=Path, help='Check a saved report without device access')
    parser.add_argument('--baseline', type=Path, default=BASELINE)
    parser.add_argument('--record-baseline', type=Path, help='Explicit passing reference; never overwrites')
    parser.add_argument('--require-target', action='store_true', help='Require production target and measured reference')
    args = parser.parse_args(argv)
    soak = args.seconds != 10
    if soak and (args.record_baseline or args.require_target or args.check):
        parser.error('A soak is a diagnostic: it cannot be checked as, or recorded as, the production reference')
    if args.require_soak and (not soak or args.seconds < SOAK_SECONDS):
        parser.error(f'--require-soak needs --seconds {SOAK_SECONDS} or more')
    try:
        baseline = load_baseline(args.baseline) if args.baseline.exists() or (args.require_target and not args.record_baseline) else None
        if args.check:
            report = json.loads(args.check.read_text())
        else:
            if (ROOT / '.panel-ci/quarantine.json').exists():
                raise RuntimeError('Throughput test stopped by unresolved CI quarantine')
            folder = args.out or ROOT / '.panel-ci/throughput' / datetime.now().strftime('run-%Y%m%d-%H%M%S-%f')
            with PanelClient(args.url, persistent=True) as client, client.exclusive():
                report = collect(client, folder, method=args.method, seconds=args.seconds)
                if args.require_soak:
                    verdict = evaluate_soak(report)
                    if not verdict['ok']:
                        # Both measurements are retained; a periodic stall reproduces, a hiccup does not.
                        repeat_folder = Path(str(folder) + '-repeat')
                        repeat = collect(client, repeat_folder, method=args.method, seconds=args.seconds)
                        first = verdict
                        verdict = evaluate_soak(repeat)
                        verdict.update(first_failures=first['failures'], repeat_report=str(repeat_folder / 'throughput.json'))
                        report = repeat
        if args.require_soak:
            verdict.update(summary=report.get('summary', {}), report=str(folder / 'throughput.json'))
            print(json.dumps(verdict, indent=2))
            return 0 if verdict['ok'] else 1
        if soak:
            result = {'ok': bool(report.get('complete') and report.get('restored') and report.get('target_met')),
                      'failures': target_failures(report), 'comparisons': [], 'baseline_checked': False,
                      'soak_seconds': args.seconds}
        else:
            result = evaluate(report, baseline)
        if args.record_baseline:
            if not result['ok']:
                raise ValueError('Cannot record failed production measurement')
            path = args.record_baseline.expanduser().absolute()
            path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
            reference = dict(report, policy_sha256=policy_digest())
            if args.check:
                reference['reference_source_sha256'] = hashlib.sha256(args.check.read_bytes()).hexdigest()
                reference['reference_source_path'] = str(args.check.absolute())
            with path.open('x') as output:
                path.chmod(0o600)
                json.dump(reference, output, indent=2, allow_nan=False)
        result['summary'] = report.get('summary', {})
        result['report'] = str(args.check or folder / 'throughput.json')
        print(json.dumps(result, indent=2))
        if args.check or args.require_target or args.record_baseline:
            return 0 if result['ok'] else 1
        return 0 if report.get('complete') and report.get('restored') else 1
    except (OSError, RuntimeError, ValueError, KeyError) as error:
        print(json.dumps({'ok': False, 'error': str(error)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
