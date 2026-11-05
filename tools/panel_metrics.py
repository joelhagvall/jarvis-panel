#!/usr/bin/env python3
"""Measured performance, explicit baselines and bounded passive health history.

Animation sampling uses bounded visual test mode. No firmware, settings, audio,
paid API or automatic baseline changes occur here.
"""
import argparse
from datetime import datetime, timezone
import fcntl
import http.client
import json
import math
import os
from pathlib import Path
import statistics
import sys
import tempfile
import time

from panel_remote import PanelClient, ROOT, DEFAULT_RECEIVE_BUFFER_BYTES

POLICY_PATH = ROOT / 'tests/performance/policy.json'
DEFAULT_HISTORY = ROOT / '.panel-ci/health.json'
SCHEMA = 1
# Status fields are deliberately allowlisted: UI text, credentials and network names are never archived.
NUMERIC_FIELDS = {
    'uptime_ms', 'psram_free', 'internal_free', 'psram_min_free', 'internal_min_free',
    'wifi_rssi_dbm', 'wifi_rssi_age_ms', 'wifi_disconnects', 'redraws_per_s', 'render_avg_ms', 'render_max_ms',
    'touch_refresh_last_ms', 'touch_refresh_p95_ms', 'boot_ready_ms', 'last_input_age_ms',
    'render_frames_total', 'render_busy_total_us', 'internal_largest_block',
    'status_main_us', 'status_encode_us', 'status_worker_us', 'status_radio_us', 'status_main_heap_us', 'status_queue_us',
    'status_previous_send_us', 'status_previous_send_bytes', 'status_previous_send_age_ms', 'status_send_count', 'status_send_errors',
    'orb_prefetch_hits', 'orb_prefetch_fallbacks', 'orb_prefetch_decoded', 'orb_prefetch_errors',
    'orb_prefetch_last_decode_us', 'orb_prefetch_bytes', 'orb_source_changes', 'orb_virtual_frames', 'orb_target_fps',
    'orb_interpolation_method', 'orb_interpolation_scratch_bytes',
    'orb_probe_fastest_method',
    'screen_swap_applied', 'screen_swap_fallbacks', 'screen_swap_last_fallback', 'screen_swap_last_areas',
    'screen_swap_last_pixels', 'screen_swap_last_us', 'screen_swap_last_old_us', 'screen_swap_last_collected',
    'orb_prefetch_miss_lead_max', 'orb_prefetch_last_unpack_us', 'orb_prefetch_last_mix_us',
    *(f'orb_probe_{i}_us' for i in range(12)),
    'pixel_dma_ready', 'pixel_dma_errors', 'pixel_dma_copies', 'pixel_dma_bytes', 'pixel_dma_us', 'pixel_staged_copies',
    'pixel_dma_burst', 'pixel_ppa_burst',
    'pixel_ppa_ready', 'pixel_ppa_errors', 'pixel_ppa_copies', 'pixel_ppa_bytes', 'pixel_ppa_us',
    'pixel_mask_images', 'pixel_mask_pixels', 'pixel_mask_skipped', 'pixel_mask_opaque', 'pixel_mask_us',
}
TEXT_FIELDS = {'page', 'firmware_version', 'firmware_sha256', 'build_id', 'boot_id', 'reset_reason', 'test_mode'}
BOOL_FIELDS = {'wifi_connected', 'api_connected', 'voice_active', 'boot_healthy'}
ALIASES = {
    'psram_low': 'psram_min_free', 'internal_low': 'internal_min_free',
    'wifi_rssi': 'wifi_rssi_dbm', 'touch_last_ms': 'touch_refresh_last_ms',
    'firmware_elf_sha256': 'firmware_sha256',
}
UNSUPPORTED = {
    'cpu_load_percent': 'No CPU utilization counter is exposed by this firmware.',
    'finger_to_photon_ms': 'Software refresh telemetry cannot measure optical touch latency.',
    'audio_quality': 'Requires a microphone/loopback measurement and an acoustic reference.',
}


def load_policy(path=POLICY_PATH):
    value = json.loads(Path(path).read_text())
    if value.get('schema') != SCHEMA:
        raise ValueError('Unsupported performance policy schema')
    return value


