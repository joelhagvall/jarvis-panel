"""Physical-suite control flow: stop on failure, restore on every path, require expiry."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_ui_test as suite


class Client:
    def __init__(self, fail=None, expires=True, latency=0):
        self.page = self.original = 'page_hemmet'
        self.mode = 'idle'
        self.now = 0
        self.deadline = None
        self.calls = []
        self.fail = fail
        self.expires = expires
        self.latency = latency
        self.chat_y = 310

    def test_mode(self, mode, duration_ms=10000):
        self.calls.append(mode)
        self.mode = mode
        if mode == 'restore':
            self.page = self.original
            self.deadline = None
        else:
            self.page = {'night': 'page_natt', 'ota': 'page_ota', 'wifi_disconnected': 'page_glass_v2',
                         'ha_disconnected': 'page_glass_v2', 'missing_image': 'page_tavla',
                         'chat': 'page_glass_v2', 'chat_scroll': 'page_glass_v2'}.get(mode, 'page_orb')
            self.deadline = self.now + duration_ms / 1000
        self.now += self.latency

    def status(self):
        self.now += self.latency
        if self.expires and self.deadline is not None and self.now >= self.deadline:
            self.page = self.original
        return {'page': self.page}

    def ui(self):
        if self.mode == 'chat_scroll':
            return {'widgets': [{'path': 'screen/7/2', 'x': 536, 'y': 310, 'width': 444, 'height': 126},
                                {'path': 'screen/7/2/0/1/0/0', 'text': 'First paragraph: a local test', 'y': self.chat_y}]}
        labels = ['Jag lyssnar…', 'Test av Hallpanel', 'Det här är ett lokalt test utan ljud eller modell.',
                  'Jarvis uppdaterar sig', 'Nätet är nere – timers och larm fungerar',
                  'Ingen kontakt med Jarvis', 'Det gick inte just nu.', 'Of course. We can continue in English.']
        return {'widgets': [{'text': label} for label in labels]}

    def swipe(self, x1, y1, x2, y2, duration, settle):
        self.chat_y += y2 - y1

    def inspect(self, folder):
        if self.mode == self.fail:
            raise OSError('capture failed')
        return {'path': str(folder)}

    def sleep(self, seconds):
        self.now += seconds


class SyntheticSuiteTest(unittest.TestCase):
    def test_tab_selection_rechecks_the_page_after_tavla_swipe(self):
        class Tabs:
            page='page_tavla'
            def status(self):return {'page':self.page}
            def tap_text(self,text):
                if text=='Hemmet' and self.page=='page_hemmet':
                    raise AssertionError('Hemmet title and tab are ambiguous')
                self.page={'Hemmet':'page_hemmet','Gå hemifrån':'page_glass_v2'}[text]
        client=Tabs()
        def leave_frame(client):client.page='page_hemmet'
        with patch.object(suite,'bar_page',side_effect=leave_frame):
            suite.select_tab(client,'page_hemmet')
            suite.select_tab(client,'page_hemmet')
            suite.select_tab(client,'page_glass_v2')
        self.assertEqual(client.page,'page_glass_v2')

    def run_suite(self, client, folder):
        with patch.object(suite.time, 'sleep', client.sleep), \
                patch.object(suite.time, 'monotonic', lambda: client.now):
            return suite.run_test_mode(client, folder)

    def test_all_modes_restore_the_page_and_expiry_is_observed(self):
        client = Client()
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'run'
            report = self.run_suite(client, folder)
            self.assertTrue(report['ok'])
            self.assertFalse(report['actual_audio'])
            self.assertFalse(report['actual_network_faults'])
            self.assertEqual(client.page, client.original)
            self.assertEqual(len(report['checks']), 12)
            self.assertEqual((folder / 'report.json').stat().st_mode & 0o777, 0o600)

    def test_capture_failure_stops_later_modes_restores_and_writes_failure_evidence(self):
        client = Client(fail='thinking')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'run'
            with self.assertRaises(OSError): self.run_suite(client, folder)
            self.assertEqual(client.page, client.original)
            self.assertNotIn('speaking', client.calls)
            self.assertEqual(client.calls[-1], 'restore')
            self.assertFalse(json.loads((folder / 'report.json').read_text())['ok'])

    def test_missing_expiry_fails_and_restores_explicitly(self):
        client = Client(expires=False)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'run'
            with self.assertRaises(AssertionError): self.run_suite(client, folder)
            self.assertEqual(client.page, client.original)
            self.assertFalse(json.loads((folder / 'report.json').read_text())['ok'])

    def test_broken_chat_scroll_fails_and_restores_without_masking_the_failure(self):
        client = Client()
        client.swipe = lambda *args, **kwargs: None
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(AssertionError, 'Chat did not scroll'):
                self.run_suite(client, Path(tmp) / 'run')
        self.assertEqual(client.page, client.original)
        self.assertEqual(client.calls[-1], 'restore')

    def test_expiry_remains_observable_with_slow_http_round_trips(self):
        client = Client(latency=1.1)
        with tempfile.TemporaryDirectory() as tmp:
            report = self.run_suite(client, Path(tmp) / 'run')
        self.assertTrue(report['ok'])
        self.assertEqual(report['checks'][-1]['duration_ms'], 5000)
        self.assertEqual(client.page, client.original)


if __name__ == '__main__':
    unittest.main()
