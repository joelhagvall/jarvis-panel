"""The shared post-install gate chain, night-mode exit and lock status, without a physical device."""
import contextlib
import io
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
import panel_gates
import panel_lock

FULL = {'type': 'object', 'clickable': True, 'x': 0, 'y': 0, 'width': 1024, 'height': 600}


class FakeClient:
    def __init__(self, pages, failures=0):
        self.pages, self.failures, self.taps = list(pages), failures, []

    def __enter__(self): return self
    def __exit__(self, *args): pass

    def status(self):
        if self.failures:
            self.failures -= 1
            raise OSError('rebooting')
        return {'page': self.pages[0] if len(self.pages) == 1 else self.pages.pop(0)}

    def ui(self):
        return {'widgets': [{**FULL, 'path': 'page/0'}, {**FULL, 'path': 'page/0/1'},
                            {'type': 'button', 'clickable': True, 'path': 'top/2', 'x': 0, 'y': 0, 'width': 100, 'height': 48}]}

    def tap_id(self, path):
        self.taps.append(path)
        self.pages = ['page_glass_v2']


class BuildClient(FakeClient):
    """A panel that answers with the builds in order (an OSError for None), then the last one."""
    def __init__(self, builds):
        super().__init__(['page_glass_v2'])
        self.builds = list(builds)

    def status(self):
        build = self.builds[0] if len(self.builds) == 1 else self.builds.pop(0)
        if build is None:
            raise OSError('rebooting')
        return {'page': 'page_glass_v2', 'build_id': build, 'reset_reason': 4 if build == 'old' else 3}


