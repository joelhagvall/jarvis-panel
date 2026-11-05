"""deploy_jarvis.sh restarts Home Assistant exactly when files it loads at startup changed; no network."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class DeployRestartTest(unittest.TestCase):
    def run_deploy(self, *args, change='', env_parses=True):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for folder in ('tools', '.venv/bin', 'bin', 'server/www', 'ha/custom_components/jarvis_agent'):
            (root / folder).mkdir(parents=True)
        shutil.copy(ROOT / 'tools/deploy_jarvis.sh', root / 'tools/deploy_jarvis.sh')
        log = root / 'calls.log'
        stubs = {
            '.venv/bin/python': '#!/bin/sh\nexit 0\n',
            'tools/jarvis_ssh.sh': '#!/bin/sh\nprintf "ssh %s\\n" "$*" >> "$CALLS"\n'
                                   'case "$*" in *"test -f"*) exit 1 ;; *"server.env\\" >/dev/null"*) exit "$ENV_FAIL" ;; esac\n'
                                   'cat >/dev/null 2>&1 || true\nexit 0\n',
            'tools/prepare_image_sampling.sh': '#!/bin/sh\necho /tmp/stage\n',
            'tools/deploy_assets.sh': '#!/bin/sh\nexit 0\n',
            'bin/rsync': '#!/bin/sh\ncase "$*" in *-rcn*) printf "%s" "$HA_CHANGE"; exit 0 ;; esac\n'
                         'printf "rsync %s\\n" "$*" >> "$CALLS"\nexit 0\n',
        }
        for name, source in stubs.items():
            (root / name).write_text(source)
            (root / name).chmod(0o755)
        env = {**os.environ, 'CALLS': str(log), 'HA_CHANGE': change, 'ENV_FAIL': '0' if env_parses else '1',
               'PATH': str(root / 'bin') + os.pathsep + os.environ['PATH']}
        result = subprocess.run(['sh', str(root / 'tools/deploy_jarvis.sh'), *args], env=env,
                                capture_output=True, text=True, stdin=subprocess.DEVNULL)
        return result, log.read_text() if log.exists() else ''

    def test_unparsable_server_env_stops_before_anything_is_copied(self):
        result, calls = self.run_deploy(env_parses=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('does not parse as sh', result.stderr)
        self.assertNotIn('rsync', calls)

    def test_changed_integration_restarts_home_assistant(self):
        result, calls = self.run_deploy(change='<fcsT.... sensor.py\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('Home Assistant files changed', result.stdout)
        self.assertIn('RESTART_HA=--restart-ha', calls)

    def test_unchanged_integration_does_not_restart(self):
        result, calls = self.run_deploy()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RESTART_HA= ', calls)

    def test_flags_force_or_prevent_the_restart(self):
        result, calls = self.run_deploy('--no-restart-ha', change='<fcsT.... sensor.py\n')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('RESTART_HA= ', calls)
        result, calls = self.run_deploy('--restart-ha')
        self.assertIn('RESTART_HA=--restart-ha', calls)
        result, calls = self.run_deploy('--bogus')
        self.assertEqual(result.returncode, 2)
        self.assertEqual(calls, '')


if __name__ == '__main__':
    unittest.main()
