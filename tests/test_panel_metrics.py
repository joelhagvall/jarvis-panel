"""Free performance gates and history regression tests; never contact the physical device."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import panel_metrics as metrics


def status(uptime=90000, heap=400000, psram=17000000, **kwargs):
    return {'uptime_ms': uptime, 'internal_free': heap, 'psram_free': psram,
            'page': 'page_glass_v2', **kwargs}


def report(samples=None, latency=120):
    samples = samples or [status(), status(uptime=91000)]
    captures = [{'elapsed_ms': latency, 'main_copy_us': 500} for _ in samples]
    return {'schema': 1, 'complete': True, 'workload': 'idle', 'samples': samples,
            'summary': metrics.summarize(samples, captures)}


class FakeClient:
    def __init__(self, values):
        self.values = iter(values)

    def status(self):
        value = next(self.values)
        if isinstance(value, Exception):
            raise value
        return value

    def screenshot(self, destination):
        Path(destination).write_bytes(b'fake private framebuffer')
        return {'elapsed_ms': 120, 'main_copy_us': 500, 'frame': 1, 'transfer_bytes': 1024}


class PanelMetricsTests(unittest.TestCase):
    def test_animation_restores_original_page_after_success_and_failure(self):
        class AnimationClient:
            def __init__(self):
                self.mode, self.page, self.calls = '', 'page_hemmet', []

            def status(self):
                return status(page=self.page, test_mode=self.mode)

            def test_mode(self, mode, duration_ms):
                self.calls.append((mode, duration_ms))
                self.mode = '' if mode == 'restore' else mode
                self.page = 'page_hemmet' if mode == 'restore' else 'page_orb'

        for failure in (False, True):
            client = AnimationClient()
            captured = report([status(page='page_orb', test_mode='animation'),
                               status(uptime=91000, page='page_orb', test_mode='animation')])
            with patch.object(metrics, 'collect', side_effect=OSError('device disconnected') if failure else None,
                              return_value=captured) as collect:
                if failure:
                    with self.assertRaises(OSError):
                        metrics.collect_animation(client, '/unused')
                else:
                    self.assertIs(metrics.collect_animation(client, '/unused'), captured)
                collect.assert_called_once_with(client, '/unused', samples=5, interval=.5,
                                                screenshot=False, workload='animation')
            self.assertEqual(client.calls, [('animation', 30000), ('restore', 100)])
            self.assertEqual(client.page, 'page_hemmet')

    def test_animation_refuses_busy_panel_and_oversized_duration(self):
        client = FakeClient([status(page='page_orb', test_mode='speaking')])
        with self.assertRaises(RuntimeError):
            metrics.collect_animation(client, '/unused')
        with self.assertRaises(ValueError):
            metrics.collect_animation(client, '/unused', samples=120, interval=2)

    def test_required_metrics_fail_closed_and_non_finite_values_rejected(self):
        policy = metrics.load_policy()
        policy['required'].append('render_avg_ms')
        result = metrics.evaluate(report(), policy=policy)
        self.assertFalse(result['ok'])
        self.assertIn('Required metric unavailable: render_avg_ms', result['failures'])
        for bad in (float('nan'), float('inf'), '400000', True, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                metrics.clean_status(status(heap=bad))

    def test_memory_floor_and_capture_leak_gate(self):
        for samples in ([status(), status(uptime=91000, heap=90000)],
                        [status(), status(uptime=91000, psram=16000000)]):
            self.assertFalse(metrics.evaluate(report(samples))['ok'])
        self.assertTrue(metrics.evaluate(report())['ok'])

    def test_measured_baseline_compares_memory_and_latency(self):
        baseline = report()
        current = report(latency=600)
        result = metrics.evaluate(current, baseline)
        self.assertFalse(result['ok'])
        self.assertTrue(result['baseline_checked'])
        self.assertTrue(any('screenshot_latency_ms regressed' in issue for issue in result['failures']))
        self.assertFalse(metrics.evaluate(report())['baseline_checked'])

    def test_baseline_context_and_disappearing_metrics_rejected(self):
        baseline = report()
        baseline['workload'] = 'orb'
        self.assertFalse(metrics.evaluate(report(), baseline)['ok'])
        baseline = report()
        baseline['summary']['render_avg_ms'] = {'median': 10}
        self.assertFalse(metrics.evaluate(report(), baseline)['ok'])
        baseline = report([status(page='page_hemmet'), status(uptime=91000, page='page_hemmet')])
        self.assertFalse(metrics.evaluate(report(), baseline)['ok'])

    def test_restarts_wraparound_and_same_uptime_new_boot(self):
        self.assertTrue(metrics.restarted(status(), status(uptime=2)))
        self.assertFalse(metrics.restarted(status(uptime=2**32 - 100), status(uptime=2)))
        self.assertTrue(metrics.restarted(status(boot_id='a'), status(boot_id='b')))
        result = metrics.evaluate(report([status(boot_id='a'), status(uptime=91000, boot_id='b')]))
        self.assertIn('Unexpected restart during measurement', result['failures'])

    def test_incomplete_and_page_changes_rejected(self):
        incomplete = report()
        incomplete['complete'] = False
        self.assertFalse(metrics.evaluate(incomplete)['ok'])
        self.assertFalse(metrics.evaluate(report([status(), status(uptime=91000, page='page_hemmet')]))['ok'])

    def test_allowlist_masks_private_unrecognized_fields_and_aliases(self):
        clean = metrics.clean_status(status(token='secret', ssid='private', text='calendar',
                                           firmware_elf_sha256='abc', wifi_rssi=-45, reset_reason=3))
        self.assertNotIn('token', clean)
        self.assertNotIn('ssid', clean)
        self.assertNotIn('text', clean)
        self.assertEqual(clean['firmware_sha256'], 'abc')
        self.assertEqual(clean['wifi_rssi_dbm'], -45)
        self.assertEqual(clean['reset_reason'], '3')
        clean = metrics.clean_status(status(boot_id=42, wifi_rssi=None, test_mode=''))
        self.assertEqual(clean['boot_id'], '42')
        self.assertEqual(clean['test_mode'], '')
        self.assertNotIn('wifi_rssi_dbm', clean)

    def test_render_rates_derive_only_from_this_workload_counter_interval(self):
        samples = [status(render_frames_total=100, render_busy_total_us=1000000, redraws_per_s=99, render_avg_ms=99),
                   status(uptime=91000, render_frames_total=112, render_busy_total_us=1120000,
                          redraws_per_s=99, render_avg_ms=99),
                   status(uptime=92000, render_frames_total=124, render_busy_total_us=1240000,
                          redraws_per_s=99, render_avg_ms=99)]
        summary = metrics.summarize(samples, [])
        self.assertEqual(summary['redraws_per_s']['median'], 12)
        self.assertEqual(summary['render_avg_ms']['median'], 10)
        self.assertEqual(summary['render_avg_ms']['source'], 'interval_counters')
        policy = metrics.load_policy()
        policy['required'].extend(['redraws_per_s', 'render_avg_ms'])
        self.assertTrue(metrics.evaluate(report(samples), policy=policy)['ok'])

    def test_render_counters_do_not_reuse_stale_duration_when_nothing_redrew(self):
        samples = [status(render_frames_total=100, render_busy_total_us=1000000, render_avg_ms=99),
                   status(uptime=91000, render_frames_total=100, render_busy_total_us=1000000, render_avg_ms=99)]
        summary = metrics.summarize(samples, [])
        self.assertEqual(summary['redraws_per_s']['median'], 0)
        self.assertNotIn('render_avg_ms', summary)
        samples[-1]['render_frames_total'] = 1
        self.assertNotIn('redraws_per_s', metrics.summarize(samples, []))

    def test_animation_workload_requires_observed_frames_and_render_duration(self):
        idle = report()
        idle['workload'] = 'animation'
        self.assertFalse(metrics.evaluate(idle)['ok'])
        animated = report([status(render_frames_total=100, render_busy_total_us=1000000),
                           status(uptime=91000, render_frames_total=112, render_busy_total_us=1120000)])
        animated['workload'] = 'animation'
        self.assertTrue(metrics.evaluate(animated)['ok'])
        animated['summary']['redraws_per_s']['min'] = 0
        self.assertFalse(metrics.evaluate(animated)['ok'])

    def test_previous_touch_and_render_windows_are_not_animation_samples(self):
        samples = [status(render_frames_total=100, render_busy_total_us=1000000),
                   status(uptime=91000, render_frames_total=125, render_busy_total_us=1250000)]
        baseline = report(samples)
        baseline['workload'] = 'animation'
        current = copy.deepcopy(baseline)
        baseline['summary'].update(touch_refresh_p95_ms={'p95': 10}, render_max_ms={'max': 20})
        current['summary'].update(touch_refresh_p95_ms={'p95': 999}, render_max_ms={'max': 999})
        result = metrics.evaluate(current, baseline)
        self.assertTrue(result['ok'])
        self.assertIn('touch_refresh_p95_ms', result['not_compared'])
        self.assertIn('render_max_ms', result['not_compared'])
        self.assertEqual(current['summary']['touch_refresh_p95_ms']['p95'], 999)

    def test_collect_keeps_failure_evidence_and_no_sensitive_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'capture'
            client = FakeClient([status(), OSError('token=secret')])
            with self.assertRaises(OSError):
                metrics.collect(client, folder, samples=2, interval=0, screenshot=False)
            saved = json.loads((folder / 'metrics.json').read_text())
            self.assertFalse(saved['complete'])
            self.assertEqual(saved['error_type'], 'OSError')
            self.assertNotIn('secret', (folder / 'metrics.json').read_text())
            self.assertEqual((folder / 'metrics.json').stat().st_mode & 0o777, 0o600)
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)

    def test_collect_reports_missing_hardware_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            saved = metrics.collect(FakeClient([status(), status(uptime=91000)]), Path(tmp) / 'capture',
                                    samples=2, interval=0, screenshot=False)
            self.assertIn('cpu_load_percent', saved['unsupported'])
            self.assertIn('render_avg_ms', saved['unsupported'])
            self.assertNotIn('internal_free', saved['unsupported'])

    def test_baseline_is_exclusive_and_cannot_promote_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'baseline.json'
            metrics.save_baseline(report(), path)
            with self.assertRaises(FileExistsError):
                metrics.save_baseline(report(), path)
            with self.assertRaises(ValueError):
                metrics.save_baseline(report([status(heap=1), status(heap=1)]), Path(tmp) / 'bad.json')

    def test_history_failure_alerts_only_change_once_then_recover(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'health.json'
            for i in range(4):
                result = metrics.monitor_once(FakeClient([OSError('secret')]), path, now=i * 300)
                self.assertEqual(result['notify'], i == 2)
            recovered = metrics.monitor_once(FakeClient([status()]), path, now=1500)
            self.assertTrue(recovered['notify'])
            self.assertTrue(recovered['recovered'])
            self.assertNotIn('secret', path.read_text())

    def test_history_detects_restart_and_has_bounded_retention(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'health.json'
            for i in range(14):
                metrics.monitor_once(FakeClient([status(uptime=90000 + i * 1000)]), path, keep=12, now=i * 300)
            self.assertEqual(len(json.loads(path.read_text())['entries']), 12)
            result = metrics.monitor_once(FakeClient([status(uptime=3, reset_reason=3)]), path, keep=12, now=4500)
            self.assertEqual(result['alerts'][0]['kind'], 'unexpected_restart')

    def test_reconnect_and_repeated_restarts_are_failures(self):
        measurement = report([status(wifi_disconnects=0), status(uptime=91000, wifi_disconnects=1)])
        self.assertIn('Wi-Fi reconnect occurred during measurement', metrics.evaluate(measurement)['failures'])
        entries = [{'time': i * 60, 'ok': True, 'status': status(boot_id=str(i))} for i in range(4)]
        self.assertTrue(any(alert['kind'] == 'repeated_restarts' for alert in metrics.analyze_history(entries)))
        entries[-1]['status']['wifi_connected'] = False
        self.assertTrue(any(alert['kind'] == 'wifi_disconnected' for alert in metrics.analyze_history(entries)))

    def test_history_finds_monotonic_leak_but_not_page_or_firmware_change(self):
        entries = [{'time': i * 300, 'ok': True,
                    'status': status(uptime=90000 + i * 300000, heap=400000 - i * 10000,
                                     firmware_sha256='a')} for i in range(12)]
        alerts = metrics.analyze_history(entries)
        self.assertTrue(any(alert['kind'] == 'possible_memory_leak' for alert in alerts))
        changed = copy.deepcopy(entries)
        changed[-1]['status']['firmware_sha256'] = 'b'
        self.assertFalse(metrics.analyze_history(changed))
        changed = copy.deepcopy(entries)
        changed[-1]['status']['page'] = 'page_orb'
        self.assertFalse(metrics.analyze_history(changed))

    def test_low_memory_alert_needs_consecutive_samples_of_one_boot(self):
        # 2026-11-03: one sample 72 bytes under the floor pushed "minne lågt", then "fungerar igen" 5 min later.
        def entries(heaps, boots=None):
            return [{'time': i * 300, 'ok': True,
                     'status': status(uptime=600000 + i * 300000, heap=heap, boot_id=(boots or ['a'] * 9)[i])}
                    for i, heap in enumerate(heaps)]
        low = lambda history: [a for a in metrics.analyze_history(history) if a['kind'] == 'low_memory']
        self.assertFalse(low(entries([110000, 110000, 98232])))
        self.assertFalse(low(entries([93000, 93000, 103000])))
        self.assertFalse(low(entries([93000, 93000, 93000], boots=['a', 'b', 'b'])))
        alerts = low(entries([110000, 93000, 93000, 93000]))
        self.assertEqual([(a['metric'], a['samples']) for a in alerts], [('internal_free', 3)])

    def test_symlink_history_and_baseline_paths_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'target.json'
            path.write_text('{}')
            link = Path(tmp) / 'link.json'
            link.symlink_to(path)
            with self.assertRaises(ValueError):
                metrics.private_write(link, {'replace': True})
            with self.assertRaises(ValueError):
                metrics.monitor_once(FakeClient([status()]), link)
            self.assertEqual(path.read_text(), '{}')


if __name__ == '__main__':
    unittest.main()
