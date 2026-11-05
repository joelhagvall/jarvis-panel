"""Cross-process local locking and bounded device leases, without a physical device."""
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from panel_lock import PanelLock
from panel_remote import PanelClient, session_command


class PanelLockTest(unittest.TestCase):
    def test_lock_excludes_another_process_and_crash_releases_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ('from panel_lock import PanelLock; import sys; '
                    'lock=PanelLock("device",root=sys.argv[1]); '
                    'lock.__enter__(); print("held",flush=True); sys.stdin.read()')
            env = dict(os.environ, PYTHONPATH=str(ROOT / 'tools'))
            child = subprocess.Popen([sys.executable, '-c', code, tmp], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True, env=env)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'held')
                with self.assertRaisesRegex(RuntimeError, 'locked'):
                    with PanelLock('device', root=tmp): pass
            finally:
                child.kill(); child.wait(); child.stdin.close(); child.stdout.close()
            with PanelLock('device', root=tmp): pass

    def test_reentrant_and_child_descriptor_share_without_releasing_parent(self):
        with tempfile.TemporaryDirectory() as tmp:
            with PanelLock('device', root=tmp) as first:
                with PanelLock('device', root=tmp) as nested:
                    self.assertEqual(first.fd, nested.fd)
                env = dict(os.environ, PYTHONPATH=str(ROOT / 'tools'))
                child = subprocess.run([sys.executable, '-c',
                    'from panel_lock import PanelLock; import sys; '
                    'l=PanelLock("device",root=sys.argv[1]); l.__enter__(); l.__exit__()', tmp],
                    **first.subprocess_options(env), capture_output=True)
                self.assertEqual(child.returncode, 0, child.stderr.decode())
                # A fresh contender cannot inherit the lock via environment text alone.
                probe = subprocess.run([sys.executable, '-c',
                    'from panel_lock import PanelLock; import sys; '
                    'PanelLock("device",root=sys.argv[1]).__enter__()', tmp], env=env, capture_output=True)
                self.assertNotEqual(probe.returncode, 0)
                self.assertIn(b'locked', probe.stderr)
            with PanelLock('device', root=tmp): pass

    def test_forged_environment_cannot_bypass_lock(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch.dict(os.environ, HALLPANEL_LOCK_DEVICE_FD='99999'):
                with self.assertRaisesRegex(RuntimeError, 'Invalid inherited'):
                    with PanelLock('device', root=tmp): pass

    def test_lock_paths_are_private_and_not_unlinked(self):
        with tempfile.TemporaryDirectory() as tmp:
            with PanelLock('workspace', root=tmp) as lock:
                inode = lock.path.stat().st_ino
                self.assertEqual(lock.path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(lock.path.stat().st_ino, inode)

    def test_cli_handoff_verifier_and_timeout_cleanup(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'tools'
            folder.mkdir()
            script = folder / 'panel_lock.py'
            shutil.copyfile(ROOT / 'tools/panel_lock.py', script)
            runner = [sys.executable, str(script)]
            env = {k: v for k, v in os.environ.items() if not k.startswith('HALLPANEL_LOCK_')}
            result = subprocess.run(runner + ['run', '--lock', 'workspace', '--lock', 'device', '--',
                sys.executable, str(script), 'verify', '--lock', 'workspace', '--lock', 'device'],
                env=env, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr.decode())
            result = subprocess.run(runner + ['verify', '--lock', 'device'], env=env, capture_output=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn(b'needs inherited', result.stderr)
            result = subprocess.run(runner + ['run', '--lock', 'device', '--timeout', '0.1', '--',
                sys.executable, '-c', 'import time; time.sleep(10)'], env=env, capture_output=True, timeout=8)
            self.assertNotEqual(result.returncode, 0)
            with PanelLock('device', root=Path(tmp) / '.panel-ci/locks'): pass

    def test_native_lease_expiry_ownership_and_clock_wrap(self):
        compiler = shutil.which('clang++') or shutil.which('g++')
        self.assertIsNotNone(compiler)
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'lease.cpp', Path(tmp) / 'lease'
            source.write_text(r'''
#include <cassert>
#include "protocol.h"
using namespace esphome::panel_debug;
int main() {
  Lease lease;
  std::string a(64, 'a'), b(64, 'b');
  assert(!lease.acquire("short", 30000, 0));
  assert(!lease.acquire(a, 29999, 0));
  assert(!lease.acquire(a, 300001, 0));
  assert(lease.acquire(a, 30000, 0xfffffff0));
  assert(lease.active(5));
  assert(lease.owns(a, 5));
  assert(!lease.owns(b, 5));
  assert(!lease.acquire(b, 30000, 5));
  assert(!lease.release(b, 5));
  assert(lease.acquire(a, 30000, 20));
  assert(lease.release(a, 25));
  assert(!lease.active(25));
  assert(lease.acquire(b, 30000, 50));
  assert(!lease.active(30050));
  assert(lease.acquire(a, 30000, 30050));
}
''')
            subprocess.run([compiler, '-std=c++17', '-fsanitize=address,undefined',
                            '-I', str(ROOT / 'components/panel_debug'), str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, timeout=10)


class ClientInteractionTest(unittest.TestCase):
    def setUp(self):
        self.client = PanelClient('http://127.0.0.1:8080', token='a' * 64)
        self.widgets = [dict(path='screen/1', type='slider', x=100, y=100, width=300, height=20,
                             min=0, max=100, value=20, clickable=True)]

    def test_element_id_and_slider_use_fresh_geometry(self):
        with patch.object(self.client, 'ui', return_value={'widgets': self.widgets}), \
                patch.object(self.client, 'tap') as tap, patch.object(self.client, 'drag') as drag:
            self.client.tap_id('screen/1')
            tap.assert_called_once_with(250, 110)
            self.client.slider('screen/1', 80)
            drag.assert_called_once_with(160, 110, 339, 110, 300)
            with self.assertRaises(ValueError): self.client.slider('screen/1', 101)
        with patch.object(self.client, 'ui', return_value={'truncated': True, 'widgets': self.widgets}):
            with self.assertRaises(ValueError): self.client.tap_id('screen/1')

    def test_modern_client_acquires_renews_and_releases_a_lease(self):
        calls = []
        def transport(path, data, headers):
            calls.append((path, data, headers))
            return b'{"ok":true}', {}
        with tempfile.TemporaryDirectory() as tmp, patch('panel_remote.PanelLock',
                side_effect=lambda name: PanelLock(name, root=tmp)):
            self.client._protocol = {'protocol': 2, 'lease_required': True, 'boot_id': 1}
            with patch.object(self.client, '_persistent_request', side_effect=transport):
                self.client.connection = object()
                self.client._ensure_exclusive(True)
                lease = self.client._lease_id
                self.client._lease_renew_at = 0
                self.client.request('/ui')
                # A fake connection only needs close during final cleanup.
                self.client.connection = None
                with patch.object(self.client.opener, 'open') as opener:
                    opener.return_value.__enter__.return_value.read.return_value = b'{"ok":true}'
                    opener.return_value.__enter__.return_value.headers = {}
                    self.client.close()
                    self.assertEqual(opener.call_count, 1)
        self.assertEqual([c[0] for c in calls], ['/lease', '/lease', '/ui'])
        self.assertIn(b'action=acquire', calls[0][1])
        self.assertIn(b'action=renew', calls[1][1])
        self.assertEqual(calls[2][2]['X-Panel-Lease'], lease)

    def test_strict_exclusive_rejects_legacy_firmware(self):
        with tempfile.TemporaryDirectory() as tmp, patch('panel_remote.PanelLock',
                side_effect=lambda name: PanelLock(name, root=tmp)):
            self.client._protocol = {'page': 'page_hemmet'}
            with self.assertRaisesRegex(RuntimeError, 'lacks a device lease'):
                with self.client.exclusive(): pass
            self.assertIsNone(self.client._lock)

    def test_cli_lease_client_reuses_the_script_lock_instead_of_locking_itself_out(self):
        # flash_panel.sh runs panel_lock.py as a script with --lease-url; PanelClient must get that
        # same module (and lock registry), not a second copy whose flock fails against our own lock.
        code = ("import runpy, sys\n"
                "sys.path.insert(0, sys.argv[1]); script = sys.argv[1] + '/panel_lock.py'\n"
                "sys.argv = [script, 'run', '--lock', 'workspace']\n"
                "try: runpy.run_path(script, run_name='__main__')\n"
                "except SystemExit: pass\n"
                "import panel_remote\n"
                "print(panel_remote.PanelLock.__module__)\n")
        result = subprocess.run([sys.executable, '-c', code, str(ROOT / 'tools')], capture_output=True, text=True)
        self.assertEqual(result.stdout.strip(), '__main__', result.stderr)

    def test_synthetic_mode_validation_happens_before_network(self):
        with patch.object(self.client, 'request') as request:
            for mode, duration in [('restart', 100), ('listening', 30001), ('idle', True)]:
                with self.assertRaises(ValueError): self.client.test_mode(mode, duration)
            request.assert_not_called()

    def test_long_chat_fixture_is_supported_with_the_same_exclusive_lease(self):
        with patch.object(self.client, '_ensure_exclusive') as lease, \
                patch.object(self.client, 'request', return_value=(b'{"ok":true}', {}, 1)) as request:
            self.assertTrue(self.client.test_mode('chat_scroll', duration_ms=30000)['ok'])
            lease.assert_called_once_with(require_device_lease=True)
            request.assert_called_once_with('/test', {'mode': 'chat_scroll', 'duration_ms': 30000})


if __name__ == '__main__':
    unittest.main()
