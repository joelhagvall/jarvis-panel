"""Execute the exact remote reconciliation code against modeled services and hardware."""
from contextlib import nullcontext, redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_lock
import panel_recovery as recovery
import panel_remote


class JarvisSyncTest(unittest.TestCase):
    def exercise(self, *, changed_quarantine=False, changed_boot=False, service_failure=False):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = root / 'state'; state.mkdir()
            quarantine = state / 'quarantine.json'
            original = b'{"reason":"old restart fault"}'
            quarantine.write_bytes(original)
            status = {'build_id':'checked','boot_id':22,'firmware_elf_sha256':'abcdef123',
                      'boot_healthy':True,'reset_reason':3,'internal_free':200000,'psram_free':10000000}
            config = {'root':str(root),'state':str(state),'url':'http://test.invalid',
                      'quarantine_sha256':hashlib.sha256(original).hexdigest(),
                      'resolution_sha256':'a'*64,'resume_smoke':True,
                      'status':{k:status[k] for k in ('build_id','boot_id','firmware_elf_sha256')}}
            if changed_quarantine: quarantine.write_bytes(b'{"reason":"new fault"}')
            if changed_boot: status['boot_id']=23
            calls = []
            def service(argv, **kwargs):
                calls.append(argv[2:])
                if service_failure and argv[2:] == ['enable','--now','hallpanel-smoke.timer']:
                    raise subprocess.CalledProcessError(1, argv)
                return SimpleNamespace(returncode=0)
            error, output = None, io.StringIO()
            with patch.object(sys,'argv',['remote',json.dumps(config)]), \
                    patch.object(panel_lock,'PanelLock',lambda *a,**kw:nullcontext()), \
                    patch.object(panel_remote,'PanelClient',lambda *a,**kw:
                                 nullcontext(SimpleNamespace(status=lambda:status))), \
                    patch.object(subprocess,'run',service), redirect_stdout(output):
                try: exec(recovery.REMOTE_SYNC, {})
                except Exception as exc: error=exc
            return {'error':error,'calls':calls,'quarantine':quarantine.read_bytes() if quarantine.exists() else None,
                    'archives':len(list(state.glob('quarantine-resolved-*.json'))),'stdout':output.getvalue()}

    def test_only_exact_resolved_fault_and_live_verified_boot_resume_existing_timers(self):
        result = self.exercise()
        self.assertIsNone(result['error'])
        self.assertIsNone(result['quarantine'])
        self.assertEqual(result['archives'],1)
        self.assertTrue(json.loads(result['stdout'])['ok'])
        self.assertIn(['enable','--now','hallpanel-smoke.timer'],result['calls'])
        self.assertNotIn(['start','hallpanel-smoke.service'],result['calls'])

    def test_new_fault_or_new_boot_never_clears_quarantine_or_starts_timers(self):
        for kwargs in ({'changed_quarantine':True},{'changed_boot':True}):
            result = self.exercise(**kwargs)
            self.assertIsInstance(result['error'],RuntimeError)
            self.assertIsNotNone(result['quarantine'])
            self.assertEqual(result['archives'],0)
            self.assertEqual(result['calls'],[])

    def test_service_failure_restores_quarantine_and_disables_physical_timer(self):
        result = self.exercise(service_failure=True)
        self.assertIsInstance(result['error'],subprocess.CalledProcessError)
        self.assertIsNotNone(result['quarantine'])
        self.assertEqual(result['archives'],0)
        self.assertIn(['disable','--now','hallpanel-smoke.timer'],result['calls'])


if __name__ == '__main__':
    unittest.main()