def private_write(path, value):
    """Atomically replace an owned private JSON file without following an existing symlink."""
    path = Path(path).expanduser().absolute()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if path.is_symlink() or path.parent.is_symlink():
        raise ValueError('History and baseline paths must not be symlinks')
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '-', dir=path.parent)
    try:
        with os.fdopen(fd, 'w') as output:
            json.dump(value, output, ensure_ascii=False, indent=2, allow_nan=False)
            output.write('\n')
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def clean_status(status):
    if not isinstance(status, dict):
        raise ValueError('Panel status must be an object')
    value = {}
    for original, item in status.items():
        field = ALIASES.get(original, original)
        if item is None:
            continue  # Optional protocol metrics may explicitly advertise unavailable counters.
        if field in NUMERIC_FIELDS:
            if type(item) not in (float, int) or not math.isfinite(item):
                raise ValueError('Panel returned an invalid numeric metric: ' + field)
            if field != 'wifi_rssi_dbm' and item < 0:
                raise ValueError('Panel returned a negative metric: ' + field)
            value[field] = item
        elif field in ('reset_reason', 'boot_id') and type(item) is int:
            value[field] = str(item)
        elif field in TEXT_FIELDS and isinstance(item, str):
            value[field] = item[:128]
        elif field in BOOL_FIELDS and type(item) is bool:
            value[field] = item
    for field in ('uptime_ms', 'psram_free', 'internal_free', 'page'):
        if field not in value:
            raise ValueError('Missing panel metric: ' + field)
    return value


def percentile(values, fraction):
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]


def summarize(samples, captures):
    result = {}
    for field in sorted(NUMERIC_FIELDS):
        values = [sample[field] for sample in samples if field in sample]
        if values:
            result[field] = {'min': min(values), 'max': max(values), 'median': statistics.median(values),
                             'p95': percentile(values, .95), 'n': len(values)}
    for field in ('elapsed_ms', 'main_copy_us'):
        values = [capture[field] for capture in captures if field in capture]
        if values:
            name = 'screenshot_latency_ms' if field == 'elapsed_ms' else 'screenshot_copy_us'
            result[name] = {'min': min(values), 'max': max(values), 'median': statistics.median(values),
                            'p95': percentile(values, .95), 'n': len(values)}
    if len(samples) >= 2 and all('render_frames_total' in s and 'render_busy_total_us' in s for s in samples):
        redraws, durations = [], []
        valid = True
        for before, after in zip(samples, samples[1:]):
            elapsed_ms = (after['uptime_ms'] - before['uptime_ms']) % 2**32
            frames = after['render_frames_total'] - before['render_frames_total']
            busy_us = after['render_busy_total_us'] - before['render_busy_total_us']
            if restarted(before, after) or frames < 0 or busy_us < 0 or elapsed_ms <= 0:
                valid = False
                break
            redraws.append(frames * 1000 / elapsed_ms)
            if frames:
                durations.append(busy_us / (1000 * frames))
        # Firmware's rolling 60-second averages may belong to an earlier workload.
        # Never mix them with the measured interval, including an interval with zero redraws.
        result.pop('redraws_per_s', None)
        result.pop('render_avg_ms', None)
        if valid:
            for field, values in (('redraws_per_s', redraws), ('render_avg_ms', durations)):
                if values:
                    result[field] = {'min': min(values), 'max': max(values), 'median': statistics.median(values),
                                     'p95': percentile(values, .95), 'n': len(values),
                                     'source': 'interval_counters'}
    return result


def restarted(before, after):
    if before.get('boot_id') and after.get('boot_id'):
        return before['boot_id'] != after['boot_id']
    # millis() wraps after 49.7 days. A wrap close to the boundary is not a restart.
    a, b = before['uptime_ms'], after['uptime_ms']
    wrapped = a >= 2**32 - 120000 and b <= 120000
    return b < a and not wrapped


