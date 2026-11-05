"""Run the local orchestrator with simulated hardware adapters; failures must stop publication."""
from contextlib import ExitStack, nullcontext
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_ci as ci
import panel_lock
import panel_metrics
import panel_remote
import panel_recovery
import panel_ui_test
import panel_visual
import panel_responsiveness
import panel_throughput


class PipelineFlowTest(unittest.TestCase):
    def exercise(self, visual_ok=True, animation_ok=True, local_only=False, quarantined=False,
                 recovery=False, recovery_approved=True, brightness=False, responsiveness_ok=True, response_baseline_ok=True,
                 capacity_complete=True, production_fps=25, capacity_target=False, compare_methods=False,
                 capacity_baseline_ok=True, capacity_reference_ok=True, soak_results=(True,)):
        with tempfile.TemporaryDirectory() as tmp, ExitStack() as stack:
            root = Path(tmp)
            state, build = root / '.panel-ci', root / 'build'
            (state / 'baselines').mkdir(parents=True)
            (state / 'baselines/performance.json').write_text('{"workload":"idle"}')
            (state / 'baselines/animation.json').write_text('{"workload":"animation"}')
            (build / 'build').mkdir(parents=True)
            (build / 'build/firmware.ota.bin').write_bytes(b'verified-test-image')
            (build / 'build/firmware.elf').write_bytes(b'test-debug-symbols')
            if quarantined:
                (state / 'quarantine.json').write_text(json.dumps({
                    'reason': 'unresolved panic', 'report': str(state / 'run-failed/report.json')}))
            policy = root / 'policy.json'; policy.write_text('{"scenes":{}}')
            events = []
            status = {'page': 'page_hemmet', 'test_mode': 'idle', 'uptime_ms': 70000,
                      'orb_target_fps': production_fps,
                      'build_id': 'a' * 32, 'boot_id': 'test-boot', 'boot_healthy': True, 'reset_reason': 3,
                      'internal_free': 200000, 'psram_free': 16000000,
                      'voice_active': False, 'alarm_active': False, 'announcement_active': False,
                      'physical_touch_active': False}
            class Lock:
                def __init__(self, *a, **kw): pass
                def __enter__(self): return self
                def __exit__(self, *a): pass
                def subprocess_options(self): return {}
            class Client(Lock):
                def status(self): return dict(status)
                def exclusive(self, **kw): return nullcontext(self)
            def event(name, value=None):
                def call(*args, **kwargs):
                    events.append(name)
                    return value
                return call
            def command(argv, path, **kwargs):
                events.append('upload' if 'upload' in map(str, argv) else 'compile' if 'compile' in map(str, argv) else 'checks')
                Path(path).write_text('simulated successful command')
            def logs(folder, host, options, filename='runtime.log'):
                events.append(filename)
                (folder / filename).write_text('Successfully connected to hallpanel\n' + (
                    'Update complete\nRebooting safely\nSuccessfully connected to hallpanel\n'
                    if filename == 'boot.log' else ''))
                return nullcontext()
            def evaluate(metrics, baseline=None):
                events.append('metrics-evaluate')
                failed = baseline is not None and metrics.get('workload') == 'animation' and not animation_ok
                return {'ok': not failed, 'baseline_checked': baseline is not None}
            def authorization(*args):
                events.append('recovery-authorization')
                if not recovery_approved:
                    raise RuntimeError('Recovery requires explicit human authorization')
                return {'ok': True, 'artifact_folder': str(build / 'build')}
            def response_reference(*args):
                if not response_baseline_ok:
                    raise ValueError('Measured responsiveness baseline missing')
                return {'protocol': panel_responsiveness.PROTOCOL, 'complete': True}
            def capacity_reference(*args):
                if not capacity_reference_ok:
                    raise ValueError('Measured 60fps baseline missing')
                return {'complete': True}
            soak_verdicts = list(soak_results)
            def collect_throughput(client, folder, method='production', seconds=10):
                events.append('capacity' if seconds == 10 else 'soak')
                if seconds != 10:
                    return {'complete': True, 'restored': True, 'protocol': 'orb-60-virtual-soak-v1', 'seconds': seconds}
                return {'complete': capacity_complete, 'restored': True, 'target_met': capacity_target}
            def evaluate_soak(report):
                events.append('soak-check')
                return {'ok': soak_verdicts.pop(0) if soak_verdicts else True, 'failures': []}
            def smoke(*args, brightness=False):
                events.append('smoke')
                if brightness:
                    events.append('brightness-detents')
                return {'ok': True}
            replacements = [
                (ci, 'ROOT', root), (ci, 'STATE', state), (ci, 'BUILD', build),
                (ci, 'inventory', lambda: {'head': 'head', 'branch': 'main', 'build_id': 'a' * 32, 'files': {}}),
                (ci, 'unchanged', lambda *a: None), (ci, 'static_checks', event('static', {'ok': True})),
                (ci, 'command', command), (ci, 'backup_built', event('backup')),
                (panel_recovery, 'restart_linkage', event('restart-linkage', {'ok': True})),
                (panel_recovery, 'validate_plan', authorization),
                (panel_recovery, 'claim_attempt', event('recovery-attempt', {'attempt': 1})),
                (ci, 'recovery_support', lambda: {'two_ota_slots': True, 'rollback_configured': True,
                                                'watchdog_configured': True}),
                (ci, 'publish_preflight', event('publish-preflight')),
                (ci, 'publish', event('publish', 'new-head')),
                (ci, 'wait_boot', event('boot', {'status': dict(status)})),
                (ci, 'runtime_logs', logs), (panel_lock, 'PanelLock', Lock),
                (panel_remote, 'PanelClient', Client), (panel_visual, 'POLICY_PATH', policy),
                (panel_visual, 'load_capture', lambda *a, **kw: {'scenes': {'settings': {}}}),
                (panel_visual, 'capture', event('visual-capture')),
                (panel_visual, 'compare', event('visual-compare', {'ok': visual_ok})),
                (panel_ui_test, 'run', smoke),
                (panel_ui_test, 'run_test_mode', event('synthetic')),
                (panel_metrics, 'collect', event('metrics', {'workload': 'idle'})),
                (panel_metrics, 'collect_animation', event('animation', {'workload': 'animation'})),
                (panel_metrics, 'evaluate', evaluate),
                (panel_responsiveness, 'collect', event('responsiveness', {})),
                (panel_responsiveness, 'load_baseline', response_reference),
                (panel_responsiveness, 'evaluate', event('responsiveness-evaluate', {'ok': responsiveness_ok, 'baseline_checked': True})),
                (panel_throughput, 'collect', collect_throughput),
                (panel_throughput, 'load_baseline', capacity_reference),
                (panel_throughput, 'evaluate', event('capacity-evaluate', {'ok': capacity_baseline_ok, 'baseline_checked': True})),
                (panel_throughput, 'evaluate_soak', evaluate_soak),
            ]
            for target, name, value in replacements:
                stack.enter_context(patch.object(target, name, value))
            args = SimpleNamespace(local_only=local_only, publish=not local_only and not recovery,
                                   files=['source.py'], message='Test release',
                                   url='http://example.invalid', device='example.invalid', repair_argv=None,
                                   recovery_plan=root / 'plan.json' if recovery else None, brightness_drag=brightness,
                                   compare_orb_methods=compare_methods)
            result = ci.pipeline(args)
            report = json.loads(next(state.glob('run-*/report.json')).read_text())
            report['_quarantine_present'] = (state / 'quarantine.json').exists()
            verified = list((state / 'verified').glob('*/verification-*.json'))
            return result, report, events, bool(verified)

    def test_successful_physical_sequence_publishes_only_after_all_gates(self):
        result, report, events, verified = self.exercise()
        self.assertEqual(result, 0)
        self.assertTrue(report['ok'])
        self.assertTrue(report['published'])
        self.assertTrue(verified)
        for first, second in [('checks', 'compile'), ('backup', 'compile'), ('compile', 'upload'),
                              ('upload', 'boot'), ('boot', 'smoke'), ('smoke', 'synthetic'),
                              ('visual-compare', 'metrics'), ('metrics', 'animation'),
                              ('animation', 'responsiveness'), ('responsiveness', 'capacity'), ('capacity', 'publish'),
                              ('boot.log', 'upload'), ('boot', 'runtime.log')]:
            self.assertLess(events.index(first), events.index(second))

    def test_requested_brightness_detents_run_before_performance_gates(self):
        result, report, events, _ = self.exercise(brightness=True)
        self.assertEqual(result, 0)
        self.assertTrue(report['brightness_drag_requested'])
        self.assertLess(events.index('brightness-detents'), events.index('metrics'))
        _, local_report, local_events, _ = self.exercise(local_only=True, brightness=True)
        self.assertNotIn('brightness-detents', local_events)

    def test_visual_failure_writes_failure_report_and_never_measures_or_publishes(self):
        result, report, events, verified = self.exercise(visual_ok=False)
        self.assertEqual(result, 1)
        self.assertFalse(report['ok'])
        self.assertFalse(report['published'])
        self.assertFalse(verified)
        self.assertNotIn('publish', events)
        self.assertNotIn('metrics', events)
        self.assertIn('Visual regression', report['error'])
        self.assertTrue(report['_quarantine_present'])

    def test_animation_regression_stops_verification_and_publication(self):
        result, report, events, verified = self.exercise(animation_ok=False)
        self.assertEqual(result, 1)
        self.assertFalse(report['ok'])
        self.assertFalse(report['published'])
        self.assertFalse(verified)
        self.assertIn('animation', events)
        self.assertNotIn('publish', events)
        self.assertIn('Animation performance regression', report['error'])

    def test_local_only_compiles_without_device_access_or_publication(self):
        result, report, events, verified = self.exercise(local_only=True)
        self.assertEqual(result, 0)
        self.assertTrue(report['ok'])
        self.assertFalse(report['published'])
        self.assertFalse(verified)

        self.assertIn('compile', events)
        for event in ('boot.log', 'upload', 'smoke', 'synthetic', 'visual-capture', 'animation', 'publish', 'responsiveness'):
            self.assertNotIn(event, events)
        self.assertIn('physical gates not run', report['verification'])

    def test_incomplete_capacity_diagnostic_stops_but_unmet_target_stays_explicit(self):
        result, report, events, verified = self.exercise(capacity_complete=False)
        self.assertEqual(result, 1)
        self.assertFalse(verified)
        self.assertNotIn('publish', events)
        self.assertIn('capacity measurement incomplete', report['error'])
        result, report, _, _ = self.exercise()
        self.assertEqual(result, 0)
        capacity = next(s['result'] for s in report['stages'] if s['name'] == '60fps-capacity-diagnostic')
        self.assertFalse(capacity['target_met'])

    def test_production_sixty_fps_requires_the_actual_source_and_cadence_target(self):
        result, report, events, verified = self.exercise(production_fps=60)
        self.assertEqual(result, 1)
        self.assertFalse(verified)
        self.assertNotIn('publish', events)
        self.assertIn('production 60fps target not met', report['error'])
        result, report, events, _ = self.exercise(production_fps=60, capacity_target=True)
        self.assertEqual(result, 0)
        self.assertTrue(report['ok'])
        self.assertEqual(events.count('soak'), 1)
        self.assertLess(events.index('capacity-evaluate'), events.index('soak'))

    def test_soak_measures_once_more_and_stops_only_when_stalls_repeat(self):
        result, report, events, verified = self.exercise(production_fps=60, capacity_target=True, soak_results=(False, True))
        self.assertEqual(result, 0)
        self.assertTrue(verified)
        self.assertEqual(events.count('soak'), 2)
        result, report, events, verified = self.exercise(production_fps=60, capacity_target=True, soak_results=(False, False))
        self.assertEqual(result, 1)
        self.assertFalse(verified)
        self.assertNotIn('publish', events)
        self.assertEqual(events.count('soak'), 2)
        self.assertIn('soak failed twice', report['error'])
        self.assertTrue(report['_quarantine_present'])
        # A 25 fps firmware has no production soak.
        _, _, events, _ = self.exercise()
        self.assertNotIn('soak', events)

    def test_responsiveness_regression_stops_publication(self):
        result, report, events, verified = self.exercise(responsiveness_ok=False)
        self.assertEqual(result, 1)
        self.assertIn('responsiveness', events)
        self.assertNotIn('publish', events)
        self.assertFalse(verified)
        self.assertTrue(report['_quarantine_present'])

    def test_relative_sixty_fps_regression_stops_even_if_absolute_target_passes(self):
        result, report, events, verified = self.exercise(production_fps=60, capacity_target=True, capacity_baseline_ok=False)
        self.assertEqual(result, 1)
        self.assertFalse(verified)
        self.assertNotIn('publish', events)
        self.assertIn('60fps regression', report['error'])
        self.assertTrue(report['_quarantine_present'])

    def test_method_comparison_does_not_replace_the_mandatory_production_gate(self):
        result, report, events, verified = self.exercise(production_fps=60,compare_methods=True)
        self.assertEqual(events.count('capacity'),3)
        self.assertEqual(result,1)
        self.assertFalse(verified)
        self.assertIn('production 60fps target not met',report['error'])

    def test_missing_responsiveness_reference_stops_before_upload(self):
        result, report, events, verified = self.exercise(response_baseline_ok=False)
        self.assertEqual(result, 1)
        self.assertFalse(verified)
        self.assertNotIn('upload', events)
        self.assertFalse(report['_quarantine_present'])

    def test_missing_sixty_fps_reference_stops_before_upload(self):
        result, report, events, verified = self.exercise(capacity_reference_ok=False)
        self.assertEqual(result, 1)
        self.assertFalse(verified)
        self.assertNotIn('upload', events)
        self.assertFalse(report['_quarantine_present'])

    def test_quarantine_stops_before_checks_upload_or_publish(self):
        result, report, events, verified = self.exercise(quarantined=True)
        self.assertEqual(result, 1)
        self.assertIn('quarantined', report['error'])
        self.assertEqual(events, [])
        self.assertFalse(verified)

    def test_quarantine_allows_free_local_checks_only(self):
        result, report, events, verified = self.exercise(quarantined=True, local_only=True)
        self.assertEqual(result, 0)
        self.assertIn('compile', events)
        self.assertNotIn('upload', events)
        self.assertFalse(verified)

    def test_approved_recovery_keeps_all_gates_and_never_publishes_or_clears_quarantine(self):
        result, report, events, verified = self.exercise(quarantined=True, recovery=True)
        self.assertEqual(result, 0)
        self.assertTrue(verified)
        self.assertTrue(report['_quarantine_present'])
        self.assertEqual(report['recovery'], {'attempt': 1})
        for item in ('checks', 'compile', 'restart-linkage', 'smoke', 'synthetic', 'visual-compare', 'metrics', 'animation'):
            self.assertIn(item, events)
        self.assertLess(events.index('recovery-attempt'), events.index('upload'))
        self.assertNotIn('publish', events)

    def test_recovery_without_approval_stops_before_checks_and_device_access(self):
        result, report, events, verified = self.exercise(quarantined=True, recovery=True, recovery_approved=False)
        self.assertEqual(result, 1)
        self.assertFalse(verified)
        self.assertEqual(events, ['recovery-authorization'])
        self.assertIn('explicit human authorization', report['error'])

    def test_final_health_rejects_changed_boot_even_when_uptime_increases(self):
        class Client:
            def status(self): return {'uptime_ms': 90000, 'build_id': 'same', 'boot_id': 'new-boot'}
        before = {'uptime_ms': 70000, 'build_id': 'same', 'boot_id': 'old-boot'}
        with self.assertRaisesRegex(RuntimeError, 'restarted'):
            ci.final_health(Client(), before)


if __name__ == '__main__':
    unittest.main()
