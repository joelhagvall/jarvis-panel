"""Offline checks of eval grading, budgets and failure handling; no model calls."""
import contextlib
from datetime import datetime
import importlib.util
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from unittest.mock import patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'server/agents'))
sys.path.insert(0, str(ROOT / 'server'))
from eval_cases import scenarios, select, summarize
import mock_tools_mcp
from tools import check

NOW = datetime(2026, 10, 23, 12, tzinfo=ZoneInfo('Europe/Stockholm'))


def call(tool, **args):
    return {'tool': tool, 'args': args}


class EvalsTest(unittest.TestCase):
    def setUp(self):
        self.cases = {c.name: c for c in scenarios(NOW)}

    def test_wrong_date_wrong_action_and_duplicate_write_fail(self):
        turn = self.cases['calendar-tomorrow'].turns[0]
        self.assertEqual(turn.grade([call('calendar_list_events', date='2026-10-24')], 'Standup.'), [])
        self.assertIn('reads correct date', turn.grade([call('calendar_list_events', date='2026-10-23')], 'Standup.'))
        tv = self.cases['tv-off'].turns[0]
        self.assertIn('exact TV action', tv.grade([call('tv_power', on=True)], 'Klart.'))
        add = self.cases['add-today'].turns[0]
        correct = call('calendar_add_event', title='Köp kvarg', start='2026-10-23T17:00')
        self.assertEqual(add.grade([correct], 'Klart, lagt till.'), [])
        self.assertIn('adds exactly once', add.grade([correct, correct], 'Klart, lagt till.'))

    def test_failed_tool_cannot_be_scored_as_success(self):
        turn = self.cases['failed-booking'].turns[0]
        calls = [call('calendar_add_event', title='Tandläkaren', start='2026-10-30T10:00')]
        self.assertEqual(turn.grade(calls, 'Kunde inte boka. Kalendern svarade inte.'), [])
        self.assertIn('never claims success', turn.grade(calls, 'Klart, bokat!'))
        self.assertIn('reports failure', turn.grade(calls, ''))

    def test_followup_has_two_turns_and_budget_counts_repeats_before_running(self):
        cases = list(self.cases.values())
        self.assertEqual(len(select(cases, ['calendar-followup'], 2, 4)), 2)
        for names, repeats, budget in ((['calendar-followup'], 2, 3), (['missing'], 1, 2), ([], 0, 15), ([], 1, 0)):
            with self.assertRaises(ValueError):
                select(cases, names, repeats, budget)

    def test_missing_timing_is_not_zero_and_p95_includes_the_slow_turn(self):
        summary = summarize([{'failed_checks': [], 'first_token_s': 1, 'wall_s': 2, 'cost_usd': .001},
                             {'failed_checks': ['bad'], 'first_token_s': None, 'wall_s': 8},
                             {'failed_checks': [], 'first_token_s': 3, 'wall_s': 4}])
        self.assertEqual(summary['first_token_p50_s'], 1)
        self.assertEqual(summary['wall_p95_s'], 8)
        self.assertEqual(summary['failed_turns'], 1)
        self.assertIsNone(summarize([])['first_token_p95_s'])
        self.assertIsNone(summary['reported_cost_usd'])
        self.assertEqual(summary['cost_missing_turns'], 2)

    def test_mock_failure_and_empty_are_distinct(self):
        with tempfile.TemporaryDirectory() as tmp:
            mode = Path(tmp) / 'mode'
            with patch.dict(os.environ, {'MOCK_MODE_FILE': str(mode), 'MOCK_LOG': str(Path(tmp) / 'log'),
                                        'MOCK_STATE': str(Path(tmp) / 'state')}):
                mode.write_text('empty')
                error, text = mock_tools_mcp.call('calendar_list_events', {'date': '2026-10-24'})
                self.assertFalse(error)
                self.assertEqual(json.loads(text)['events'], [])
                mode.write_text('fail-read')
                error, text = mock_tools_mcp.call('calendar_list_events', {'date': '2026-10-24'})
                self.assertTrue(error)
                self.assertIn('Fel:', text)

    def test_live_flag_and_budget_are_required_before_credentials_or_network(self):
        script = ROOT / 'tests/integration_test.py'
        for args in ([], ['--live'], ['--live', '--max-turns', '1']):
            result = subprocess.run([sys.executable, str(script), *args], capture_output=True, text=True)
            self.assertEqual(result.returncode, 2, result.stderr)
        preview = subprocess.run([sys.executable, str(script), '--list'], capture_output=True, text=True)
        self.assertEqual(preview.returncode, 0, preview.stderr)
        self.assertIn('calendar-followup: 2', preview.stdout)

    def test_failed_local_checks_prevent_later_release_steps(self):
        with patch.object(sys, 'argv', ['check.py', '--ha', 'fake-host']), \
             patch.object(check, 'local_tests', side_effect=RuntimeError('failed regression')), \
             patch.object(check, 'panel_config') as config, patch.object(check, 'ha_tests') as ha, \
             contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(check.main(), 1)
            config.assert_not_called()
            ha.assert_not_called()

    def test_offline_guard_blocks_external_hosts_in_a_child_process(self):
        code = "import socket; socket.getaddrinfo('api.openai.com',443)"
        result = subprocess.run([sys.executable, '-c', code], env=check.offline_env(), capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('offline tests block external DNS', result.stderr)

    def test_install_scripts_stop_before_remote_calls_or_compile_when_checks_fail(self):
        for script in ('deploy_jarvis.sh', 'flash_panel.sh'):
            with self.subTest(script=script), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / 'tools').mkdir()
                (root / '.venv/bin').mkdir(parents=True)
                shutil.copy(ROOT / 'tools' / script, root / 'tools' / script)
                shutil.copy(ROOT / 'tools/panel_lock.py', root / 'tools/panel_lock.py')
                # The new target preflight is passive and may precede checks.
                # Any subsequent panel command still fails this fixture.
                panel = root / 'tools/panel'
                # Recording the build id and resolving the URL are local and passive too.
                panel.write_text('#!/bin/sh\n[ "$1" = preflight ] && exit 0\n'
                                 '[ "$1 $2" = "gates --record-build" ] && echo 0123456789abcdef0123456789abcdef && exit 0\n'
                                 '[ "$1 $2" = "gates --resolve-url" ] && echo http://192.0.2.7:8080 && exit 0\n'
                                 'printf "%s\\n" "$*" >> "$FORBIDDEN_LOG"\nexit 91\n')
                panel.chmod(0o755)
                executable = root / '.venv/bin/python'
                executable.write_text('#!/bin/sh\ncase "$1" in */panel_lock.py) '
                                      'exec "$REAL_PYTHON" "$@" ;; esac\n'
                                      'printf "%s\\n" "$*" >> "$GATE_LOG"\nexit 23\n')
                executable.chmod(0o755)
                # Any compile or outward-facing command is a test failure. The
                # real lock CLI still executes and verifies its inherited fd.
                for name in ('esphome', 'ssh', 'rsync'):
                    forbidden = root / '.venv/bin' / name
                    forbidden.write_text('#!/bin/sh\nprintf "%s\\n" "$0 $*" >> "$FORBIDDEN_LOG"\nexit 91\n')
                    forbidden.chmod(0o755)
                env = {**check.clean_env(), 'GATE_LOG': str(root / 'gate.log'),
                       'FORBIDDEN_LOG': str(root / 'forbidden.log'), 'REAL_PYTHON': sys.executable,
                       'PATH': str(root / '.venv/bin') + os.pathsep + os.environ.get('PATH', '')}
                result = subprocess.run(['sh', str(root / 'tools' / script)], env=env, capture_output=True, text=True)
                self.assertEqual(result.returncode, 23, result.stderr)
                self.assertIn('tools/check.py', (root / 'gate.log').read_text())
                self.assertEqual(len((root / 'gate.log').read_text().splitlines()), 1)
                self.assertFalse((root / 'forbidden.log').exists())

    def test_candidate_agent_only_offers_mocks_and_screen(self):
        spec = importlib.util.spec_from_file_location('eval_server', ROOT / 'tests/eval_server.py')
        bootstrap = importlib.util.module_from_spec(spec)
        # Importing the bootstrap patches the class; restore it after exercising the candidate.
        import agent_core
        original = agent_core.ToolAgent._ensure_tools
        from watch import Watch
        original_watch_start = Watch.start
        try:
            spec.loader.exec_module(bootstrap)
            with patch.dict(os.environ, check.clean_env(), clear=True):
                agent = agent_core.ToolAgent()
                agent._init_tools(True, None, None, None)
                agent._ensure_tools()
                self.assertEqual({t['name'] for t in agent.tools},
                                 {t['name'] for t in mock_tools_mcp.TOOLS} | {'skarm'})
                self.assertEqual(set(agent.local_tools), {'skarm'})
        finally:
            agent_core.ToolAgent._ensure_tools = original
            Watch.start = original_watch_start

    def test_eval_server_entrypoint_imports_without_starting_a_model(self):
        result = subprocess.run([sys.executable, str(ROOT / 'tests/eval_server.py'), '--help'],
                                env=check.offline_env(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('--bind', result.stdout)

    def test_transport_failure_is_saved_as_a_failed_report_without_a_model(self):
        spec = importlib.util.spec_from_file_location('integration_eval_test', ROOT / 'tests/integration_test.py')
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)

        def respond(request, **kwargs):
            if request.full_url.endswith('/converse'):
                raise urllib.error.URLError('synthetic transport failure')
            return io.BytesIO(b'{"messages": []}' if '/history?' in request.full_url else b'{}')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tests').mkdir()
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'free_port', return_value=12345), \
                 patch.object(runner.subprocess, 'Popen') as process, \
                 patch.object(runner.time, 'sleep'), patch.object(runner.urllib.request, 'urlopen', side_effect=respond), \
                 patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-key'}), \
                 patch.object(sys, 'argv', ['integration_test.py', '--live', '--max-turns', '1', '--cases', 'calendar-tomorrow']), \
                 contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(runner.main(), 1)
                process.return_value.terminate.assert_called_once()
            reports = list((root / 'tests/results').glob('*.json'))
            self.assertEqual(len(reports), 1)
            report = json.loads(reports[0].read_text())
            self.assertEqual(report['summary']['failed_turns'], 1)
            self.assertEqual(report['results'][0]['error'], 'URLError')


if __name__ == '__main__':
    unittest.main()
