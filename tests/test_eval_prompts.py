"""Ad-hoc prompt mode of tests/integration_test.py (--prompt/--prompts), offline: no model, no network.

Regression for 2026-10-23, when ad-hoc prompts were POSTed to the production /converse on Jarvis and the
brain created a real calendar event, tried a phone push and wrote into Joel's day history. Prompt mode must
parse the prompts, keep the paid-run guards and turn every tool outside eval_cases.PROMPT_READ_TOOLS into a
recording stub that never runs.
"""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tests'))
sys.path.insert(0, str(ROOT / 'server/agents'))
sys.path.insert(0, str(ROOT / 'server'))
import agent_core
from agent_core import Call, Round
from eval_cases import PROMPT_READ_TOOLS, prompt_scenarios, read_prompts
from memory import Memory
from notify import Notifier
from reminders import Reminders
from tools import check
from watch import Watch

SCRIPT = ROOT / 'tests/integration_test.py'
WRITES = {'calendar_add_event', 'tv_power', 'radio', 'kom_ihag', 'glom', 'paminnelse', 'till_telefonen'}


def load_bootstrap():
    """Import tests/eval_server.py, which patches ToolAgent; the caller restores it with restore()."""
    spec = importlib.util.spec_from_file_location('eval_server_prompts_test', ROOT / 'tests/eval_server.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class WritingBrain(agent_core.ToolAgent):
    """Round 1 asks for one real read, every write the server offers and a brand-new write tool; round 2 answers."""
    label = 'fake'

    def __init__(self, extra):
        self._init_tools(True, None, None, None)
        self.extra_tools = extra

    def _begin(self, prompt, instructions):
        return {'rounds': 0, 'results': None}

    def _round(self, convo, on_text):
        convo['rounds'] += 1
        if convo['rounds'] == 1:
            return Round('', [Call('1', 'calendar_add_event', {'title': 'Moderna museet', 'start': '2026-10-25T13:00'}),
                              Call('2', 'till_telefonen', {'text': 'Planen'}), Call('3', 'kom_ihag', {'text': 'x'}), Call('9', 'glom', {'n': 1}),
                              Call('4', 'paminnelse', {'text': 'Gå', 'at': '12:30'}), Call('5', 'tv_power', {'on': True}),
                              Call('6', 'radio', {'action': 'play'}), Call('7', 'boka_bord', {'where': 'Pelikan'}),
                              Call('8', 'wikipedia', {'query': 'Moderna museet'})],
                         usage={'input_tokens': 10, 'output_tokens': 5})
        if on_text:
            on_text('Inget bokades.')
        return Round('Inget bokades.', [], usage={'input_tokens': 20, 'output_tokens': 5})

    def _add_results(self, convo, r, results):
        convo['results'] = results


class PromptModeTest(unittest.TestCase):
    def setUp(self):
        self.saved = agent_core.ToolAgent._ensure_tools, Watch.start
        self.tmp = Path(tempfile.mkdtemp(prefix='hallpanel-prompts-'))
        self.addCleanup(self.restore)

    def restore(self):
        agent_core.ToolAgent._ensure_tools, Watch.start = self.saved

    def test_prompts_from_args_and_file_are_parsed(self):
        file = self.tmp / 'prompts.txt'
        file.write_text('# Joels frågor\n\nVad blir vädret i helgen?\n  # indrag är också en kommentar\n'
                        '  Hur tar jag mig till Moderna museet?  \n', encoding='utf-8')
        prompts = read_prompts(['Vilket flygplan flyger över mig?'], [str(file)])
        self.assertEqual(prompts, ['Vilket flygplan flyger över mig?', 'Vad blir vädret i helgen?',
                                   'Hur tar jag mig till Moderna museet?'])
        separate = prompt_scenarios(prompts)
        self.assertEqual([len(c.turns) for c in separate], [1, 1, 1])
        self.assertEqual([c.turns[0].text for c in separate], prompts)
        self.assertEqual([t.text for t in prompt_scenarios(prompts, chain=True)[0].turns], prompts)
        with self.assertRaises(ValueError):
            read_prompts(['  '])
        # The free preview lists the prompts and the tool policy, without a model or the network.
        result = subprocess.run([sys.executable, str(SCRIPT), '--list', '--prompts', str(file),
                                 '--prompt', 'Boka Moderna museet på söndag'],
                                env=check.offline_env(), capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        # --prompt values come first, then the files' lines in order.
        self.assertIn('prompt-1: Boka Moderna museet på söndag', result.stdout)
        self.assertIn('prompt-3: Hur tar jag mig till Moderna museet?', result.stdout)
        blocked = next(l for l in result.stdout.splitlines() if l.startswith('blocked and recorded'))
        for name in WRITES:
            self.assertIn(name, blocked)

    def test_live_and_max_turns_are_still_required_in_prompt_mode(self):
        for args in (['--prompt', 'Hej'], ['--prompt', 'Hej', '--live'], ['--prompt', 'Hej', '--max-turns', '1'],
                     ['--prompt', 'Hej', '--agent', 'claude', '--live', '--max-turns', '1'],
                     ['--prompt', 'Hej', '--cases', 'tv-on', '--live', '--max-turns', '1']):
            with self.subTest(args=args):
                result = subprocess.run([sys.executable, str(SCRIPT), *args], env=check.offline_env(),
                                        capture_output=True, text=True)
                self.assertEqual(result.returncode, 2, result.stderr)
        # Two prompts do not fit a one-turn budget: refused before keys, the server or the model.
        spec = importlib.util.spec_from_file_location('integration_prompts_budget', SCRIPT)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        with patch.object(runner.subprocess, 'Popen', side_effect=AssertionError('server started')), \
             patch.object(runner.urllib.request, 'urlopen', side_effect=AssertionError('network')), \
             patch.object(sys, 'argv', ['integration_test.py', '--live', '--max-turns', '1',
                                        '--prompt', 'Ett', '--prompt', 'Två']), \
             contextlib.redirect_stderr(io.StringIO()) as err, self.assertRaises(SystemExit) as stop:
            runner.main()
        self.assertEqual(stop.exception.code, 2)
        self.assertIn('budget is 1', err.getvalue())

    def test_write_tools_are_blocked_and_recorded_reads_still_run(self):
        bootstrap = load_bootstrap()
        mock_log, state, blocked_log = self.tmp / 'calls.jsonl', self.tmp / 'state.json', self.tmp / 'blocked.jsonl'
        ran_for_real = []

        def forbidden(name):
            return lambda args: (ran_for_real.append(name), (False, 'ran'))[1]

        db = str(self.tmp / 'history.db')
        phone = Notifier()
        phone.phone = 'eval-stub'
        memory, reminders = Memory(db), Reminders(db)
        self.addCleanup(memory.db.close)
        self.addCleanup(reminders.db.close)
        server_tools = [*memory.tools(), *reminders.tools(), *phone.tools()]
        new_write = {'type': 'function', 'name': 'boka_bord', 'description': 'Bokar ett bord.',
                     'parameters': {'type': 'object', 'properties': {}}}
        extra = [(tool, forbidden(tool['name'])) for tool, _ in server_tools] + [(new_write, forbidden('boka_bord'))]
        env = {**check.clean_env(), 'JARVIS_EVAL_PROMPTS': '1', 'EVAL_BLOCKED_LOG': str(blocked_log),
               'MOCK_LOG': str(mock_log), 'MOCK_STATE': str(state), 'MOCK_MODE_FILE': str(self.tmp / 'mode')}
        with patch.dict(os.environ, env, clear=True), \
             patch.object(agent_core, 'GUARD_FILE', self.tmp / 'recent-creates.json'), \
             patch.object(agent_core.radio, 'radio', side_effect=AssertionError('radio played')), \
             patch.object(agent_core.lookup, 'wikipedia', return_value=(False, 'Moderna museet är ett konstmuseum.')) as wiki:
            brain = WritingBrain(extra)
            reply, _, usage = brain.ask('Boka Moderna museet på söndag och skicka planen till telefonen')
        self.assertEqual(reply, 'Inget bokades.')
        self.assertEqual(usage['steps'], 2)
        self.assertEqual(ran_for_real, [])
        wiki.assert_called_once_with('Moderna museet')
        self.assertFalse(mock_log.exists() and mock_log.read_text().strip(), 'mock calendar/TV ran')
        self.assertFalse(state.exists() and state.read_text().strip(), 'a mock event was added')
        recorded = [json.loads(line) for line in blocked_log.read_text().splitlines()]
        self.assertEqual({r['tool'] for r in recorded}, WRITES | {'boka_bord'})
        self.assertIn({'tool': 'calendar_add_event', 'args': {'title': 'Moderna museet', 'start': '2026-10-25T13:00'}},
                      recorded)
        errors = {t['name']: t['error'] for t in brain.last_tools}
        self.assertTrue(all(errors[name] for name in WRITES | {'boka_bord'}))
        self.assertFalse(errors['wikipedia'])
        # Every offered tool outside the allow-list is a stub, so a tool added later is blocked by default.
        offered = {t['name'] for t in brain.tools}
        stubs = {n for n in offered if getattr(brain.local_tools.get(n), 'blocked', False)}
        self.assertEqual(stubs, offered - PROMPT_READ_TOOLS)
        self.assertLessEqual(brain.read_only, PROMPT_READ_TOOLS)
        with patch.dict(os.environ, {**check.clean_env(), 'JARVIS_WRITE_GUARD_FILE': str(self.tmp / 'g.json'),
                                     'HALLPANEL_PROFILE': str(self.tmp / 'profile.json')}, clear=True):
            real, blocked = bootstrap.prompt_tool_policy(str(self.tmp / 'policy.db'))
        self.assertLessEqual(set(real), PROMPT_READ_TOOLS)
        self.assertLessEqual(WRITES, set(blocked))

    def test_scenario_mode_still_offers_only_mocks_and_screen(self):
        load_bootstrap()
        with patch.dict(os.environ, check.clean_env(), clear=True):
            agent = agent_core.ToolAgent()
            agent._init_tools(True, None, None, None)
            agent._ensure_tools()
        self.assertEqual(set(agent.local_tools), {'skarm'})
        self.assertNotIn('wikipedia', {t['name'] for t in agent.tools})

    def test_prompt_run_isolates_the_server_and_reports_blocked_writes(self):
        spec = importlib.util.spec_from_file_location('integration_prompts_run', SCRIPT)
        runner = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(runner)
        started, messages = {}, []

        def popen(cmd, env, **kwargs):
            started.update(cmd=cmd, env=env)
            return MagicMock()

        def respond(request, **kwargs):
            env = started['env']
            if request.full_url.endswith('/converse'):
                with open(env['AGENT_TOOL_LOG'], 'a') as f:
                    f.write(json.dumps({'tool': 'vader', 'args': {}, 'error': False, 'ms': 5}) + '\n')
                    f.write(json.dumps({'tool': 'calendar_add_event', 'args': {'title': 'Moderna museet'},
                                        'error': True, 'ms': 1}) + '\n')
                with open(env['EVAL_BLOCKED_LOG'], 'a') as f:
                    f.write(json.dumps({'tool': 'calendar_add_event', 'args': {'title': 'Moderna museet'}}) + '\n')
                messages.extend(['q', 'a'])
                return io.BytesIO(json.dumps({'ok': True, 'reply': 'Det blir sol.', 'timing': {
                    'first_token_s': 0.5, 'total_s': 1.2, 'steps': 2, 'brain': 'fake', 'cost_usd': 0.001}}).encode())
            if '/history?' in request.full_url:
                return io.BytesIO(json.dumps({'messages': messages}).encode())
            return io.BytesIO(b'{}')

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / 'tests').mkdir()
            with patch.object(runner, 'ROOT', root), patch.object(runner, 'free_port', return_value=12345), \
                 patch.object(runner.subprocess, 'Popen', side_effect=popen), patch.object(runner.time, 'sleep'), \
                 patch.object(runner.urllib.request, 'urlopen', side_effect=respond), \
                 patch.dict(os.environ, {'OPENAI_API_KEY': 'synthetic-key'}), \
                 patch.object(sys, 'argv', ['integration_test.py', '--live', '--max-turns', '1',
                                            '--prompt', 'Vad blir vädret i helgen?']), \
                 contextlib.redirect_stdout(io.StringIO()) as out:
                self.assertEqual(runner.main(), 0)
            env = started['env']
            self.assertEqual(env['JARVIS_EVAL_PROMPTS'], '1')
            self.assertEqual(env['JARVIS_MOCK_TOOLS'], '1')
            self.assertNotIn('JARVIS_CALENDAR_MCP', env)
            self.assertNotIn('JARVIS_HOME_TOKEN_FILE', env)
            self.assertNotIn('JARVIS_TV_MCP', env)
            tmpdir = Path(env['HALLPANEL_PROFILE']).parent
            self.assertTrue(tmpdir.name.startswith('hallpanel-it-'), tmpdir)
            db = Path(started['cmd'][started['cmd'].index('--db') + 1])
            self.assertEqual(db.parent, tmpdir)
            self.assertEqual(Path(env['JARVIS_WRITE_GUARD_FILE']).parent, tmpdir)
            self.assertIn("BLOCKED writes: [('calendar_add_event'", out.getvalue())
            report = json.loads(next((root / 'tests/results').glob('*-prompts.json')).read_text())
            row = report['results'][0]
            self.assertEqual(report['mode'], 'prompts')
            self.assertEqual(row['reply'], 'Det blir sol.')
            self.assertEqual(row['steps'], 2)
            self.assertEqual(row['blocked'], ['calendar_add_event'])
            self.assertEqual([(c['tool'], c['blocked']) for c in row['tools_tried']],
                             [('vader', False), ('calendar_add_event', True)])
            self.assertEqual(row['failed_checks'], [])


if __name__ == '__main__':
    unittest.main()
