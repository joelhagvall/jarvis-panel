"""Exact workload timing, loss detection and gates, without a panel or paid services."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_responsiveness as response


def record(count, values, boot='one'):
    return {'status': {'boot_id': boot, 'uptime_ms': count * 1000},
            'timing': {key: {'total': count, 'us': values} for key in response.SERIES}}


class ResponsivenessTests(unittest.TestCase):
    def test_new_samples_only_and_no_repeated_old_tail(self):
        self.assertEqual(response.delta(record(3, [900, 800, 700]), record(5, [900, 800, 700, 10, 20]), 'refresh_work'), [10, 20])
        self.assertEqual(response.delta(record(3, [900, 800, 700]), record(3, [900, 800, 700]), 'refresh_work'), [])
        for later in (record(2, [1, 2]), record(132, [1] * 128), record(4, [1, 2, 3, 4], 'new')):
            with self.assertRaises(RuntimeError):
                response.delta(record(3, [1, 2, 3]), later, 'refresh_work')

    def test_validates_bounded_finite_raw_values(self):
        raw = {'responsiveness': {'schema': 1, **record(1, [42])['timing']}}
        self.assertEqual(response.telemetry(raw)['remote_press']['us'], [42])
        for values in ([True], [-1], [2**32], [], [float('nan')]):
            bad = copy.deepcopy(raw)
            bad['responsiveness']['remote_press']['us'] = values
            with self.assertRaises(ValueError): response.telemetry(bad)
        with self.assertRaises(RuntimeError): response.telemetry({})

    def good_report(self):
        policy = json.loads(response.POLICY.read_text())
        summary = {key: response.stats([10] * n) for key, n in policy['minimum_samples'].items()}
        summary.update({'animation.fps': response.stats([24.5]), 'animation.jank_percent': response.stats([0]),
                        'navigation.command_roundtrip_ms': response.stats([120] * 20),
                        'status_roundtrip_ms': response.stats([60] * 20)})
        return {'schema': 1, 'protocol': response.PROTOCOL, 'policy_sha256': 'same',
                'complete': True, 'summary': summary}

    def test_absolute_relative_missing_and_changed_policy_fail_closed(self):
        baseline = self.good_report()
        self.assertTrue(response.evaluate(baseline, baseline)['ok'])
        for change in ('missing', 'slow', 'incomplete', 'few', 'policy'):
            report = copy.deepcopy(baseline)
            if change == 'missing': del report['summary']['animation.refresh_work_ms']
            if change == 'slow': report['summary']['navigation.remote_release_ms']['p95'] = 40
            if change == 'incomplete': report['complete'] = False
            if change == 'few': report['summary']['navigation.remote_release_ms']['n'] = 1
            if change == 'policy': report['policy_sha256'] = 'different'
            with self.subTest(change=change): self.assertFalse(response.evaluate(report, baseline)['ok'])

    def test_release_is_also_split_by_target_page(self):
        def nav(count, values, page):
            r = record(count, values)
            r.update(stage='navigation', status={**r['status'], 'page': page})
            return r
        records = [nav(1, [1000], 'page_glass_v2'), nav(2, [1000, 50000], 'page_hemmet'),
                   nav(3, [1000, 50000, 70000], 'page_glass_v2'), nav(4, [1000, 50000, 70000, 52000], 'page_hemmet')]
        result = response.summarize(records, json.loads(response.POLICY.read_text()))
        self.assertEqual(result['navigation.remote_release_ms']['n'], 3)
        self.assertEqual(result['navigation.remote_release_to_page_hemmet_ms']['median'], 51.0)
        self.assertEqual(result['navigation.remote_release_to_page_glass_v2_ms']['max'], 70.0)
        # The split must not swallow the animation stage's fps and jank (it did, 2026-10-30: the gate failed).
        anim = [{**record(n, list(range(1, n + 1))), 'stage': 'animation'} for n in (1, 5, 9)]
        result = response.summarize(anim, json.loads(response.POLICY.read_text()))
        self.assertIn('animation.fps', result)
        self.assertIn('animation.jank_percent', result)

    def test_nearest_rank_tail_is_not_average(self):
        result = response.stats([1] * 98 + [50, 200])
        self.assertEqual((result['median'], result['p95'], result['p99'], result['max']), (1, 1, 50, 200))

    def test_baseline_requires_complete_current_protocol_and_policy(self):
        report = self.good_report()
        report['policy_sha256'] = hashlib.sha256(response.POLICY.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'baseline.json'
            with self.assertRaises(ValueError): response.load_baseline(path)
            path.write_text(json.dumps(report))
            self.assertEqual(response.load_baseline(path)['protocol'], response.PROTOCOL)
            for change in ({'complete': False}, {'policy_sha256': 'stale'}, {'protocol': 'old'}):
                path.write_text(json.dumps({**report, **change}))
                with self.assertRaises(ValueError): response.load_baseline(path)

    def test_missing_baseline_never_starts_device_work(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(response, 'PanelClient') as client:
            code = response.main(['--baseline', str(Path(tmp) / 'missing.json'), '--out', str(Path(tmp) / 'out')])
            self.assertEqual(code, 1)
            client.assert_not_called()

    def test_fresh_touch_and_observer_cost_have_absolute_gates_without_a_baseline(self):
        for field, value in (('navigation.remote_release_ms', 130), ('status_main_us', 6000)):
            report = self.good_report()
            report['summary'][field]['p95'] = value
            self.assertFalse(response.evaluate(report)['ok'])

    def test_prefetch_uses_workload_deltas_and_rejects_errors_resets_and_partial_support(self):
        def entry(stage, hits, misses, errors=0):
            return {'stage': stage, 'status': {'internal_free': 300000, 'psram_free': 19000000,
                    'orb_prefetch_hits': hits, 'orb_prefetch_fallbacks': misses,
                    'orb_prefetch_decoded': hits + misses, 'orb_prefetch_errors': errors,
                    'orb_prefetch_bytes': 435600}}
        records = [entry('navigation', 1000, 200), entry('animation', 1010, 205), entry('animation', 1259, 206)]
        result = response.prefetch_summary(records)
        self.assertEqual((result['hits'], result['fallbacks'], result['decoded'], result['resident_bytes']),
                         (249, 1, 250, 435600))
        self.assertEqual(result['hit_percent'], 99.6)
        self.assertEqual(response.prefetch_summary([]), {'supported': False})
        for bad in ('decode', 'reset', 'missing', 'invalid', 'resize'):
            report = self.good_report()
            report['records'] = copy.deepcopy(records)
            status = report['records'][-1]['status']
            if bad == 'decode': status['orb_prefetch_errors'] = 1
            if bad == 'reset': status['orb_prefetch_hits'] = 1
            if bad == 'missing': del status['orb_prefetch_bytes']
            if bad == 'invalid': status['orb_prefetch_hits'] = True
            if bad == 'resize': status['orb_prefetch_bytes'] = 0
            with self.subTest(bad=bad): self.assertFalse(response.evaluate(report)['ok'])

    def test_source_changes_do_not_count_repeated_draws_as_new_animation_frames(self):
        def entry(changes, uptime):
            return {'stage': 'animation', 'status': {'orb_prefetch_hits': uptime, 'orb_prefetch_fallbacks': 0,
                    'orb_prefetch_decoded': uptime, 'orb_prefetch_errors': 0, 'orb_prefetch_bytes': 435600,
                    'orb_source_changes': changes, 'uptime_ms': uptime}}
        result = response.prefetch_summary([entry(100, 1000), entry(125, 2000)])
        self.assertEqual(result['source_changes_per_s'], 25)
        self.assertEqual(result['hits'], 1000)
        with self.assertRaises(ValueError): response.prefetch_summary([entry(100, 1000), entry(90, 2000)])

    def test_presentation_counts_repeated_frames_from_display_boundaries(self):
        def shown(count, presented, total, us, uptime, age=300):
            return {'stage': 'animation', 'status': {'boot_id': 'b', 'uptime_ms': uptime},
                    'timing': {key: {'total': 0, 'us': []} for key in response.SERIES},
                    'presentation': {'vsync_total': count, 'presented_total': presented, 'vsync_age_us': age,
                                     'total': total, 'us': us}}
        raw = {'presentation': {'schema': 1, 'vsync_total': 10, 'presented_total': 9, 'vsync_age_us': 300,
                                'presented_interval': {'total': 8, 'us': [16560] * 8}}}
        self.assertEqual(response.presentation(raw)['us'], [16560] * 8)
        self.assertIsNone(response.presentation({}))
        for bad in ({'schema': 2}, {'presented_total': 11}, {'vsync_total': -1},
                    {'presented_interval': {'total': 8, 'us': [16560] * 9}},
                    {'presented_interval': {'total': 8, 'us': [True] * 8}}, {'presented_interval': None}):
            broken = copy.deepcopy(raw); broken['presentation'].update(bad)
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError): response.presentation(broken)
        records = [shown(1000, 990, 900, [16560] * 128, 10000),
                   shown(1060, 1049, 959, [16560] * 127 + [33120], 11000),
                   shown(1120, 1109, 1019, [16560] * 128, 12000)]
        summary = response.summarize(records, {'frame_budget_ms': 25})
        self.assertEqual(summary['animation.presented_interval_ms']['n'], 119)
        self.assertEqual(summary['animation.presented_interval_ms']['max'], 33.12)
        self.assertEqual(summary['animation.repeated_frames']['max'], 1)
        self.assertEqual(summary['animation.late_presentations']['max'], 1)
        self.assertAlmostEqual(summary['animation.repeated_percent']['max'], 100 / 120)
        self.assertEqual(summary['animation.vsync_per_s']['max'], 60)
        self.assertEqual(summary['animation.presented_per_s']['max'], 59.5)
        self.assertNotIn('animation.presented_interval_ms', response.summarize(records[:1] + [
            {k: v for k, v in records[1].items() if k != 'presentation'}], {'frame_budget_ms': 25}))
        with self.assertRaises(RuntimeError):
            response.presentation_delta(records[0], shown(1200, 1190, 1100, [16560] * 128, 13000))
        with self.assertRaises(RuntimeError):
            response.presentation_delta(records[0], shown(999, 990, 900, [16560] * 128, 13000))

    def test_hardware_copy_errors_fail_without_a_new_or_weaker_baseline(self):
        report = self.good_report()
        report['records'] = [{'status': {'internal_free': 300000, 'psram_free': 19000000, 'pixel_dma_errors': 1}}]
        self.assertFalse(response.evaluate(report)['ok'])
        report['records'][0]['status'] = {'internal_free': 300000, 'psram_free': 19000000, 'pixel_ppa_errors': 1}
        self.assertFalse(response.evaluate(report)['ok'])

    def test_guard_refuses_unknown_activity_real_touch_and_reset(self):
        original = {'boot_id': 1, 'build_id': 'a', 'uptime_ms': 90000, 'physical_touch_idle_ms': 10000,
                    'test_mode': 'idle', 'wifi_connected': True, 'voice_active': False,
                    'announcement_active': False, 'alarm_active': False, 'physical_touch_active': False}
        response.guard(original, original)
        for change in ({'voice_active': True}, {'voice_active': None}, {'boot_id': 2},
                       {'test_mode': 'speaking'}, {'wifi_connected': False},
                       {'uptime_ms': 91000, 'physical_touch_idle_ms': 500}):
            with self.subTest(change=change), self.assertRaises(RuntimeError):
                response.guard({**original, **change}, original)

    def test_collector_restores_page_and_saves_evidence_on_success_and_disconnect(self):
        for fail in (False, True):
            clock = [100.0]
            class Client:
                page, frames, touches, taps = 'page_hemmet', 0, 0, 0
                mode = 'idle'
                def status(self):
                    clock[0] += .05
                    self.frames += 32
                    self.last_transport = {'request_ms': 1, 'headers_ms': 30, 'body_ms': 2}
                    timing = {key: {'total': self.frames if key.startswith('refresh_') else self.touches if key.startswith('remote_') else 0,
                                    'us': [40000 if key == 'refresh_interval' else 15000] * min(128, self.frames if key.startswith('refresh_') else self.touches if key.startswith('remote_') else 0)}
                              for key in response.SERIES}
                    return {'page': self.page, 'test_mode': self.mode, 'boot_id': 1, 'build_id': 'a',
                            'uptime_ms': int(clock[0] * 1000), 'physical_touch_idle_ms': int(clock[0] * 1000),
                            'wifi_connected': True, 'voice_active': False, 'alarm_active': False,
                            'announcement_active': False, 'physical_touch_active': False,
                            'internal_free': 300000, 'psram_free': 19000000,
                            'status_main_us': 100, 'status_worker_us': 30000,
                            'status_radio_us': 29000, 'status_main_heap_us': 50,
                            'responsiveness': {'schema': 1, **timing}}
                def inspect(self, folder): pass
                def ui(self):
                    return {'page': self.page, 'widgets': [
                        {'text': 'Hemmet', 'x': 0, 'y': 0, 'width': 20, 'height': 20},
                        {'text': 'Gå hemifrån', 'x': 20, 'y': 0, 'width': 20, 'height': 20}]}
                def tap_text(self, text):
                    self.page = 'page_hemmet' if text == 'Hemmet' else 'page_glass_v2'
                def tap(self, x, y, **kwargs):
                    self.taps += 1
                    self.touches += 1
                    self.page = 'page_hemmet' if x == 10 else 'page_glass_v2'
                    self.last_transport = {'request_ms': 3, 'headers_ms': 70, 'body_ms': 4}
                    if fail and self.taps == 3: raise OSError('ambiguous disconnect')
                def test_mode(self, mode, duration_ms):
                    self.mode = 'idle' if mode == 'restore' else mode
                    self.page = 'page_glass_v2' if mode == 'restore' else 'page_orb'
            client = Client()
            with tempfile.TemporaryDirectory() as tmp, \
                    patch.object(response.time, 'monotonic', side_effect=lambda: clock[0]), \
                    patch.object(response.time, 'sleep', side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
                folder = Path(tmp) / 'run'
                if fail:
                    with self.assertRaises(OSError): response.collect(client, folder)
                    self.assertEqual(client.taps, 3)  # No ambiguous input retry.
                else:
                    measured = response.collect(client, folder)
                    self.assertEqual(measured['summary']['navigation.remote_release_ms']['n'], 20)
                    self.assertEqual(measured['summary']['status.transport.headers_ms']['max'], 30)
                    self.assertEqual(measured['summary']['command.transport.headers_ms']['max'], 70)
                    self.assertFalse(measured['orb_prefetch']['supported'])
                saved = json.loads((folder / 'responsiveness.json').read_text())
                self.assertEqual(saved['complete'], not fail)
                self.assertTrue(saved['restored'])
                self.assertEqual(client.page, 'page_hemmet')
                self.assertEqual((folder / 'responsiveness.json').stat().st_mode & 0o777, 0o600)

    def test_native_recorder_ignores_empty_refresh_keeps_slow_samples_and_wrap(self):
        source = r'''
#include "bench/responsiveness.h"
#include <cassert>
#include <iostream>
#include <thread>
int main() {
  responsiveness::Recorder r;
  responsiveness::Recorder phases;
  phases.begin(100); phases.wait_begin(110); phases.wait_finish(150);
  phases.render_begin(160); phases.submitted(5); phases.render_finish(190); phases.finish(200);
  assert(phases.layout_sync.us[0] == 60 && phases.render_work.us[0] == 30);
  assert(phases.flush_wait.us[0] == 40 && phases.flush_submit.us[0] == 5);
  // The slowest render of the last two seconds keeps its areas; a faster one later does not replace it.
  phases.begin(1000); phases.render_begin(1010); phases.area(0, 0, 9, 9); phases.area(100, 50, 199, 59);
  phases.render_finish(1050); phases.finish(1060);
  assert(phases.slowest.render_us == 40 && phases.slowest.areas == 2 && phases.slowest.pixels == 1100);
  assert(phases.slowest.rects[1][0] == 100 && phases.slowest.rects[1][3] == 59);
  phases.begin(2000); phases.render_begin(2001); phases.area(0, 0, 0, 0); phases.render_finish(2002); phases.finish(2003);
  assert(phases.slowest.render_us == 40 && phases.slowest.areas == 2);
  phases.begin(3000000); phases.render_begin(3000001); phases.render_finish(3000003); phases.finish(3000004);
  assert(phases.slowest.render_us == 2 && phases.slowest.areas == 0);   // the window moved on
  r.touch(0, true, false);
  r.begin(10); r.finish(20);
  assert(r.remote_press.total == 0 && r.refresh_work.total == 0);
  assert(r.empty_refresh_work.total == 1 && r.empty_refresh_work.us[0] == 10);  // Buffer sync stays visible.
  r.begin(30); r.rendered(); r.finish(900000);
  assert(r.remote_press.us[0] == 900000); // Never discard >500 ms as the old bench did.
  assert(r.refresh_work.us[0] == 899970);
  r.touch(0xfffffff0U, false, true);
  r.begin(0xfffffff5U); r.rendered(); r.finish(20);
  assert(r.physical_release.us[0] == 36);
  assert(r.remote_release.total == 0);
  const auto frozen = r.deferred_json();
  const auto expected = frozen();
  std::thread worker([&]() { for (unsigned i=0;i<1000;++i) assert(frozen()==expected); });
  for (unsigned i=0;i<10000;++i) r.refresh_work.add(i);
  worker.join();  // Formatter reads a frozen copy while the owner keeps recording.
  responsiveness::Series s;
  for (unsigned i=0;i<300;++i) s.add(i);
  std::cout << s.json();
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'test.cpp').write_text(source)
            subprocess.run(['c++', '-std=c++17', '-pthread', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I', str(ROOT), str(path / 'test.cpp'), '-o', str(path / 'test')], check=True, capture_output=True)
            data = json.loads(subprocess.check_output([str(path / 'test')]))
        self.assertEqual(data, {'total': 300, 'us': list(range(172, 300))})

    def test_host_latency_failure_points_to_the_host_link_check(self):
        hint = response.host_latency_hint(['status_roundtrip_ms regressed against measured baseline'])
        self.assertIn('tools/panel host-link', hint)
        self.assertIn('tools/panel host-link', response.host_latency_hint(
            ['navigation.command_roundtrip_ms.p95 exceeds 750 or is unavailable']))
        self.assertIsNone(response.host_latency_hint(['animation.refresh_work_ms regressed against measured baseline']))


if __name__ == '__main__':
    unittest.main()


class CompareRunsTest(unittest.TestCase):
    """--compare reads two saved runs only: release and render-work series side by side."""

    def test_compare_two_saved_runs(self):
        with tempfile.TemporaryDirectory() as tmp:
            runs = []
            for name, median in (('a', 66.7), ('b', 59.1)):
                folder = Path(tmp) / name
                folder.mkdir()
                series = {'n': 20, 'median': median, 'p95': median + 5, 'max': median + 6}
                (folder / 'report.json').write_text(json.dumps({'summary': {
                    'navigation.remote_release_ms': series, 'navigation.render_work_ms': series,
                    'animation.fps': {'median': 25}}}))
                runs.append(folder)
                (folder / 'responsiveness.json').write_text(json.dumps({'samples': [
                    {'status': {'internal_free': 121000 if name == 'a' else 97004, 'psram_free': 8000000}},
                    {'status': {'internal_free': 125000, 'psram_free': 7900000}}]}))
            rows = response.compare_runs(*runs)
            self.assertEqual(set(rows), {'navigation.remote_release_ms', 'navigation.render_work_ms',
                                         'lowest.internal_free', 'lowest.psram_free'})
            self.assertEqual(rows['lowest.internal_free'], {'before': 121000, 'after': 97004})
            self.assertEqual((rows['navigation.remote_release_ms']['before']['median'],
                              rows['navigation.remote_release_ms']['after']['median']), (66.7, 59.1))
