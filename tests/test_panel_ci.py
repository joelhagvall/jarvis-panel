"""Release invariants without network, model calls or hardware writes."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_ci as ci


class CandidateTest(unittest.TestCase):
    def test_native_log_subscription_requires_auth_and_a_real_record_before_protected_work(self):
        class Child:
            def __init__(self, exited=False): self.exited = exited
            def poll(self): return 0 if self.exited else None
        with tempfile.TemporaryFile() as raw:
            raw.write(b'INFO Successfully connected to hallpanel\n')
            raw.flush()
            offset = raw.tell()
            with self.assertRaisesRegex(RuntimeError, 'timed out'):
                ci.wait_log_subscription(Child(), raw, seconds=0)
            self.assertEqual(raw.tell(), offset)
            raw.write(b'INFO Successful handshake\n[17:08:43.172][I][app:151]: ESPHome version 2026.9.1\n')
            raw.flush()
            offset = raw.tell()
            ci.wait_log_subscription(Child(), raw, seconds=0)
            self.assertEqual(raw.tell(), offset)
            with self.assertRaisesRegex(RuntimeError, 'exited'):
                ci.wait_log_subscription(Child(exited=True), raw, seconds=0)

    def repository(self, path):
        subprocess.run(['git', 'init', '-b', 'main', str(path)], check=True, capture_output=True)
        subprocess.run(['git', '-C', str(path), 'config', 'user.name', 'Local Test'], check=True)
        subprocess.run(['git', '-C', str(path), 'config', 'user.email', 'test@example.invalid'], check=True)
        (path / 'source.py').write_text('x = 1\n')
        subprocess.run(['git', '-C', str(path), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(path), 'commit', '-m', 'initial'], check=True, capture_output=True)

    def test_candidate_detects_untracked_modification_deletion_and_modes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.repository(root)
            initial = ci.inventory(root)
            ci.unchanged(initial, root)
            (root / 'new.txt').write_text('new\n')
            with self.assertRaisesRegex(RuntimeError, 'Candidate changed'):
                ci.unchanged(initial, root)
            current = ci.inventory(root)
            (root / 'source.py').unlink()
            self.assertEqual(ci.inventory(root)['files']['source.py']['sha256'], 'deleted')
            with self.assertRaises(RuntimeError):
                ci.unchanged(current, root)

    def test_publish_rejects_unrelated_changes_before_fetch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.repository(root)
            subprocess.run(['git', '-C', tmp, 'remote', 'add', 'origin', 'https://github.com/joelhagvall/jarvis-panel.git'], check=True)
            subprocess.run(['git', '-C', tmp, 'update-ref', 'refs/remotes/origin/main', 'HEAD'], check=True)
            subprocess.run(['git', '-C', tmp, 'branch', '--set-upstream-to', 'origin/main'], check=True, capture_output=True)
            (root / 'other-agent.txt').write_text('private work')
            with self.assertRaisesRegex(RuntimeError, 'Unrelated/pre-existing'):
                ci.publish_preflight(['source.py'], root)

    def test_prune_preserves_incomplete_runs_and_baselines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('run-1', 'run-2', 'run-active', 'baselines'):
                (root / name).mkdir()
            for name in ('run-1', 'run-2'):
                (root / name / 'report.json').write_text('{}')
            (root/'run-1/raw.json').write_text('{"slow_samples_ms":[9148.3,518.879]}')
            ci.prune_runs(root, keep=1)
            self.assertTrue((root / 'run-1').is_symlink())
            self.assertEqual(json.loads((root/'run-1/raw.json').read_text())['slow_samples_ms'],[9148.3,518.879])
            ci.prune_runs(root,keep=1)
            self.assertTrue((root/'history/run-1/raw.json').is_file())
            self.assertTrue((root / 'run-2').exists())
            self.assertTrue((root / 'run-active').exists())
            self.assertTrue((root / 'baselines').exists())

    def test_prune_drops_old_archived_elfs_but_keeps_bound_runs_and_measurements(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            names = ['run-20261024-0000%02d-000000' % i for i in range(5)]
            for name in names:
                run = root / 'history' / name
                (run / 'previous-build').mkdir(parents=True)
                (run / 'report.json').write_text('{"raw":[1,2]}')
                (run / 'firmware.ota.bin').write_bytes(b'bin')
                (run / 'firmware.elf').write_bytes(b'elf-' + name.encode())
                (run / 'previous-build/firmware.elf').write_bytes(b'old')
            plan = root / 'some-recovery.json'
            plan.write_text(json.dumps({'run': str(root / 'history' / names[0])}))
            Path(str(plan.resolve()) + '.approval.json').write_text('{}')
            (root / 'unsealed.json').write_text(json.dumps({'run': names[1]}))
            ci.prune_runs(root, keep=1)
            ci.prune_archived_elfs(root, keep=2)
            history = root / 'history'
            self.assertTrue((history / names[0] / 'firmware.elf').is_file())
            for name in names[3:]:
                self.assertTrue((history / name / 'firmware.elf').is_file())
            for name in names[1:3]:
                self.assertFalse((history / name / 'firmware.elf').exists())
                self.assertFalse((history / name / 'previous-build/firmware.elf').exists())
                self.assertTrue((history / name / 'report.json').is_file())
                self.assertTrue((history / name / 'firmware.ota.bin').is_file())
                record = json.loads((history / name / 'elf-pruned.json').read_text())
                self.assertEqual(set(record), {'firmware.elf', 'previous-build/firmware.elf'})

    def test_runtime_log_requires_connection_and_rejects_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'runtime.log'
            path.write_text('Connecting to device...\n')
            with self.assertRaisesRegex(RuntimeError, 'connection'):
                ci.check_logs(path)
            path.write_text('Successfully connected to hallpanel\nGuru Meditation Error\n')
            with self.assertRaisesRegex(RuntimeError, 'Crash'):
                ci.check_logs(path)
            for line in ("can't fetch data from external memory fast enough, underrun happens", 'Frame presentation failed'):
                path.write_text('Successfully connected to hallpanel\n' + line + '\n')
                with self.assertRaisesRegex(RuntimeError, 'Crash'): ci.check_logs(path)
            path.write_text('Successfully connected to hallpanel\nBoot seems successful\n')
            self.assertTrue(ci.check_logs(path)['ok'])

    def test_prune_preserves_evidence_for_unresolved_quarantine(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name in ('run-1','run-2','run-3'):
                (root/name).mkdir();(root/name/'report.json').write_text('{}')
            (root/'quarantine.json').write_text(json.dumps({'report':str(root/'run-1/report.json')}))
            ci.prune_runs(root,keep=1)
            self.assertTrue((root/'run-1/report.json').exists())
            self.assertTrue((root/'run-2').is_symlink())
            self.assertTrue((root/'history/run-2/report.json').is_file())
            self.assertTrue((root/'run-3').exists())

    def test_recovery_capabilities_are_not_a_physical_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'partitions.csv').write_text('app0,app,ota_0,,0x7C0000\napp1,app,ota_1,,0x7C0000\n')
            (root / 'sdkconfig.hallpanel').write_text('CONFIG_BOOTLOADER_APP_ROLLBACK_ENABLE=y\nCONFIG_ESP_TASK_WDT_PANIC=y\n')
            support = ci.recovery_support(root)
            self.assertTrue(support['two_ota_slots'])
            self.assertTrue(support['rollback_configured'])
            self.assertFalse(support['automatic_host_restore'])

    def test_no_publish_for_local_only(self):
        with patch.object(sys, 'argv', ['panel_ci.py','--local-only','--publish']):
            with self.assertRaises(SystemExit) as result:
                ci.main()
            self.assertEqual(result.exception.code, 2)

    def test_build_identity_rejects_code_injection(self):
        env = {**os.environ, 'HALLPANEL_BUILD_ID': 'bad"-DINJECT=1'}
        result = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py', '--remote',
                                 'http://example.invalid/assets'], cwd=ROOT / 'styles/glass-v2', env=env,
                                capture_output=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(b'32 lowercase hexadecimal', result.stderr)

    def test_build_identity_stays_out_of_global_build_flags(self):
        # A per-flash -D flag changes every object's flags and forces a full ~2 min rebuild.
        build_id = 'a' * 32
        env = {**os.environ, 'HALLPANEL_BUILD_ID': build_id}
        result = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py', '--remote',
                                 'http://example.invalid/assets'], cwd=ROOT / 'styles/glass-v2', env=env,
                                capture_output=True, check=True, text=True)
        self.assertEqual(result.stdout.count(build_id), 1)
        self.assertNotIn('HALLPANEL_BUILD_ID', result.stdout)
        config = json.loads(result.stdout)
        self.assertEqual(config['panel_debug']['build_id'], build_id)


if __name__ == '__main__':
    unittest.main()
