"""Recovery grants bind actual artifacts and stop after failure, interruption or two attempts."""
from contextlib import ExitStack
import json
from pathlib import Path
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_ci as ci
import panel_recovery as recovery


class RecoveryTest(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory())).resolve()
        self.state = self.root / '.panel-ci'
        self.run = self.state / 'run-local'
        self.run.mkdir(parents=True)
        (self.root / 'diagnosis.md').write_text('Confirmed cause and bounded correction\n')
        guard = self.root / 'components/panel_restart/restart_guard.cpp'
        guard.parent.mkdir(parents=True)
        guard.write_text('checked restart guard\n')
        (self.state / 'quarantine.json').write_text('{"reason":"restart panic"}')
        (self.run / 'firmware.ota.bin').write_bytes(b'checked firmware')
        (self.run / 'firmware.elf').write_bytes(b'checked debug symbols')
        self.candidate = {'build_id': 'a'*32, 'head': 'head', 'branch': 'main',
                          'files': {'diagnosis.md': {'sha256': recovery.sha(self.root / 'diagnosis.md')},
                                    'components/panel_restart/restart_guard.cpp': {'sha256': recovery.sha(guard)}}}
        (self.run / 'candidate.json').write_text(json.dumps(self.candidate))
        self.dump(self.run / 'report.json', {'ok': True, 'mode': 'local-only',
                  'build_id': 'a'*32, 'firmware_sha256': recovery.sha(self.run / 'firmware.ota.bin')})
        self.plan = self.state / 'recovery.json'
        self.stack.enter_context(patch.object(ci, 'ROOT', self.root))
        self.stack.enter_context(patch.object(ci, 'STATE', self.state))
        self.stack.enter_context(patch.object(ci, 'inventory', lambda: self.candidate))
        self.stack.enter_context(patch.object(ci, 'unchanged', lambda candidate: None))
        self.stack.enter_context(patch.object(recovery, 'restart_linkage', lambda elf:
                                            {'ok': True, 'elf_sha256': recovery.sha(elf)}))
        recovery.prepare(self.run, self.root / 'diagnosis.md', self.plan)

    def dump(self, path, value):
        path.write_text(json.dumps(value))

    def approved(self):
        recovery.approve(self.plan, 'Test fixture: human approved exactly this bounded plan')

    def claim(self, number):
        run = self.state / f'run-physical-{number}'
        run.mkdir()
        for name in ('firmware.ota.bin', 'firmware.elf'):
            (run / name).write_bytes((self.run / name).read_bytes())
        report = run / 'report.json'
        return report, recovery.claim_attempt(self.plan, self.candidate, report)

    def passed(self, report, claim, *, ok=True, reset=3, boot=None):
        self.dump(report, {'ok': ok, 'verification': 'physical', 'published': False,
                           'build_id': 'a'*32, 'recovery': claim, 'stages': [
                               {'name': 'boot-and-identity', 'ok': True, 'result': {'status': {
                                   'reset_reason': reset, 'boot_id': boot or f'boot-{claim["attempt"]}'}}}]})

    def test_prepared_plan_uses_standing_authority_without_another_question(self):
        self.assertTrue((self.state / 'quarantine.json').exists())
        self.assertTrue(recovery.validate_plan(self.plan, self.candidate)['ok'])
        seal = recovery.read(recovery.sidecar(self.plan, '.approval.json'))
        self.assertEqual(seal['standing_authorization'], recovery.STANDING_AUTHORIZATION)
        self.assertNotIn('human_authorization', seal)
        self.assertFalse(recovery.sidecar(self.plan, '.attempts').exists())
        with self.assertRaisesRegex(RuntimeError, 'actual human'):
            recovery.approve(self.plan, ' ')
        self.approved()
        self.assertTrue(recovery.validate_plan(self.plan, self.candidate)['ok'])

    def test_incomplete_or_failed_attempt_stops_next_attempt(self):
        self.approved()
        report, claim = self.claim(1)
        with self.assertRaises(FileNotFoundError):
            recovery.claim_attempt(self.plan, self.candidate, report)
        self.passed(report, claim, ok=False)
        with self.assertRaisesRegex(RuntimeError, 'failed or is incomplete'):
            recovery.claim_attempt(self.plan, self.candidate, report)
        self.assertTrue((self.state / 'quarantine.json').exists())

    def test_two_different_normal_boots_are_required_and_no_third_attempt(self):
        self.approved()
        report1, claim1 = self.claim(1)
        self.passed(report1, claim1)
        with self.assertRaisesRegex(RuntimeError, 'Two complete'):
            recovery.finish(self.plan)
        report2, claim2 = self.claim(2)
        self.passed(report2, claim2, reset=4)
        with self.assertRaisesRegex(RuntimeError, 'normal software reset'):
            recovery.finish(self.plan)
        self.passed(report2, claim2, boot='boot-1')
        with self.assertRaisesRegex(RuntimeError, 'two distinct boots'):
            recovery.finish(self.plan)
        self.passed(report2, claim2)
        with self.assertRaisesRegex(RuntimeError, 'limit reached'):
            recovery.claim_attempt(self.plan, self.candidate, report2)
        result = recovery.finish(self.plan)
        self.assertTrue(result['ok'])
        self.assertFalse((self.state / 'quarantine.json').exists())
        self.assertEqual(recovery.read(result['resolution'])['quarantine']['reason'], 'restart panic')
        self.assertFalse(result['automatic_host_restore'])

    def test_changed_candidate_firmware_quarantine_or_plan_revokes_grant(self):
        self.approved()
        with self.assertRaisesRegex(RuntimeError, 'candidate changed'):
            recovery.validate_plan(self.plan, {**self.candidate, 'head': 'different'})
        artifact = self.run / 'firmware.ota.bin'
        original = artifact.read_bytes()
        artifact.write_bytes(b'changed')
        with self.assertRaisesRegex(RuntimeError, 'artifact changed'):
            recovery.validate_plan(self.plan, self.candidate)
        artifact.write_bytes(original)
        quarantine = self.state / 'quarantine.json'
        original = quarantine.read_bytes()
        quarantine.write_text('{"reason":"new fault"}')
        with self.assertRaisesRegex(RuntimeError, 'Quarantine changed'):
            recovery.validate_plan(self.plan, self.candidate)
        quarantine.write_bytes(original)
        plan = recovery.read(self.plan)
        self.dump(self.plan, {**plan, 'expires_epoch': time.time()-1})
        with self.assertRaisesRegex(RuntimeError, 'expired'):
            recovery.validate_plan(self.plan, self.candidate)
        self.dump(self.plan, {**plan, 'extra': 'changed after approval'})
        with self.assertRaisesRegex(RuntimeError, 'plan seal changed'):
            recovery.validate_plan(self.plan, self.candidate)

    def test_identical_installed_guard_allows_only_one_newly_approved_attempt(self):
        installed = self.state / 'run-installed'
        installed.mkdir()
        previous = {**self.candidate, 'build_id': 'b'*32}
        self.dump(installed / 'candidate.json', previous)
        for name in ('firmware.ota.bin', 'firmware.elf'):
            (installed / name).write_bytes((self.run / name).read_bytes())
        boot = {'boot_healthy': True, 'reset_reason': 3, 'build_id': 'b'*32,
                'boot_id': 'installed-boot', 'firmware_elf_sha256': recovery.sha(installed / 'firmware.elf')[:9]}
        self.dump(installed / 'report.json', {'ok': False, 'firmware_sha256': recovery.sha(installed / 'firmware.ota.bin'),
                  'stages': [{'name': name, 'ok': True, 'result': {'status': boot} if name == 'boot-and-identity' else {}}
                             for name in ('restart-linkage','ota','boot-and-identity','boot-log-review','physical-integration')]})
        plan = self.state / 'single-attempt.json'
        prepared = recovery.prepare(self.run, self.root / 'diagnosis.md', plan, installed / 'report.json')
        self.assertEqual(prepared['max_attempts'], 1)
        self.assertTrue(recovery.validate_plan(plan, self.candidate)['ok'])
        self.assertTrue(recovery.validate_plan(plan, self.candidate)['installed_guard'])
        self.plan = plan
        report, claim = self.claim(1)
        self.passed(report, claim, boot='installed-boot')
        with self.assertRaisesRegex(RuntimeError, 'new boot'):
            recovery.finish(plan)
        self.passed(report, claim)
        with self.assertRaisesRegex(RuntimeError, 'limit reached'):
            recovery.claim_attempt(plan, self.candidate, report)
        guard = 'components/panel_restart/restart_guard.cpp'
        changed = {**self.candidate, 'files': {**self.candidate['files'], guard: {'sha256': 'different'}}}
        with self.assertRaisesRegex(RuntimeError, 'Runtime source changed'):
            recovery.installed_guard_proof(installed / 'report.json', changed)
        self.assertTrue(recovery.finish(plan)['ok'])

    def test_off_step_host_failure_requires_new_same_boot_complete_smoke(self):
        installed, _, _, _, _ = self.navigation_plan()
        path=installed/'report.json';failed=json.loads(path.read_text())
        failed['error']='Saved brightness is between detents; exact touch restoration is unavailable'
        failed['firmware_sha256']=recovery.sha(installed/'firmware.ota.bin')
        self.dump(path,failed);self.dump(installed/'candidate.json',self.candidate)
        boot=next(s['result']['status'] for s in failed['stages'] if s['name']=='boot-and-identity')
        state={**boot,'wifi_connected':True,'page':'page_glass_v2'}
        smoke=self.state/'new-smoke.json'
        payload={'ok':True,'before':state,'after':state,'checks':['passed']*8+[
            'real brightness detents: 48 and 52 snap to 50; exact value restoration']}
        self.dump(smoke,payload)
        proof=recovery.installed_guard_proof(path,self.candidate,smoke)
        self.assertEqual(proof['smoke_sha256'],recovery.sha(smoke))
        with self.assertRaisesRegex(RuntimeError,'physical-integration'):
            recovery.installed_guard_proof(path,self.candidate)
        self.dump(smoke,{**payload,'after':{**state,'boot_id':'another-boot'}})
        with self.assertRaisesRegex(RuntimeError,'same installed healthy boot'):
            recovery.installed_guard_proof(path,self.candidate,smoke)
        self.dump(smoke,payload);failed['error']='Unexplained hardware failure';self.dump(path,failed)
        with self.assertRaisesRegex(RuntimeError,'diagnosed off-step'):
            recovery.installed_guard_proof(path,self.candidate,smoke)

    def navigation_plan(self):
        installed = self.state/'run-ui-failed'; installed.mkdir()
        self.dump(installed/'candidate.json', self.candidate)
        for name in ('firmware.ota.bin', 'firmware.elf'):
            (installed/name).write_bytes((self.run/name).read_bytes())
        boot = {'boot_healthy': True, 'reset_reason': 3, 'build_id': 'a'*32,
                'boot_id': 'failed-ui-boot', 'firmware_elf_sha256': recovery.sha(installed/'firmware.elf')[:9]}
        failed = {'ok': False, 'error': 'Expected physical page page_hemmet', 'stages': [
            {'name': name, 'ok': name != 'physical-integration',
             'result': {'status': boot} if name == 'boot-and-identity' else {}}
            for name in ('restart-linkage','ota','boot-and-identity','boot-log-review','physical-integration')]}
        self.dump(installed/'report.json', failed)
        self.dump(self.state/'quarantine.json', {'report': str(installed/'report.json')})
        sources = {name:'checked-hash' for name in
                   ('styles/glass-v2/gen.py','tests/native/tab_gesture.cpp','tools/check_tab_gesture.py',
                    'components/panel_debug/panel_debug.cpp')}
        self.candidate['files'].update({name:{'sha256':digest} for name,digest in sources.items()})
        self.dump(self.run/'candidate.json', self.candidate)
        native = self.state/'native.json'
        proof = {'ok': True, 'actual_native_lvgl': True, 'sources': sources, 'cases': [
            {'gesture_bubble': True, 'explicit_pointer': True, 'gestures': 0, 'clicks': 1},
            {'gesture_bubble': False, 'explicit_pointer': False, 'gestures': 0, 'clicks': 1},
            {'gesture_bubble': False, 'explicit_pointer': True, 'gestures': 1, 'clicks': 0}]}
        self.dump(native, proof)
        plan = self.state/'ui-plan.json'
        result = recovery.prepare(self.run, self.root/'diagnosis.md', plan,
                                  ui_report=installed/'report.json', native_report=native)
        return installed, native, proof, plan, result

    def test_ui_recovery_requires_clean_boot_and_matching_real_lvgl_proof(self):
        installed, native, proof, plan, result = self.navigation_plan()
        self.assertEqual(result['max_attempts'], 1)
        self.assertEqual(result['kind'], 'ui-navigation-recovery')
        recovery.approve(plan, 'Human authorizes the task-specific UI correction')
        self.assertTrue(recovery.validate_plan(plan, self.candidate)['ok'])
        self.dump(native, {**proof, 'ok': False})
        with self.assertRaisesRegex(RuntimeError, 'Real native LVGL'):
            recovery.validate_plan(plan, self.candidate)
        self.dump(native, proof)
        self.plan = plan
        report, claim = self.claim(1)
        self.passed(report, claim)
        self.assertTrue(recovery.finish(plan)['ok'])

    def test_ui_repair_lineage_stops_after_two_corrections(self):
        installed, native, proof, plan, _ = self.navigation_plan()
        recovery.approve(plan, 'Human authorizes correcting this navigation task')
        self.plan = plan
        report, claim = self.claim(1)
        self.dump(report.parent/'candidate.json', self.candidate)
        failed = recovery.read(installed/'report.json')
        failed['recovery'] = claim
        failed['stages'][2]['result']['status']['boot_id'] = 'second-failed-boot'
        self.dump(report, failed)
        with self.assertRaisesRegex(RuntimeError, 'sealed prior attempt'):
            recovery.ui_failure_proof(report, native, self.candidate)
        next_plan = self.state/'second-correction.json'
        prepared = recovery.prepare(self.run, self.root/'diagnosis.md', next_plan,
                                    ui_report=report, native_report=native, prior_plan=plan)
        self.assertEqual(prepared['prior_ui_boot']['repair_depth'], 2)
        recovery.approve(next_plan, 'Same human-authorized navigation correction')
        folder = self.state/'run-physical-2'; folder.mkdir()
        for name in ('firmware.ota.bin', 'firmware.elf', 'candidate.json'):
            (folder/name).write_bytes((self.run/name).read_bytes())
        second_claim = recovery.claim_attempt(next_plan, self.candidate, folder/'report.json')
        failed['recovery'] = second_claim
        self.dump(folder/'report.json', failed)
        with self.assertRaisesRegex(RuntimeError, 'UI repair limit reached'):
            recovery.ui_failure_proof(folder/'report.json', native, self.candidate, next_plan)


