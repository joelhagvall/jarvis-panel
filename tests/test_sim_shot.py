"""jarvis_orb.py --then: a delayed boot step for simulator stills, since --page falls back to the dashboard."""
import json
from pathlib import Path
import subprocess
import sys
import unittest

GLASS = Path(__file__).resolve().parents[1] / 'styles/glass-v2'


class SimShotTest(unittest.TestCase):
    def test_then_appends_a_delayed_boot_step_and_keeps_the_others(self):
        action = [{"lvgl.page.show": {"id": "page_hemmet"}}]
        plain = json.loads(subprocess.run([sys.executable, 'jarvis_orb.py'], cwd=GLASS, check=True,
                                          capture_output=True, text=True).stdout)
        cfg = json.loads(subprocess.run([sys.executable, 'jarvis_orb.py', '--then', json.dumps(action)], cwd=GLASS,
                                        check=True, capture_output=True, text=True).stdout)
        before = plain['esphome']['on_boot']
        before = before if isinstance(before, list) else [before]
        boot = cfg['esphome']['on_boot']
        self.assertEqual(boot[:-1], before)
        self.assertEqual(boot[-1]['then'], [{"delay": "8s"}, *action])


if __name__ == '__main__':
    unittest.main()