class GatesTest(unittest.TestCase):
    def test_a_rolled_back_panel_fails_at_build_before_any_gate(self):
        # 2026-10-28: the candidate aborted before ESPHome confirmed it, the panel booted the old
        # firmware, and the gates passed on that.
        out, ran, now = io.StringIO(), [], [0]
        code = panel_gates.run_gates('u', 5, lambda url: BuildClient(['old']), lambda args: ran.append(args) or 0,
                                     out, expect_build='new', clock=lambda: now[0],
                                     sleep=lambda s: now.__setitem__(0, now[0] + s))
        self.assertEqual(code, 3)
        self.assertEqual(ran, [])
        self.assertIn('runs old, not the flashed new (reset reason 4', out.getvalue())
        self.assertTrue(out.getvalue().rstrip().endswith('GATES FAILED at build (exit 3)'))

    def test_the_gates_wait_until_the_flashed_build_answers(self):
        # Right after an upload the old firmware answers for a few seconds; visual then lost its lease (HTTP 423).
        panel = BuildClient(['old', 'old', None, None, 'new'])
        out, ran = io.StringIO(), []
        code = panel_gates.run_gates('u', 30, lambda url: panel, lambda args: ran.append(args[0]) or 0, out,
                                     expect_build='new', sleep=lambda s: None)
        self.assertEqual(code, 0)
        self.assertEqual(ran[0], 'music-cover')   # the first gate
        self.assertIn('GATE build: ok (new)', out.getvalue())
    def test_rollback_during_a_passing_gate_stops_before_running_any_more_gates(self):
        panel=BuildClient(['new']);ran=[];out=io.StringIO()
        def runner(args):
            ran.append(args[0]);panel.builds=['old'];return 0
        self.assertEqual(panel_gates.run_gates('u',30,lambda url:panel,runner,out,expect_build='new'),3)
        self.assertEqual(ran,['music-cover'])
        self.assertIn('after music, panel runs old, expected new',out.getvalue())

    def test_the_gates_use_one_url_in_release_order(self):
        names = [name for name, _ in panel_gates.gate_commands('http://192.0.2.7:8080', 90)]
        self.assertEqual(names, ['music', 'visual', 'responsiveness', 'throughput', 'turntable', 'soak'])
        for _, args in panel_gates.gate_commands('http://192.0.2.7:8080', 90):
            self.assertIn('http://192.0.2.7:8080', args)
        self.assertIn('--require-soak', panel_gates.gate_commands('u', 1)[5][1])

    def test_url_is_resolved_once_and_ip_urls_are_kept(self):
        self.assertEqual(panel_gates.resolve_url('http://192.0.2.7:8080'), 'http://192.0.2.7:8080')
        resolver = lambda host, port, *a: [(None, None, None, None, ('192.0.2.9', port))]
        self.assertEqual(panel_gates.resolve_url('http://hallpanel.local:8080', resolver), 'http://192.0.2.9:8080')

    def test_night_is_left_with_the_real_full_screen_tap_after_reboot_reads(self):
        client = FakeClient(['page_natt'], failures=2)
        self.assertTrue(panel_gates.leave_night(client, 10, sleep=lambda s: None))
        self.assertEqual(client.taps, ['page/0/1'])
        day = FakeClient(['page_hemmet'])
        self.assertFalse(panel_gates.leave_night(day, 10))
        self.assertEqual(day.taps, [])

    def test_failed_gate_stops_the_chain_and_names_it(self):
        client = FakeClient(['page_natt'])
        calls = []
        runner = lambda args: calls.append(args) or (5 if args[:2] == ['throughput', '--url'] and '--seconds' not in args else 0)
        out = io.StringIO()
        code = panel_gates.run_gates('http://192.0.2.7:8080', 1, lambda url: client, runner, out)
        self.assertEqual(code, 5)
        self.assertEqual(len(calls), 4)   # music, visual, responsiveness, throughput
        self.assertEqual(client.taps, ['page/0/1'])
        self.assertEqual(out.getvalue().splitlines()[-1], 'GATES FAILED at throughput (exit 5)')
        self.assertIn('tools/panel host-link', out.getvalue())

    def test_all_gates_pass_and_report_ok_last(self):
        out = io.StringIO()
        code = panel_gates.run_gates('u', 1, lambda url: FakeClient(['page_glass_v2']), lambda args: 0, out)
        self.assertEqual(code, 0)
        self.assertEqual(out.getvalue().splitlines()[-1], 'GATES OK')

    def test_from_reruns_only_the_later_gates_and_says_it_is_partial(self):
        calls, out = [], io.StringIO()
        code = panel_gates.run_gates('u', 1, lambda url: FakeClient(['page_glass_v2']),
                                     lambda args: calls.append(args) or 0, out, start='throughput')
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 3)                 # throughput, turntable, soak
        self.assertIn('partial run', out.getvalue().splitlines()[-1])
        with self.assertRaises(ValueError):
            panel_gates.run_gates('u', 1, lambda url: FakeClient(['page_glass_v2']), lambda args: 0, out, start='nope')

    def test_a_panel_left_on_spelas_nu_goes_to_the_dashboard_before_each_gate(self):
        # 2026-10-28: throughput's test mode answered 409 twice because an agent had left Spelas nu open.
        class Panel(FakeClient):            # full screen, no tab bar: a swipe to the right reaches Hemmet
            def swipe(self, *args):
                self.taps.append(('swipe', *args))
                self.pages = ['page_hemmet']
        panel, out = Panel(['page_musik']), io.StringIO()
        self.assertEqual(panel_gates.run_gates('u', 1, lambda url: panel, lambda args: 0, out), 0)
        self.assertEqual(panel.taps, [('swipe', 220, 300, 820, 300, 350)])   # once: Hemmet stays for the rest
        self.assertIn('left Spelas nu with a swipe', out.getvalue())

    def test_night_is_restored_with_the_natt_tab_even_after_a_failed_gate(self):
        class Panel(FakeClient):
            def ui(self):
                if self.pages == ['page_glass_v2']:
                    return {'widgets': [{'path': 'top/0/1/3', 'type': 'button', 'clickable': True},
                                        {'path': 'top/0/1/3/0', 'type': 'label', 'text': 'Natt'}]}
                return super().ui()
            def tap_id(self, path):
                self.taps.append(path)
                self.pages = ['page_natt' if path == 'top/0/1/3' else 'page_glass_v2']
        panel, out = Panel(['page_natt']), io.StringIO()
        self.assertEqual(panel_gates.run_gates('u', 1, lambda url: panel, lambda args: 4, out), 4)
        self.assertEqual(panel.taps, ['page/0/1', 'top/0/1/3'])
        self.assertIn('Night mode restored', out.getvalue())
        self.assertEqual(out.getvalue().splitlines()[-1], 'GATES FAILED at music (exit 4)')
        # After an upload the panel boots to a day page; --restore-night still puts it back.
        rebooted = Panel(['page_glass_v2'])
        self.assertEqual(panel_gates.run_gates('u', 1, lambda url: rebooted, lambda args: 0, io.StringIO(), night=True), 0)
        self.assertEqual(rebooted.taps, ['top/0/1/3'])
        self.assertEqual(panel_gates.night_flag('u', lambda url: Panel(['page_natt'])), '--restore-night')
        self.assertEqual(panel_gates.night_flag('u', lambda url: Panel(['page_hemmet'])), '')

    def test_night_entered_during_a_gate_is_named_in_the_failure(self):
        state = {'n': 0}
        def factory(url):
            state['n'] += 1
            return FakeClient(['page_natt'] if state['n'] == 2 else ['page_glass_v2'])
        out = io.StringIO()
        self.assertEqual(panel_gates.run_gates('u', 1, factory, lambda args: 3, out), 3)
        self.assertIn('entered night mode during this gate', out.getvalue())


