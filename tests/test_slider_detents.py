"""Settings touch detents preserve bounds, restored values and matching displayed/saved values."""
import importlib.util
import asyncio
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, AsyncMock, MagicMock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('detents_gen', ROOT / 'styles/glass-v2/gen.py')
gen = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gen)
sys.path.insert(0, str(ROOT / 'tools'))
from panel_ui_test import brightness_targets, restore_brightness, _native_brightness


class SliderDetentsTest(unittest.TestCase):
    def test_exact_native_snapshot_and_restore_only_address_the_backlight(self):
        from aioesphomeapi import LightInfo, LightState
        with tempfile.TemporaryDirectory() as tmp:
            folder=Path(tmp)/'styles/glass-v2';folder.mkdir(parents=True)
            (folder/'secrets.yaml').write_text('api_key: private-test-key\n')
            api=MagicMock()
            api.connect=AsyncMock();api.disconnect=AsyncMock()
            api.list_entities_services=AsyncMock(return_value=([LightInfo(key=7,name='Skärmljus')],[]))
            callback=[]
            def subscribe(cb):
                callback.append(cb);cb(LightState(key=7,state=True,brightness=.51))
            api.subscribe_states.side_effect=subscribe
            def command(key, **kw):
                callback[-1](LightState(key=key,state=True,brightness=kw['brightness']))
            api.light_command.side_effect=command
            with patch('panel_ui_test.ROOT',Path(tmp)), patch('aioesphomeapi.APIClient',return_value=api):
                self.assertEqual(asyncio.run(_native_brightness('http://device:8080')), .51)
                api.light_command.assert_not_called()
                self.assertEqual(asyncio.run(_native_brightness('http://device:8080',.57)), .57)
                api.light_command.assert_called_once_with(7,state=True,brightness=.57,transition_length=0)
                self.assertEqual(api.disconnect.await_count,2)

    def test_physical_suite_selects_detents_including_existing_off_step_values(self):
        for old in (5, 20, 50, 51, 57, 95, 100):
            targets = brightness_targets({'value': old, 'min': 5, 'max': 100})
            self.assertEqual(targets[-2:], [(48, 50), (52, 50)])
            self.assertNotEqual(targets[0][0], old)
            self.assertEqual(targets[0][0] % 5, 0)

    def test_restoration_refuses_a_restart_real_touch_or_alarm_before_native_write(self):
        before = {'boot_id':'boot', 'uptime_ms':10000}
        for changed in ({'boot_id':'new'}, {'physical_touch_active':True}, {'alarm_active':True},
                        {'voice_active':True}, {'physical_touch_idle_ms':100}):
            state = {'boot_id':'boot','uptime_ms':12000,'physical_touch_idle_ms':12000, **changed}
            class Client:
                def status(self):return state
            with patch('panel_ui_test._native_brightness') as write:
                with self.assertRaisesRegex(RuntimeError, 'preempted'):
                    restore_brightness(Client(), .51, before)
                write.assert_not_called()

    def test_every_percentage_reaches_nearest_stop_with_original_safety_bounds(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'detents.cpp', Path(tmp) / 'detents'
            source.write_text('''#include "slider_detents.h"
#include <cassert>
int main() {
  for (const int minimum : {0, 1, 5}) {
    int previous = minimum;
    for (int raw = -10; raw <= 110; ++raw) {
      const int value = slider_detents::nearest(raw, minimum);
      assert(value >= minimum && value <= 100);
      assert(value == minimum || value % 5 == 0);
      assert(value >= previous);
      assert(slider_detents::nearest(value, minimum) == value);
      if (raw >= minimum && raw <= 100) assert(value - raw <= 2 && raw - value <= 2);
      previous = value;
    }
    for (int raw = 48; raw <= 52; ++raw) assert(slider_detents::nearest(raw, minimum) == 50);
    assert(slider_detents::nearest(47, minimum) == 45);
    assert(slider_detents::nearest(53, minimum) == 55);
  }
}'''.replace('#include <cassert>', '#include <cassert>\n#include <initializer_list>'))
            subprocess.run(['c++', '-std=c++17', '-I', str(ROOT / 'styles/glass-v2'), str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)

    def test_all_three_sliders_snap_touch_preview_and_committed_value(self):
        for key, icon, title, value, minimum in [('bright', 'sun', 'Skärmljus', 100, 5),
                                               ('nightl', 'moon', 'Nattljus', 12, 1),
                                               ('vol', 'volume', 'Volym', 20, 0)]:
            row = gen.slider_row(key, icon, title, value, minimum)['obj']
            slider = next(widget['slider'] for widget in row['widgets'] if 'slider' in widget)
            change, release = slider['on_value'][0]['lambda'], slider['on_release'][0]['lambda']
            self.assertIn('lv_slider_is_dragged', change)  # opening settings must not round a saved 12%
            self.assertIn('slider_detents::nearest', change)
            self.assertIn('slider_detents::nearest', release)
            self.assertIn(f'id({key}_pct) = value;', change)
            self.assertIn(f'id({key}_pct) = value;', release)
            self.assertEqual(slider['on_release'][1], {'script.execute': f'{key}_set'})
            self.assertEqual((slider['min_value'], slider['max_value']), (minimum, 100))


if __name__ == '__main__':
    unittest.main()
