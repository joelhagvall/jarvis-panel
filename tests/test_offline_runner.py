#!/usr/bin/env python3
"""tools/offline_tests.py: the parallel local run must stay as strict as the old single process.

A failing, erroring, skipping or crashing module fails the run; every test still runs; per-module
write-guard/profile files never collide.
"""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PASS = "import unittest\nclass T(unittest.TestCase):\n    def test_a(self): pass\n    def test_b(self): pass\n"
FILES = """import os, unittest
from pathlib import Path
class T(unittest.TestCase):
    def test_own_files(self):
        guard = Path(os.environ['JARVIS_WRITE_GUARD_FILE'])
        self.assertFalse(guard.exists(), 'another module already used this file')
        guard.write_text(__name__)
"""


class OfflineRunnerTest(unittest.TestCase):
    def run_with(self, extra, jobs='4', own_files=True):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tools').mkdir()
            (root / 'tests').mkdir()
            shutil.copy(ROOT / 'tools/offline_tests.py', root / 'tools/offline_tests.py')
            (root / 'tests/test_pass.py').write_text(PASS)
            if own_files:                           # the single-process run shares one file, as it always did
                (root / 'tests/test_files_one.py').write_text(FILES)
                (root / 'tests/test_files_two.py').write_text(FILES)
            for name, body in extra.items():
                (root / f'tests/{name}.py').write_text(body)
            env = {**os.environ, 'JARVIS_WRITE_GUARD_FILE': str(root / 'recent-creates.json'),
                   'HALLPANEL_PROFILE': str(root / 'profile.json')}
            done = subprocess.run([sys.executable, str(root / 'tools/offline_tests.py'), '--jobs', jobs],
                                  capture_output=True, text=True, env=env, timeout=120)
            self.kept = sorted(p.read_text() for p in (root / '.panel-ci/test-failures').glob('*.log'))
            return done.returncode, done.stdout + done.stderr

    def test_passing_modules_run_every_test_and_pass(self):
        code, out = self.run_with({})
        self.assertEqual(code, 0, out)
        self.assertIn('Ran 4 tests', out)
        self.assertEqual(self.kept, [])

    def test_failure_error_skip_or_crash_fails_the_run(self):
        cases = {
            'fail': "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): self.fail('no')\n",
            'error': "import unittest\nclass T(unittest.TestCase):\n    def test_x(self): raise RuntimeError\n",
            'skip': "import unittest\nclass T(unittest.TestCase):\n    @unittest.skip('missing dep')\n"
                    "    def test_x(self): pass\n",
            'crash': "import os\nos._exit(3)\n",
            'import': "import a_module_that_does_not_exist\n",
        }
        for kind, body in cases.items():
            with self.subTest(kind=kind):
                code, out = self.run_with({f'test_bad_{kind}': body})
                self.assertEqual(code, 1, out)
                self.assertEqual(len(self.kept), 1, out)       # the output survives a filtered log
                self.assertIn('exit ', self.kept[0])
                if kind == 'fail':
                    self.assertIn('AssertionError: no', self.kept[0])

    def test_single_job_keeps_the_old_sequential_path(self):
        code, out = self.run_with({}, jobs='1', own_files=False)
        self.assertEqual(code, 0, out)
        self.assertIn('Ran 2 tests', out)


if __name__ == '__main__':
    unittest.main()
