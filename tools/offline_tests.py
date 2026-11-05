#!/usr/bin/env python3
"""Run only free tests; missing dependencies/skips fail instead of producing a green release.

Local runs give every test module its own process, several at a time (--jobs, default: all cores), slowest
first by the previous run's times in .panel-ci/test-durations.json. Each module gets its own write-guard and
profile files. The HA container run (--ha) stays one sequential process. A failing module's full output is
also kept in .panel-ci/test-failures/, so a flake seen through a filtered log can still be read afterwards.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
HA_TESTS = {'test_ha_conversation', 'test_panel_voice', 'test_wakeup_ha', 'test_media_ha'}
DURATIONS = ROOT / '.panel-ci' / 'test-durations.json'
FAILURES = ROOT / '.panel-ci' / 'test-failures'
PER_MODULE_FILES = ('JARVIS_WRITE_GUARD_FILE', 'HALLPANEL_PROFILE', 'HALLPANEL_AUTH_PIN')
SKIPPED = 'FAIL: skipped tests; install tests/requirements.txt and a C++ compiler/ffmpeg.'


def modules(ha):
    return [path.stem for path in sorted((ROOT / 'tests').glob('test_*.py')) if (path.stem in HA_TESTS) == ha]


def run_suite(names, result_file=None):
    loader, suite = unittest.TestLoader(), unittest.TestSuite()
    sys.path.insert(0, str(ROOT / 'tests'))
    for name in names:
        suite.addTests(loader.loadTestsFromName(name))
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    if result_file:
        Path(result_file).write_text(json.dumps({'run': result.testsRun, 'failures': len(result.failures),
                                                 'errors': len(result.errors), 'skipped': len(result.skipped)}))
    if result.skipped:
        print(SKIPPED, file=sys.stderr)
    return 0 if result.testsRun and result.wasSuccessful() and not result.skipped else 1


def keep_failure(name, done):
    try:
        FAILURES.mkdir(parents=True, exist_ok=True)
        log = FAILURES / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}.log"
        log.write_text(f'exit {done.returncode}\n{done.stdout}\n{done.stderr}')
        print(f'{name} output kept in {log}', file=sys.stderr)
    except OSError:
        pass


def run_parallel(names, jobs):
    try:
        previous = json.loads(DURATIONS.read_text())
    except (OSError, ValueError):
        previous = {}
    names = sorted(names, key=lambda name: -previous.get(name, 1e9))   # unknown first, then slowest
    started, totals, durations, failed = time.monotonic(), {'run': 0, 'skipped': 0}, {}, []
    with tempfile.TemporaryDirectory(prefix='hallpanel-tests-') as tmp:
        def one(name):
            env = dict(os.environ)
            for key in PER_MODULE_FILES:
                if key in env:
                    env[key] = str(Path(tmp) / f'{name}-{Path(env[key]).name}')
            result_file = Path(tmp) / f'{name}.json'
            began = time.monotonic()
            done = subprocess.run([sys.executable, __file__, '--module', name, '--result', str(result_file)],
                                  cwd=ROOT, env=env, stdin=subprocess.DEVNULL, capture_output=True, text=True)
            try:
                counts = json.loads(result_file.read_text())
            except (OSError, ValueError):
                counts = None                       # crashed before reporting: counts as a failure
            return name, done, counts, time.monotonic() - began
        with ThreadPoolExecutor(max_workers=jobs) as pool:
            for future in as_completed([pool.submit(one, name) for name in names]):
                name, done, counts, seconds = future.result()
                durations[name] = round(seconds, 2)
                sys.stdout.write(done.stdout)
                sys.stderr.write(done.stderr)
                if counts:
                    totals['run'] += counts['run']
                    totals['skipped'] += counts['skipped']
                if done.returncode or not counts:
                    failed.append(name)
                    keep_failure(name, done)
    try:
        DURATIONS.parent.mkdir(exist_ok=True)
        DURATIONS.write_text(json.dumps(durations, indent=1, sort_keys=True))
    except OSError:
        pass
    elapsed = time.monotonic() - started
    print(f"\nRan {totals['run']} tests in {elapsed:.3f}s ({len(names)} modules, {jobs} at a time)")
    if totals['skipped']:
        print(SKIPPED, file=sys.stderr)
    if failed:
        print(f'FAILED modules: {", ".join(sorted(failed))}')
        return 1
    print('OK')
    return 0 if totals['run'] and not totals['skipped'] else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ha', action='store_true')
    parser.add_argument('--jobs', type=int, default=os.cpu_count() or 1)
    parser.add_argument('--module', help=argparse.SUPPRESS)
    parser.add_argument('--result', help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.ha and importlib.util.find_spec('homeassistant') is None:
        parser.error('--ha must run inside Home Assistant')
    if args.module:
        return run_suite([args.module], args.result)
    names = modules(args.ha)
    if args.ha or args.jobs <= 1:
        return run_suite(names)
    return run_parallel(names, args.jobs)


if __name__ == '__main__':
    sys.exit(main())