class LockStatusTest(unittest.TestCase):
    def test_restricted_process_inspection_never_labels_a_live_holder_dead(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'holder';path.write_text(json.dumps({'pid':123}))
            with patch('panel_lock.os.kill',side_effect=PermissionError('sandbox')), \
                 patch('subprocess.run',side_effect=PermissionError('sandbox')):
                self.assertTrue(panel_lock.holder(path)['alive'])
            with patch('panel_lock.os.kill',return_value=None), \
                 patch('subprocess.run',side_effect=PermissionError('sandbox')):
                self.assertTrue(panel_lock.holder(path)['alive'])
            with patch('panel_lock.os.kill',side_effect=ProcessLookupError()):
                self.assertFalse(panel_lock.holder(path)['alive'])

    def test_status_names_the_live_holder_and_wait_times_out(self):
        with tempfile.TemporaryDirectory() as tmp:
            code = ('import sys; sys.path.insert(0, sys.argv[2]); from panel_lock import PanelLock; '
                    'lock=PanelLock("device",root=sys.argv[1]); lock.__enter__(); print("held",flush=True); sys.stdin.read()')
            child = subprocess.Popen([sys.executable, '-c', code, tmp, str(ROOT / 'tools')], stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, text=True)
            try:
                self.assertEqual(child.stdout.readline().strip(), 'held')
                out = io.StringIO()
                with contextlib.redirect_stdout(out):
                    self.assertEqual(panel_lock.report(['device', 'workspace'], root=tmp), 0)
                rows = [json.loads(line) for line in out.getvalue().splitlines()]
                self.assertTrue(rows[0]['held'])
                self.assertEqual(rows[0]['holder']['pid'], child.pid)
                self.assertTrue(rows[0]['holder']['alive'])
                self.assertFalse(rows[1]['held'])
                ticks = iter(range(100))
                with contextlib.redirect_stdout(io.StringIO()):
                    self.assertEqual(panel_lock.report(['device'], wait=True, max_wait=2, root=tmp,
                                                       clock=lambda: next(ticks), sleep=lambda s: None), 1)
                with self.assertRaisesRegex(RuntimeError, f'pid {child.pid}'):
                    with panel_lock.PanelLock('device', root=tmp): pass
            finally:
                child.kill(); child.wait(); child.stdin.close(); child.stdout.close()
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(panel_lock.report(['device'], wait=True, max_wait=0, root=tmp), 0)
            # Probing never creates a missing lock file.
            self.assertFalse((Path(tmp) / 'workspace.lock').exists())


if __name__ == '__main__':
    unittest.main()
