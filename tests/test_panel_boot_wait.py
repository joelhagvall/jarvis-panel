"""Read-only boot retries must not turn transport recovery into a false success."""
from contextlib import ExitStack
from pathlib import Path
import socket
import sys
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_ci as ci
import panel_remote


class Clock:
    now = 0
    def monotonic(self): return self.now
    def sleep(self, seconds): self.now += seconds


def status(**changes):
    return dict(build_id='candidate', boot_id=2, uptime_ms=65000, boot_healthy=True) | changes


def client(*values):
    obj = Mock()
    obj.status.side_effect = values
    return obj


class BootWaitTest(unittest.TestCase):
    def run_wait(self, factory, seconds=30):
        clock = Clock()
        with ExitStack() as stack:
            stack.enter_context(patch.object(ci.time, 'monotonic', clock.monotonic))
            stack.enter_context(patch.object(ci.time, 'sleep', clock.sleep))
            stack.enter_context(patch.object(panel_remote, 'PanelClient', factory))
            return ci.wait_boot('http://panel.invalid', 'candidate', {'boot_id': 1}, seconds)

    def test_missing_before_identity_cannot_certify_a_new_boot(self):
        with patch.object(panel_remote,'PanelClient') as factory:
            with self.assertRaisesRegex(RuntimeError,'pre-restart boot identity'):
                ci.wait_boot('http://panel.invalid','candidate',{})
        factory.assert_not_called()

    def test_constructor_dns_failure_retries_and_closes_the_successful_client(self):
        good = client(status())
        factory = Mock(side_effect=[socket.gaierror('temporary name failure'), good])
        result = self.run_wait(factory)
        self.assertTrue(result['ok']); self.assertEqual(result['read_errors'], 1)
        self.assertEqual(factory.call_count, 2); good.close.assert_called_once()

    def test_transient_read_failure_reopens_without_reusing_a_dead_connection(self):
        broken, good = client(ConnectionResetError()), client(status())
        factory = Mock(side_effect=[broken, good])
        self.assertEqual(self.run_wait(factory)['read_errors'], 1)
        broken.close.assert_called_once(); good.close.assert_called_once()

    def test_same_candidate_old_boot_is_never_accepted_as_the_new_boot(self):
        old = status(); old['boot_id'] = 1
        good = client(old, status())
        self.assertEqual(self.run_wait(Mock(return_value=good))['status']['boot_id'], 2)
        good.close.assert_called_once()

    def test_old_boot_grace_period_expires_even_when_healthy(self):
        old = status(); old['boot_id'] = 1
        good = Mock(); good.status.return_value = old
        with self.assertRaisesRegex(RuntimeError, 'fresh boot'): self.run_wait(Mock(return_value=good))
        good.close.assert_called_once()

    def test_wrong_new_firmware_fails_immediately_without_retry(self):
        wrong = status(); wrong['build_id'] = 'other'
        good = client(wrong); factory = Mock(return_value=good)
        with self.assertRaisesRegex(RuntimeError, 'firmware identity'): self.run_wait(factory)
        self.assertEqual(good.status.call_count, 1); good.close.assert_called_once()

    def test_changed_boot_after_read_gap_fails_even_if_uptime_increases(self):
        first = status(); first.update(uptime_ms=1000, boot_healthy=False)
        restarted = status(); restarted['boot_id'] = 3
        broken, good = client(first, OSError()), client(restarted)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected restart'):
            self.run_wait(Mock(side_effect=[broken, good]))
        broken.close.assert_called_once(); good.close.assert_called_once()

    def test_same_boot_decreasing_uptime_fails(self):
        first, second = status(), status(); first.update(uptime_ms=50000, boot_healthy=False)
        second['uptime_ms'] = 40000
        good = client(first, second)
        with self.assertRaisesRegex(RuntimeError, 'Unexpected restart'): self.run_wait(Mock(return_value=good))
        good.close.assert_called_once()

    def test_minimum_age_and_real_health_boolean_still_required(self):
        early, false_health = status(), status(); early['uptime_ms'] = 64999
        false_health['boot_healthy'] = 'true'
        good = client(early, false_health, status())
        result = self.run_wait(Mock(return_value=good)); self.assertTrue(result['ok'])
        self.assertEqual(good.status.call_count, 3)

    def test_missing_identity_or_invalid_uptime_fails_closed(self):
        for changes, message in (({'boot_id': None}, 'Missing boot'), ({'uptime_ms': '65000'}, 'Invalid uptime')):
            s = status(); s.update(changes); good = client(s)
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, message):
                self.run_wait(Mock(return_value=good))
            good.close.assert_called_once()

    def test_persistent_dns_failure_exhausts_budget_without_device_input_or_fallback(self):
        factory = Mock(side_effect=socket.gaierror('not found'))
        with self.assertRaisesRegex(RuntimeError, 'health not proven'): self.run_wait(factory, seconds=3)
        self.assertEqual(factory.call_count, 3)
        self.assertTrue(all(call.args == ('http://panel.invalid',) for call in factory.call_args_list))

if __name__ == '__main__': unittest.main()
