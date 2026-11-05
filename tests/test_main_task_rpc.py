"""The C6 signal-strength RPC (~30 ms on this board) may run only on panel_debug's sampling task.

ESPHome's wifi_signal sensor ran it on the UI task once a minute, which repeated frames and
delayed touch handling. This guards the generator and the firmware sources against it coming back.
"""
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
RPC = re.compile(r'esp_wifi_sta_get_ap_info|wifi_rssi\s*\(')


def board_config():
    env = {k: v for k, v in os.environ.items() if not k.startswith(('JARVIS_', 'HALLPANEL_'))}
    env['HALLPANEL_BOARD'] = '1'
    out = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py', '--remote',
                          'http://jarvis.local:8123/local/hallpanel'], cwd=ROOT / 'styles/glass-v2',
                         check=True, capture_output=True, text=True, env=env).stdout
    return json.loads(out)


class MainTaskRpcGuard(unittest.TestCase):
    def test_firmware_sources_query_signal_strength_only_in_the_sampling_task(self):
        hits = []
        for folder, pattern in ((ROOT / 'components', '**/*.cpp'), (ROOT / 'components', '**/*.h'),
                                (ROOT / 'bench', '*.h'), (ROOT / 'styles/glass-v2', '*.h'), (ROOT, 'board.py'),
                                (ROOT / 'board_config', '*.py')):
            for path in sorted(folder.glob(pattern)):
                for number, line in enumerate(path.read_text().splitlines(), 1):
                    if RPC.search(line):
                        hits.append((path.relative_to(ROOT).as_posix(), number, line.strip()))
        self.assertEqual([hit[0] for hit in hits], ['components/panel_debug/panel_debug.cpp'], hits)
        source = (ROOT / 'components/panel_debug/panel_debug.cpp').read_text()
        task = source[source.index('void PanelDebug::radio_task_('):]
        task = task[:task.index('\n}\n') + 3]
        self.assertEqual(len(RPC.findall(source)), len(RPC.findall(task)), 'RPC outside radio_task_')
        self.assertIn('xTaskCreatePinnedToCoreWithCaps(radio_task_', source)

    def test_board_config_uses_the_sampled_signal_strength_not_the_wifi_signal_platform(self):
        cfg = board_config()
        platforms = [s.get('platform') for s in cfg.get('sensor', [])]
        self.assertNotIn('wifi_signal', platforms)
        sensor = next(s for s in cfg['sensor'] if s.get('name') == 'Wi-Fi-signal')
        self.assertEqual(sensor['platform'], 'template')
        self.assertIn('radio_rssi()', sensor['lambda'])
        self.assertEqual((sensor['unit_of_measurement'], sensor['device_class'], sensor['entity_category']),
                         ('dBm', 'signal_strength', 'diagnostic'))
        # The orb's production images follow the display frame clock on the board.
        boot = json.dumps(cfg['esphome']['on_boot'])
        self.assertIn('packed_orb::timelines.set_clock(&direct_display::clock)', boot)
        self.assertIn('responsiveness::recorder.clock = &direct_display::clock', boot)
        self.assertNotIn('lvgl.animimg.start', json.dumps(cfg))


if __name__ == '__main__':
    unittest.main()
