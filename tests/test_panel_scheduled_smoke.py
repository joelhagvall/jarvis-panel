import sys
from pathlib import Path
import unittest
import tempfile
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from panel_scheduled_smoke import idle_reason, scheduled


class ScheduledSmokeTest(unittest.TestCase):
    def status(self):
        return dict(page='page_hemmet',test_mode='idle',voice_active=False,alarm_active=False,
                    announcement_active=False,physical_touch_active=False,boot_healthy=True,
                    physical_touch_idle_ms=600000,internal_free=300000,psram_free=18000000)

    def test_unknown_or_active_critical_state_never_runs_touch(self):
        good = self.status()
        self.assertIsNone(idle_reason(good, 300))
        for field in ('voice_active','alarm_active','announcement_active','physical_touch_active'):
            for value in (True,None):
                current = dict(good, **{field:value})
                self.assertIsNotNone(idle_reason(current,300))
        self.assertIsNotNone(idle_reason(dict(good,physical_touch_idle_ms=0),300))

    def test_rechecks_idle_after_acquiring_lease(self):
        client = MagicMock()
        client.status.side_effect = [self.status(), dict(self.status(),voice_active=True)]
        with tempfile.TemporaryDirectory() as tmp, patch('panel_scheduled_smoke.ROOT',Path(tmp)), patch('panel_scheduled_smoke.run') as run:
            result = scheduled(client,Path('/unused'))
        self.assertFalse(result['performed'])
        run.assert_not_called()

    def test_quarantine_never_reads_or_operates_device(self):
        client = MagicMock()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'.panel-ci').mkdir();(root/'.panel-ci/quarantine.json').write_text('{}')
            with patch('panel_scheduled_smoke.ROOT',root):
                result=scheduled(client,root/'evidence')
        self.assertFalse(result['performed'])
        client.status.assert_not_called()
        client.exclusive.assert_not_called()


if __name__ == '__main__':
    unittest.main()
