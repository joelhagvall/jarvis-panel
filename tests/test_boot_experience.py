"""Boot work must overlap safely, retain exact pixels, and never delay readiness for branding."""
import json
from collections import deque
from itertools import product
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import unittest

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
from panel_image import convert


class BootExperienceTest(unittest.TestCase):
    def board(self):
        result = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py', '--remote',
                                 'http://example.invalid/assets'], cwd=ROOT / 'styles/glass-v2',
                                check=True, capture_output=True, text=True)
        return json.loads(result.stdout)

    def test_image_success_callback_releases_its_socket_before_the_next_download(self):
        # ESPHome OnlineImage invokes on_download_finished before end_connection_.
        # Model one remaining image socket while API/debug/atlas own the others.
        cfg = self.board()
        images = {im['id']: im for im in cfg['image'] if im.get('platform') == 'online_image'}
        pending, completed, failures = deque(), [], []
        active = 0

        def execute(actions):
            for i, action in enumerate(actions):
                if 'if' in action:
                    execute(action['if'].get('else', []))  # initial boot: assets_loaded is false
                elif 'delay' in action:
                    pending.append(lambda actions=actions[i + 1:]: execute(actions))
                    return
                elif 'component.update' in action:
                    download(action['component.update'])

        def download(name):
            nonlocal active
            if active:
                failures.append(name)
                return
            active += 1
            def finish():
                nonlocal active
                completed.append(name)
                execute(images[name]['on_download_finished'])
                active -= 1  # actual OnlineImage closes only after callbacks return
            pending.append(finish)

        download('bg')
        while pending:
            pending.popleft()()
        self.assertEqual(failures, [], 'Callback opened the next image before closing its own socket')
        self.assertEqual(completed, ['bg', 'art', 'voice_bg'])

    def test_ready_never_reports_a_missing_image_or_failed_orb_install_as_success(self):
        cfg = self.board()
        ready = next(s for s in cfg['script'] if s['id'] == 'assets_ready')['then'][0]['if']
        condition = ready['condition']['lambda'].removeprefix('return ').removesuffix(';')
        expression = re.sub(r'id\((\w+)\)', r'\1', condition).replace('&&', ' and ').replace('!', ' not ')
        keys = ['assets_loaded', 'backgrounds_loaded', 'orb_loaded', 'bg_valid', 'art_valid', 'voice_bg_valid']
        for bits in product((False, True), repeat=len(keys)):
            state = dict(zip(keys, bits))
            expected = not state['assets_loaded'] and all(state[k] for k in keys[1:])
            self.assertEqual(bool(eval(expression.strip(), {'__builtins__': {}}, state)), expected, state)

    def test_initial_image_failures_retry_only_three_times_and_always_yield(self):
        cfg = self.board()
        images = {im['id']: im for im in cfg['image'] if im.get('platform') == 'online_image'}
        state = {f'{name}_attempts': 0 for name in images}
        pending, calls = deque(), []

        def execute(actions):
            for i, action in enumerate(actions):
                if 'if' in action:
                    branch = action['if']
                    condition = branch['condition']
                    if 'not' in condition:
                        passed = True  # assets_loaded stays false when every HTTP request fails
                    else:
                        expression = re.sub(r'id\((\w+)\)', r'\1', condition['lambda'][7:-1])
                        passed = eval(expression, {'__builtins__': {}}, state)
                    execute(branch.get('then' if passed else 'else', []))
                elif 'lambda' in action:
                    match = re.fullmatch(r'\+\+id\((\w+_attempts)\);', action['lambda'])
                    if match:
                        state[match[1]] += 1
                elif 'delay' in action:
                    pending.append(lambda actions=actions[i + 1:]: execute(actions))
                    return
                elif 'component.update' in action:
                    download(action['component.update'])

        def download(name):
            calls.append(name)
            if len(calls) > 9:
                self.fail('Unbounded image retry')
            # The real error path closes before invoking on_error; immediate
            # callbacks make a missing delay recurse instead of safely yielding.
            execute(images[name]['on_error'])

        download('bg')
        self.assertEqual(calls, ['bg'])
        while pending:
            pending.popleft()()
        self.assertEqual(calls, ['bg'] * 3 + ['art'] * 3 + ['voice_bg'] * 3)

    def test_lossless_transport_preserves_every_prepared_pixel(self):
        # Gradients and independent colours expose channel shifts/quantization.
        rng = np.random.default_rng(17)
        pixels = rng.integers(0, 256, (64, 256, 3), dtype=np.uint8)
        pixels[:16] = np.arange(256, dtype=np.uint8)[None, :, None]
        with tempfile.TemporaryDirectory() as tmp:
            source, png = Path(tmp) / 'source.png', Path(tmp) / 'prepared.png'
            Image.fromarray(pixels).save(source)
            convert(source, png)
            with Image.open(png) as p, Image.open(png.with_suffix('.qoi')) as q:
                self.assertEqual(q.size, (256, 64))
                self.assertEqual(p.tobytes(), q.tobytes())

    def test_generated_boot_downloads_concurrently_then_joins_both_completion_orders(self):
        output = subprocess.run([sys.executable, str(ROOT / 'board.py'), '--voice', 'jarvis_orb.py', '--remote',
                                 'http://example.invalid/assets'], cwd=ROOT / 'styles/glass-v2',
                                check=True, capture_output=True, text=True).stdout
        cfg = json.loads(output)
        boot = next(b for b in cfg['esphome']['on_boot'] if any(
            a.get('component.update') == 'bg' for a in b.get('then', [])))['then']
        self.assertIn('wifi.connected', json.dumps(boot[0]))
        self.assertEqual(boot[1], {'script.execute': 'orb_load'})
        self.assertEqual(boot[2], {'component.update': 'bg'})
        scripts = {s['id']: s for s in cfg['script']}
        joined = scripts['assets_ready']['then'][0]['if']
        self.assertEqual(joined['condition']['lambda'],
                         'return !id(assets_loaded) && id(backgrounds_loaded) && id(orb_loaded) && '
                         'id(bg_valid) && id(art_valid) && id(voice_bg_valid);')
        # Both inputs independently notify the join; completion cannot depend on
        # which HTTP request wins. Later background refreshes use their own path.
        self.assertIn('assets_ready', json.dumps(scripts['orb_load']))
        images = {im['id']: im for im in cfg['image']}
        self.assertIn('backgrounds_loaded', json.dumps(images['voice_bg']['on_download_finished']))
        self.assertIn('assets_ready', json.dumps(images['voice_bg']['on_download_finished']))
        self.assertEqual(images['bg']['format'], 'QOI')
        self.assertTrue(all(images[name]['buffer_size'] == 8192 for name in ('bg', 'art', 'voice_bg')))
        self.assertNotIn('boot_lbl', json.dumps(cfg['api']))
        self.assertNotIn('boot_lbl', json.dumps(cfg['wifi']))
        light = next(b for b in cfg['esphome']['on_boot'] if b['priority'] == 390)['then'][0]['lambda']
        self.assertLess(light.index('lv_refr_now'), light.index('turn_on'))
        self.assertIn('set_transition_length(0)', light)
        self.assertLess(light.index('call.perform()'), light.index('get_output()->write_state'))
        self.assertLess(light.index('get_output()->write_state'), light.index('id(boot_light_ms)'))
        self.assertNotIn('id(backlight).loop()', light)
        self.assertIn('LV_EVENT_REFR_READY', light)
        brand = next(b for b in cfg['esphome']['on_boot'] if b['priority'] == 394)
        self.assertIn('boot_brand::image()', json.dumps(brand))
        self.assertNotIn('boot_logo', images)  # Do not retain an uncompressed duplicate in XIP PSRAM.
        self.assertEqual(cfg['light'][0]['restore_mode'], 'ALWAYS_OFF')
        self.assertNotIn('delay', json.dumps(scripts['boot_done']))
        self.assertIn('boot_bg_ms', output)
        self.assertIn('boot_orb_ms', output)
        self.assertIn('boot_ui_ms', output)

    def test_dashboard_reveal_never_dims_backlight_and_retains_alarm_priority(self):
        cfg = self.board()
        scripts = {s['id']: s for s in cfg['script']}
        actions = scripts['boot_done']['then'][0]['if']['then']
        reveal = next(i for i,a in enumerate(actions) if a.get('script.execute') == 'boot_fade')
        rendered = next(i for i,a in enumerate(actions)
                        if a.get('wait_until',{}).get('condition',{}).get('lambda') == 'return id(boot_shown_ms) != 0;')
        self.assertLess(reveal, rendered)
        transition = scripts['boot_fade']['then']
        self.assertEqual(len(transition), 1)
        self.assertIn('boot_reveal::show(id(boot_cover)', transition[0]['lambda'])
        combined = json.dumps((scripts['boot_done'], scripts['boot_fade']))
        for forbidden in ('set_brightness', 'backlight', 'set_transition_length', 'lv_refr_now', 'lv_anim'):
            self.assertNotIn(forbidden, combined)
        light = next(b for b in cfg['esphome']['on_boot'] if b['priority'] == 390)['then'][0]['lambda']
        self.assertIn('lv_obj_has_flag(id(boot_cover), LV_OBJ_FLAG_HIDDEN)', light)
        self.assertNotIn('get_style_bg_opa', light)