class BootLogPhaseTest(unittest.TestCase):
    def test_retained_crash_before_ota_is_recorded_and_new_crash_still_fails(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'boot.log'
            history = ('Successfully connected\nCRASH DETECTED ON PREVIOUS BOOT\n'
                       'Update complete\nRebooting safely\nSuccessfully connected\n')
            path.write_text(history)
            result = ci.check_logs(path, ota_boot={'reset_reason': 3})
            self.assertEqual(result['historical_fault_lines'], 1)
            with self.assertRaisesRegex(RuntimeError, 'normal software reset'):
                ci.check_logs(path, ota_boot={'reset_reason': 4})
            path.write_text(history + 'CRASH DETECTED ON PREVIOUS BOOT\n')
            with self.assertRaisesRegex(RuntimeError, 'Crash'):
                ci.check_logs(path, ota_boot={'reset_reason': 3})
            path.write_text('Successfully connected\nRebooting safely\n')
            with self.assertRaisesRegex(RuntimeError, 'boundary'):
                ci.check_logs(path, ota_boot={'reset_reason': 3})
            path.write_text('Guru Meditation Error\n' + history)
            with self.assertRaisesRegex(RuntimeError, 'Crash'):
                ci.check_logs(path, ota_boot={'reset_reason': 3})


if __name__ == '__main__':
    unittest.main()
