"""A new Python image worker must never arrive before its native dependencies."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class NativeDeployTest(unittest.TestCase):
    def deployment(self, native_failure=0):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tools').mkdir()
            (root / 'server').mkdir()
            binary = root / 'bin'
            binary.mkdir()
            log = root / 'events'
            shutil.copyfile(ROOT / 'tools/deploy_jarvis.sh', root / 'tools/deploy_jarvis.sh')
            shutil.copy2(ROOT / 'tools/jarvis_ssh.sh', root / 'tools/jarvis_ssh.sh')
            for name in ('tools/check.py', 'server/stage.py'):
                (root / name).write_text('import os\nfrom pathlib import Path\np=Path(os.environ["DEPLOY_TEST_LOG"])\np.open("a").write("local-check-or-stage\\n")\n')
            (root / 'tools/prepare_image_sampling.sh').write_text(
                '#!/bin/sh\nprintf "candidate\\n" >> "$DEPLOY_TEST_LOG"\n'
                'if [ "$NATIVE_TEST_FAILURE" != 0 ]; then exit "$NATIVE_TEST_FAILURE"; fi\n'
                'printf "%s\\n" /tmp/hallpanel-image-native.TEST\n')
            ssh = r'''import os,sys
from pathlib import Path
p=Path(os.environ['DEPLOY_TEST_LOG'])
args=' '.join(sys.argv[1:])
event='library' if '.libhallpanel_image_sampling.so.tmp' in args else 'ssh'
with p.open('a') as f:f.write(event+'\n')
'''
            rsync = r'''import os,sys
from pathlib import Path
p=Path(os.environ['DEPLOY_TEST_LOG'])
if sys.argv[-1].endswith('hallpanel-live/server/'):
    events=p.read_text().splitlines()
    if 'library' not in events or 'binding' not in events:sys.exit(99)
    with p.open('a') as f:f.write('server\n')
    sys.exit(73)  # Stop this fake deployment before any later credential/setup paths.
if any(a.endswith('/image_sampling.py') for a in sys.argv):
    with p.open('a') as f:f.write('binding\n')
'''
            for name, body in (('ssh', ssh), ('rsync', rsync)):
                script = binary / name
                script.write_text(f'#!{sys.executable}\n' + body)
                script.chmod(0o700)
            env = {**os.environ, 'PATH': str(binary) + os.pathsep + os.environ['PATH'],
                   'DEPLOY_TEST_LOG': str(log), 'NATIVE_TEST_FAILURE': str(native_failure),
                   'JARVIS_SSH': 'local-test-only'}
            result = subprocess.run(['sh', str(root / 'tools/deploy_jarvis.sh')], env=env,
                                    stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=15)
            return result.returncode, log.read_text().splitlines(), result.stderr

    def test_native_library_and_binding_precede_the_dependent_server_upload(self):
        code, events, error = self.deployment()
        self.assertEqual(code, 73, error)
        self.assertLess(events.index('candidate'), events.index('library'))
        self.assertLess(events.index('library'), events.index('binding'))
        self.assertLess(events.index('binding'), events.index('server'))

    def test_failed_native_candidate_never_starts_any_live_copy(self):
        code, events, error = self.deployment(native_failure=81)
        self.assertEqual(code, 81, error)
        self.assertNotIn('library', events)
        self.assertNotIn('binding', events)
        self.assertNotIn('server', events)


if __name__ == '__main__':
    unittest.main()