class BootSplashColourTest(unittest.TestCase):
    def test_splash_background_is_the_logo_border_colour_on_the_panel(self):
        # The opaque 320x320 logo must not show as a box: compare RGB565, which is what the panel displays.
        from collections import Counter
        # Same source and resize as tools/pack_boot_logo.py.
        source = Image.open(ROOT / 'assets/branding/jarvis-neural-orb-v1.png').convert('RGB').resize((320, 320))
        q = lambda p: (p[0] >> 3, p[1] >> 2, p[2] >> 3)
        border = Counter(q(source.getpixel((x, y))) for x in range(320) for y in (0, 319))
        border.update(q(source.getpixel((x, y))) for y in range(320) for x in (0, 319))
        logo_bg, count = border.most_common(1)[0]
        self.assertGreater(count, 0.99 * sum(border.values()))
        sys.path.insert(0, str(ROOT / 'styles/glass-v2'))
        import jarvis_orb
        value = int(jarvis_orb.BOOT_BG, 16)
        self.assertEqual(q(((value >> 16) & 255, (value >> 8) & 255, value & 255)), logo_bg)


class DirectDisplayInstallTest(unittest.TestCase):
    def test_install_never_clears_the_buffer_that_shows_the_splash(self):
        # 2026-10-26: install() zeroed both DPI frame buffers ~2.6 s into boot, while the splash was on screen,
        # so the panel went black until LVGL's next frame was presented (the logo "blinked").
        text = (ROOT / 'bench/direct_display.h').read_text()
        body = text[text.index('inline void install('):]
        body = body[:body.index('\n}\n')]
        code = '\n'.join(line.split('//')[0] for line in body.splitlines())
        for forbidden in ('memset', 'fill(', 'memcpy', 'esp_lcd_panel_draw_bitmap'):
            self.assertNotIn(forbidden, code)
        self.assertIn('lv_obj_invalidate(lv_screen_active())', code)   # LVGL still redraws every pixel

if __name__ == '__main__':
    unittest.main()
