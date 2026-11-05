"""Free soak-report checks on a synthetic health history; never contacts the panel or Jarvis."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import panel_soak as soak

T0 = 1_793_000_000.0


def sample(i, boot='a', uptime_s=None, reason=1, **status):
    t = T0 + i * 300
    base = {'boot_id': boot, 'uptime_ms': int((uptime_s if uptime_s is not None else 60 + i * 300) * 1000),
            'reset_reason': reason, 'build_id': 'b1', 'page': 'page_glass_v2', 'internal_free': 200_000,
            'internal_min_free': 150_000, 'psram_free': 16_000_000, 'psram_min_free': 15_000_000,
            'wifi_rssi_dbm': -50, 'wifi_connected': True, 'render_avg_ms': 0, 'touch_refresh_p95_ms': 0}
    return {'time': t, 'ok': True, 'status': {**base, **status}}


class SoakReport(unittest.TestCase):
    def test_counts_restarts_crashes_failures_and_gaps(self):
        entries = [sample(i) for i in range(10)]
        entries.append({'time': T0 + 10 * 300, 'ok': False, 'error_type': 'TimeoutError'})
        entries.append({'time': T0 + 11 * 300, 'ok': False, 'error_type': 'TimeoutError'})
        # Panic boot, then a software restart after a 40-minute hole in the monitor.
        entries += [sample(i, boot='b', uptime_s=(i - 12) * 300 + 30, reason=4) for i in range(12, 15)]
        entries += [sample(i, boot='c', uptime_s=(i - 23) * 300 + 30, reason=3) for i in range(23, 25)]
        r = soak.report({'entries': entries, 'active_alerts': []}, now=entries[-1]['time'])
        self.assertEqual(r['failed_reads'], 2)
        self.assertEqual(r['unreachable'][0]['samples'], 2)
        self.assertEqual(len(r['monitor_gaps']), 1)
        self.assertEqual(r['restarts'], 3)  # the first boot started inside the window (uptime 60 s)
        self.assertEqual(r['crashes'], 1)
        self.assertEqual(r['restart_reasons'], {'power-on': 1, 'panic': 1, 'software': 1})
        self.assertGreaterEqual(r['boots'][2]['down_minutes_max'], 40)
        self.assertIn('CRASH panic', soak.render(r))

    def test_a_boot_older_than_the_window_is_not_a_restart(self):
        entries = [sample(i, uptime_s=86400 + i * 300) for i in range(5)]
        r = soak.report({'entries': entries}, now=entries[-1]['time'])
        self.assertEqual((r['restarts'], r['crashes']), (0, 0))
        self.assertFalse(r['boots'][0]['new_in_window'])

    def test_idle_zero_timings_are_not_frames_and_drift_needs_six_hours(self):
        entries = [sample(i, internal_free=200_000 - i * 100) for i in range(80)]
        entries[5]['status']['touch_refresh_p95_ms'] = 18.0
        r = soak.report({'entries': entries}, now=entries[-1]['time'])
        self.assertEqual(r['metrics']['touch_refresh_p95_ms']['samples'], 1)
        self.assertIsNone(r['metrics']['render_avg_ms'])
        self.assertLess(r['boots'][0]['internal_free_drift'], -6000)
        short = soak.report({'entries': entries[:30]}, now=entries[29]['time'])
        self.assertIsNone(short['boots'][0]['internal_free_drift'])

    def test_days_window_and_empty_window(self):
        entries = [sample(i) for i in range(600)]  # a little over two days
        r = soak.report({'entries': entries}, days=1, now=entries[-1]['time'])
        self.assertLessEqual(r['window']['samples'], 289)
        with self.assertRaises(ValueError):
            soak.report({'entries': entries}, days=1, now=entries[-1]['time'] + 5 * 86400)

    def test_low_memory_floors_are_flagged_like_the_push(self):
        entries = [sample(i, internal_min_free=8 * 1024) for i in range(3)]
        r = soak.report({'entries': entries}, now=entries[-1]['time'])
        self.assertEqual(r['low_floor_boots'], 1)
        self.assertIn('LOW MEMORY', soak.render(r))

    def test_crash_reasons_are_the_pushed_ones(self):
        import panel_health
        self.assertIs(soak.CRASH_REASONS, panel_health.CRASH_REASONS)


if __name__ == '__main__':
    unittest.main()