def collect(client, folder, samples=5, interval=0.5, screenshot=True, workload='idle'):
    if type(samples) is not int or not 2 <= samples <= 120 or not 0 <= interval <= 30:
        raise ValueError('Use 2–120 samples and a 0–30 second interval')
    if not isinstance(workload, str) or not workload or len(workload) > 80:
        raise ValueError('A short workload name is required')
    folder = Path(folder).expanduser().absolute()
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    folder.chmod(0o700)
    report = {'schema': SCHEMA, 'captured_at': datetime.now(timezone.utc).isoformat(), 'workload': workload,
              'samples': [], 'captures': [], 'unsupported': dict(UNSUPPORTED), 'complete': False}
    started = time.monotonic()
    try:
        for index in range(samples):
            report['samples'].append(clean_status(client.status()))
            if screenshot:
                capture = client.screenshot(folder / f'capture-{index:03d}.jpg')
                report['captures'].append({key: capture[key] for key in
                                           ('elapsed_ms', 'main_copy_us', 'frame', 'transfer_bytes', 'transport',
                                            'receive_buffer_bytes') if key in capture})
            if index + 1 < samples:
                time.sleep(interval)
        report['complete'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        raise
    finally:
        report['duration_s'] = round(time.monotonic() - started, 3)
        report['summary'] = summarize(report['samples'], report['captures'])
        report['supported'] = sorted(report['summary'])
        for field, reason in {
                'redraws_per_s': 'No render counter is exposed in device status.',
                'render_avg_ms': 'No render duration is exposed in device status.',
                'render_max_ms': 'No worst frame duration is exposed in device status.',
                'touch_refresh_p95_ms': 'No sample-to-refresh histogram is exposed in device status.',
                'boot_ready_ms': 'No ready milestone is exposed in device status.',
                'wifi_rssi_dbm': 'No radio signal metric is exposed in device status.',
        }.items():
            if field not in report['summary']:
                report['unsupported'][field] = reason
        report['metric_notes'] = {
            'render_avg_ms': 'Derived from lifetime counter differences when available; no redraw means no duration sample.',
            'render_max_ms': 'Firmware window/lifetime maximum; cannot attribute an earlier maximum to this workload.',
            'touch_refresh_p95_ms': 'Firmware sample history, not finger-to-photon timing.',
        }
        private_write(folder / 'metrics.json', report)
    return report


def collect_animation(client, folder, samples=5, interval=.5):
    """Sample a bounded silent animation and prove restoration, reusing the caller's lease."""
    if type(samples) is not int or not 2 <= samples <= 120 or not 0 < interval <= 2 or \
            (samples - 1) * interval > 10:
        raise ValueError('Animation requires 2–120 samples spanning at most 10 seconds')
    original = client.status()
    idle_mode = original.get('test_mode', '')
    if original['page'] not in ('page_glass_v2', 'page_hemmet', 'page_musik', 'page_tavla') or \
            idle_mode not in ('', 'idle') or original.get('voice_active') or original.get('physical_touch_active'):
        raise RuntimeError('Panel is busy; animation measurement was not started')
    attempted = False
    try:
        attempted = True
        client.test_mode('animation', 30000)
        deadline = time.monotonic() + 2
        while True:
            current = client.status()
            if current.get('test_mode') == 'animation' and current['page'] == 'page_orb':
                break
            if time.monotonic() >= deadline:
                raise RuntimeError('Animation mode did not become active')
            time.sleep(.05)
        report = collect(client, folder, samples=samples, interval=interval,
                         screenshot=False, workload='animation')
        if any(sample.get('test_mode') != 'animation' or sample['page'] != 'page_orb'
               for sample in report['samples']):
            report['complete'] = False
            report['error_type'] = 'AnimationInterrupted'
            private_write(Path(folder) / 'metrics.json', report)
            raise RuntimeError('Animation ended or was preempted during measurement')
        return report
    finally:
        if attempted:
            # Restore is a separate idempotent cleanup operation, never a retry of the start/input.
            client.test_mode('restore', 100)
            restored = client.status()
            if restored['page'] != original['page'] or restored.get('test_mode', '') not in ('', 'idle'):
                raise RuntimeError('Animation cleanup could not prove original page and idle mode')


def evaluate(report, baseline=None, policy=None):
    policy = load_policy() if policy is None else policy
    override = policy.get('workloads', {}).get(report.get('workload'), {})
    if override:
        policy = {**policy, 'required': sorted(set(policy.get('required', [])) | set(override.get('required', [])))}
        for key in ('minimum', 'maximum', 'max_drop_bytes', 'regressions'):
            policy[key] = {**policy.get(key, {}), **override.get(key, {})}
    failures, comparisons = [], []
    # These are the previous 60-second window, which can span smoke/settings and reset
    # mid-animation. Raw values remain archived; fresh per-event gates live in responsiveness.
    not_compared = {field: 'Legacy history is not scoped to this workload; fresh events are gated by responsiveness'
                    for field in ('render_max_ms', 'touch_refresh_p95_ms')
                    if report.get('workload') in ('idle', 'animation')}
    samples, summary = report.get('samples', []), report.get('summary', {})
    if report.get('schema') != SCHEMA or not report.get('complete') or len(samples) < 2:
        failures.append('Incomplete measurement')
    for field in policy.get('required', []):
        if field in not_compared:
            failures.append('Required metric is not scoped to this workload: ' + field)
            continue
        measured = summary.get(field, {})
        complete = measured.get('n') == len(samples)
        if measured.get('source') == 'interval_counters':
            complete = measured.get('n', 0) >= 1
        if not complete:
            failures.append('Required metric unavailable: ' + field)
    for field, floor in policy.get('minimum', {}).items():
        if field in summary and summary[field]['min'] < floor:
            failures.append(f'{field} below minimum {floor}')
    for field, ceiling in policy.get('maximum', {}).items():
        if field in summary and summary[field]['max'] > ceiling:
            failures.append(f'{field} above maximum {ceiling}')
    if samples:
        for sample in samples:
            if restarted(samples[0], sample):
                failures.append('Unexpected restart during measurement')
                break
        if len({sample['page'] for sample in samples}) != 1:
            failures.append('Page changed during measurement')
        if any(sample.get('wifi_connected') is False for sample in samples):
            failures.append('Wi-Fi disconnected during measurement')
        disconnect_counts = [sample['wifi_disconnects'] for sample in samples if 'wifi_disconnects' in sample]
        if len(disconnect_counts) > 1 and disconnect_counts[-1] > disconnect_counts[0]:
            failures.append('Wi-Fi reconnect occurred during measurement')
        for field, budget in policy.get('max_drop_bytes', {}).items():
            if field in samples[0] and field in samples[-1] and samples[0][field] - samples[-1][field] > budget:
                failures.append(f'{field} fell by more than {budget} bytes')
    if baseline is not None:
        if baseline.get('schema') != SCHEMA or not baseline.get('complete'):
            failures.append('Baseline is not a complete measurement')
        if baseline.get('workload') != report.get('workload'):
            failures.append('Baseline workload differs')
        if samples and baseline.get('samples') and samples[0]['page'] != baseline['samples'][0]['page']:
            failures.append('Baseline page differs')
        for field, rule in policy.get('regressions', {}).items():
            if field in not_compared:
                continue
            previous = baseline.get('summary', {}).get(field)
            current = summary.get(field)
            if previous is None:
                continue  # A metric not measured before has no honest relative baseline.
            if current is None:
                failures.append('Previously measured metric disappeared: ' + field)
                continue
            statistic = rule.get('statistic', 'median')
            a, b = previous[statistic], current[statistic]
            margin = max(rule.get('absolute', 0), abs(a) * rule.get('relative', 0))
            loss = a - b if rule['direction'] == 'lower' else b - a
            item = {'metric': field, 'baseline': a, 'current': b, 'allowed_change': margin, 'ok': loss <= margin}
            comparisons.append(item)
            if not item['ok']:
                failures.append(field + ' regressed against measured baseline')
    return {'ok': not failures, 'failures': failures, 'comparisons': comparisons,
            'baseline_checked': baseline is not None, 'unsupported': report.get('unsupported', {}),
            'not_compared': not_compared}


def save_baseline(report, path, policy=None):
    result = evaluate(report, policy=policy)
    if not result['ok']:
        raise ValueError('Cannot establish baseline from a failed measurement')
    path = Path(path).expanduser().absolute()
    if path.exists():
        raise FileExistsError('Baseline exists; choose a new reviewed path')
    # Exclusive creation prevents two agents from promoting different captures at once.
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        json.dump(report, out, ensure_ascii=False, indent=2, allow_nan=False)


def analyze_history(entries, policy=None):
    policy = load_policy() if policy is None else policy
    monitor = policy.get('monitor', {})
    alerts = []
    streak = 0
    for entry in reversed(entries):
        if entry.get('ok'):
            break
        streak += 1
    if streak >= monitor.get('failure_streak', 3):
        alerts.append({'kind': 'repeated_failures', 'count': streak})
    healthy = [entry for entry in entries if entry.get('ok') and 'status' in entry]
    if len(healthy) >= 2 and entries[-1].get('ok'):
        before, after = healthy[-2]['status'], healthy[-1]['status']
        if restarted(before, after):
            alerts.append({'kind': 'unexpected_restart', 'reset_reason': after.get('reset_reason', 'unavailable')})
        recent = [entry for entry in healthy if entry['time'] >= healthy[-1]['time'] - 900]
        restarts = sum(restarted(a['status'], b['status']) for a, b in zip(recent, recent[1:]))
        if restarts >= 3:
            alerts.append({'kind': 'repeated_restarts', 'count': restarts, 'window_s': 900})
    if healthy and entries[-1].get('ok'):
        last = healthy[-1]
        if last['status'].get('wifi_connected') is False:
            alerts.append({'kind': 'wifi_disconnected'})
        # Compare only an uninterrupted boot, firmware and page to avoid confusing a workload change with a leak.
        window = []
        for entry in reversed(healthy):
            status, current = entry['status'], last['status']
            if restarted(status, current) or status['page'] != current['page'] or \
                    status.get('firmware_sha256') != current.get('firmware_sha256'):
                break
            window.append(entry)
            if len(window) >= monitor.get('leak_samples', 12):
                break
        window.reverse()
        if len(window) >= monitor.get('leak_samples', 12) and \
                window[-1]['time'] - window[0]['time'] >= monitor.get('leak_min_seconds', 1800):
            width = max(2, len(window) // 3)
            for field, budget in monitor.get('leak_drop_bytes', {}).items():
                old = statistics.median(entry['status'][field] for entry in window[:width])
                new = statistics.median(entry['status'][field] for entry in window[-width:])
                falling = sum(a['status'][field] >= b['status'][field] for a, b in zip(window, window[1:]))
                if old - new > budget and falling >= math.ceil((len(window) - 1) * .8):
                    alerts.append({'kind': 'possible_memory_leak', 'metric': field,
                                   'drop_bytes': old - new, 'duration_s': window[-1]['time'] - window[0]['time']})
        # A floor alert needs the last N samples of this boot all under it: one sample 72 bytes under the
        # 96 KiB floor at 06:00 on 2026-11-03 pushed "minne lågt" and five minutes later "fungerar igen".
        needed = max(1, int(monitor.get('low_memory_samples', 1)))
        run = []
        for entry in reversed(healthy):
            if len(run) >= needed or restarted(entry['status'], last['status']):
                break
            run.append(entry['status'])
        for field, floor in policy.get('minimum', {}).items():
            if len(run) >= needed and all(field in s and s[field] < floor for s in run):
                alerts.append({'kind': 'low_memory' if 'free' in field else 'below_minimum', 'metric': field,
                               'value': last['status'][field], 'minimum': floor, 'samples': needed})
    return alerts


def _monitor_once(client, history_path, policy=None, keep=2016, now=None):
    if type(keep) is not int or not 12 <= keep <= 10000:
        raise ValueError('History retention must be 12–10000 samples')
    policy = load_policy() if policy is None else policy
    path = Path(history_path).expanduser().absolute()
    if path.is_symlink():
        raise ValueError('History must not be a symlink')
    if path.exists() and path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('History exceeds its 16 MiB safety limit')
    history = json.loads(path.read_text()) if path.exists() else {'schema': SCHEMA, 'entries': [], 'active_alerts': []}
    if history.get('schema') != SCHEMA or not isinstance(history.get('entries'), list):
        raise ValueError('Invalid health history')
    entry = {'time': time.time() if now is None else now}
    try:
        entry.update(ok=True, status=clean_status(client.status()))
    except (OSError, http.client.HTTPException, ValueError, KeyError, RuntimeError) as error:
        entry.update(ok=False, error_type=type(error).__name__)
    history['entries'] = (history['entries'] + [entry])[-keep:]
    alerts = analyze_history(history['entries'], policy)
    # Notify only when an alert category appears or clears; counters naturally change each run.
    signature = sorted({(alert['kind'], alert.get('metric', '')) for alert in alerts})
    previous = [tuple(item) for item in history.get('active_alerts', [])]
    changed = signature != previous
    history['active_alerts'] = signature
    history['alerts'] = alerts
    private_write(path, history)
    return {'ok': entry['ok'] and not alerts, 'notify': changed, 'alerts': alerts,
            'recovered': changed and bool(previous) and not signature,
            'samples': len(history['entries']), 'history': str(path)}


def monitor_once(client, history_path=DEFAULT_HISTORY, policy=None, keep=2016, now=None):
    """One passive status read, with a process lock protecting the read/modify/write history transaction."""
    path = Path(history_path).expanduser().absolute()
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    lock = path.with_name(path.name + '.lock')
    flags = os.O_WRONLY | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0)
    with os.fdopen(os.open(lock, flags, 0o600), 'w') as output:
        fcntl.flock(output, fcntl.LOCK_EX)
        return _monitor_once(client, path, policy, keep, now)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default=os.environ.get('HALLPANEL_DEBUG_URL', 'http://hallpanel.local:8080'))
    commands = parser.add_subparsers(dest='command', required=True)
    measure = commands.add_parser('collect')
    measure.add_argument('--out', type=Path, required=True)
    measure.add_argument('--samples', type=int, default=5)
    measure.add_argument('--interval', type=float, default=.5)
    measure.add_argument('--workload', default='idle')
    measure.add_argument('--no-screenshot', action='store_true')
    measure.add_argument('--animation', action='store_true', help='Bounded silent animation; restores original page')
    measure.add_argument('--receive-buffer', type=int, default=DEFAULT_RECEIVE_BUFFER_BYTES,
                         help='TCP receive buffer, 4096–65536 bytes; 0 uses OS default for diagnosis')
    check = commands.add_parser('check')
    check.add_argument('report', type=Path)
    check.add_argument('--baseline', type=Path)
    baseline = commands.add_parser('baseline')
    baseline.add_argument('report', type=Path)
    baseline.add_argument('--out', type=Path, required=True)
    monitor = commands.add_parser('monitor')
    monitor.add_argument('--once', action='store_true', required=True, help='One passive read, suitable for systemd timer')
    monitor.add_argument('--history', type=Path, default=DEFAULT_HISTORY)
    monitor.add_argument('--keep', type=int, default=2016)
    args = parser.parse_args(argv)
    try:
        if args.command == 'check':
            report = json.loads(args.report.read_text())
            result = evaluate(report, json.loads(args.baseline.read_text()) if args.baseline else None)
        elif args.command == 'baseline':
            save_baseline(json.loads(args.report.read_text()), args.out)
            result = {'ok': True, 'baseline': str(args.out)}
        elif args.command == 'monitor':
            # DNS/connect failures must be recorded as health failures as well.
            class OneShotStatus:
                def status(self):
                    with PanelClient(args.url, persistent=True) as client:
                        return client.status()
            result = monitor_once(OneShotStatus(), args.history, keep=args.keep)
        else:
            with PanelClient(args.url, persistent=True,
                             receive_buffer_bytes=args.receive_buffer or None) as client, client.exclusive():
                if args.animation:
                    if args.workload not in ('idle', 'animation'):
                        raise ValueError('--animation sets workload=animation')
                    report = collect_animation(client, args.out, args.samples, args.interval)
                else:
                    report = collect(client, args.out, args.samples, args.interval,
                                     not args.no_screenshot, args.workload)
                result = evaluate(report)
                result['report'] = str(args.out / 'metrics.json')
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['ok'] else 1
    except (OSError, http.client.HTTPException, ValueError, KeyError, RuntimeError) as error:
        print('Performance command failed: ' + type(error).__name__, file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
