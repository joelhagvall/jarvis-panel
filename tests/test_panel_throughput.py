import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import panel_throughput as throughput


def measurement():
    return {'schema': 1, 'protocol': throughput.PROTOCOL, 'method': 'production',
            'complete': True, 'restored': True, 'target_met': True,
            'policy_sha256': throughput.policy_digest(),
            'records': [{'status': {'pixel_dma_errors': 0, 'pixel_ppa_errors': 0}}],
            'summary': {'animation.refresh_interval_ms': {'n': 600, 'p95': 18, 'max': 26},
                        'animation.refresh_work_ms': {'p95': 16}, 'animation.render_work_ms': {'p95': 13},
                        'animation.jank_percent': {'max': .5}, 'animation.fps': {'median': 60},
                        'animation.presented_interval_ms': {'n': 600, 'p95': 16.6},
                        'animation.repeated_percent': {'max': 0}, 'animation.vsync_latency_ms': {'p95': 1.5},
                        'status_roundtrip_ms': {'p95': 60}},
            'orb_prefetch': {'source_changes_per_s': 60, 'lifetime_errors': 0}}


class ThroughputGateTest(unittest.TestCase):
    def test_same_production_measurement_passes_absolute_and_relative_gates(self):
        d = measurement()
        self.assertTrue(throughput.evaluate(d, copy.deepcopy(d))['ok'])
        self.assertTrue(throughput.evaluate(d, d)['baseline_checked'])

    def test_rate_cadence_count_and_jank_fail_even_with_claimed_true_target(self):
        for field, statistic, value in [('animation.fps', 'median', 57.9),
                ('animation.refresh_interval_ms', 'n', 399), ('animation.refresh_interval_ms', 'p95', 20.001),
                ('animation.refresh_interval_ms', 'max', 33.401), ('animation.jank_percent', 'max', 1.001),
                ('animation.presented_interval_ms', 'n', 399), ('animation.presented_interval_ms', 'p95', 17.001),
                ('animation.repeated_percent', 'max', .501)]:
            with self.subTest(field=field, statistic=statistic):
                d = measurement(); d['summary'][field][statistic] = value
                self.assertFalse(throughput.evaluate(d)['ok'])
        for value in (57.9, float('nan'), float('inf'), True):
            d = measurement(); d['orb_prefetch']['source_changes_per_s'] = value
            self.assertFalse(throughput.evaluate(d)['ok'])

    def test_render_or_source_regression_fails_below_absolute_ceilings(self):
        baseline = measurement()
        for name, section, value in [('animation.render_work_ms', 'p95', 15.1),
                                     ('animation.refresh_work_ms', 'p95', 18.5)]:
            d = measurement(); d['summary'][name][section] = value
            self.assertFalse(throughput.target_failures(d))
            self.assertFalse(throughput.evaluate(d, baseline)['ok'])
        d = measurement(); d['orb_prefetch']['source_changes_per_s'] = 59
        self.assertFalse(throughput.target_failures(d))
        self.assertFalse(throughput.evaluate(d, baseline)['ok'])
        # Refresh starts drifting away from the frame boundary regress even while every frame is presented.
        d = measurement(); d['summary']['animation.vsync_latency_ms']['p95'] = 2.6
        self.assertFalse(throughput.target_failures(d))
        self.assertFalse(throughput.evaluate(d, baseline)['ok'])
        d = measurement(); d['summary']['animation.vsync_latency_ms']['p95'] = 2.4
        self.assertTrue(throughput.evaluate(d, baseline)['ok'])
        # Older firmware without presentation telemetry cannot pass the production gate.
        d = measurement()
        for key in ('animation.presented_interval_ms', 'animation.repeated_percent'):
            del d['summary'][key]
        self.assertTrue(throughput.target_failures(d))

    def test_incomplete_ab_old_protocol_and_pixel_errors_fail(self):
        for key, value in [('complete', False), ('restored', False), ('protocol', 'orb-60-stress-10s-v1'),
                           ('protocol', throughput.SOAK_PROTOCOL),
                           ('method', 'staged'), ('policy_sha256', 'different'), ('records', [])]:
            d = measurement(); d[key] = value
            self.assertFalse(throughput.evaluate(d)['ok'])
        for key in ('pixel_dma_errors', 'pixel_ppa_errors'):
            for value in (1, False, None):
                d = measurement(); d['records'][0]['status'][key] = value
                self.assertFalse(throughput.evaluate(d)['ok'])

    def test_soak_tolerates_one_hiccup_but_not_repeated_stalls_and_never_becomes_a_reference(self):
        def soak(late=0, repeated=0, latency_p99=2.0, n=7200, seconds=120, fallbacks=0):
            return {'schema': 1, 'protocol': throughput.SOAK_PROTOCOL, 'method': 'production', 'seconds': seconds,
                    'complete': True, 'restored': True, 'policy_sha256': throughput.policy_digest(),
                    'records': [{'status': {'pixel_dma_errors': 0, 'pixel_ppa_errors': 0}}],
                    'summary': {'animation.presented_interval_ms': {'n': n, 'p95': 16.57},
                                'animation.late_presentations': {'max': late},
                                'animation.repeated_frames': {'max': repeated},
                                'animation.vsync_latency_ms': {'p99': latency_p99}, 'animation.fps': {'median': 60.37}},
                    'orb_prefetch': {'source_changes_per_s': 60.37, 'lifetime_errors': 0, 'fallbacks': fallbacks}}
        self.assertTrue(throughput.evaluate_soak(soak())['ok'])
        self.assertTrue(throughput.evaluate_soak(soak(late=1, repeated=2))['ok'])
        for bad in (soak(late=2, repeated=4), soak(late=1, repeated=4), soak(latency_p99=4.1), soak(n=6999),
                    soak(seconds=119), soak(fallbacks=1), dict(soak(), method='linear'), dict(soak(), complete=False),
                    dict(soak(), protocol=throughput.PROTOCOL)):
            with self.subTest(failures=throughput.evaluate_soak(bad)['failures']):
                self.assertFalse(throughput.evaluate_soak(bad)['ok'])
        # The gate's evaluate() rejects a soak report, so it cannot be recorded as the reference.
        self.assertFalse(throughput.evaluate(soak(), None)['ok'])

    def test_baseline_policy_or_protocol_mismatch_fails(self):
        for key in ('protocol', 'policy_sha256'):
            baseline = measurement(); baseline[key] = 'wrong'
            self.assertFalse(throughput.evaluate(measurement(), baseline)['ok'])

    def test_explicit_reference_preserves_raw_report_and_never_overwrites(self):
        with tempfile.TemporaryDirectory() as tmp, patch('panel_throughput.PanelClient') as client:
            root = Path(tmp); source = root / 'raw.json'; baseline = root / 'reference.json'
            d = measurement(); d.pop('policy_sha256')  # Earlier immutable measurement, unchanged protocol/limits.
            source.write_text(json.dumps(d)); before = source.read_bytes()
            args = ['--check', str(source), '--baseline', str(baseline), '--record-baseline', str(baseline)]
            self.assertEqual(throughput.main(args), 0)
            saved = baseline.read_bytes()
            self.assertEqual(throughput.main(args), 1)
            self.assertEqual(baseline.read_bytes(), saved)
            self.assertEqual(source.read_bytes(), before)
            self.assertTrue(throughput.load_baseline(baseline)['reference_source_sha256'])
            client.assert_not_called()

    def test_missing_baseline_stops_before_device_access(self):
        with tempfile.TemporaryDirectory() as tmp, patch('panel_throughput.PanelClient') as client:
            self.assertEqual(throughput.main(['--require-target', '--baseline', str(Path(tmp)/'missing')]), 1)
            client.assert_not_called()


if __name__ == '__main__':
    unittest.main()
