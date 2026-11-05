"""Free visual-regression checks: prove real defects fail and live content stays out of the comparison."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_visual as visual


class VisualPixelsTest(unittest.TestCase):
    def setUp(self):
        self.policy = json.loads(visual.POLICY_PATH.read_text())
        self.mask = visual.pixel_mask(self.policy['size'], self.policy['scenes']['settings_dashboard']['pixels'])
        self.good = Image.new('RGB', (1024, 600), (16, 24, 32))
        draw = ImageDraw.Draw(self.good)
        draw.rectangle((258, 136, 325, 152), fill='white')

    def diff(self, actual):
        return visual.pixel_diff(self.good, actual, self.mask, self.policy['channel_tolerance'],
                                 self.policy['max_changed_pixels'])[0]

    def test_identical_capture_passes_with_substantial_explicit_pixel_coverage(self):
        result = self.diff(self.good.copy())
        self.assertTrue(result['ok'])
        self.assertEqual(result['changed_pixels'], 0)
        self.assertGreater(result['checked_pixels'], 180000)

    def test_missing_static_label_pixels_and_shifted_text_fail(self):
        missing = self.good.copy()
        ImageDraw.Draw(missing).rectangle((258, 136, 325, 152), fill=(16, 24, 32))
        self.assertFalse(self.diff(missing)['ok'])
        shifted = missing.copy()
        ImageDraw.Draw(shifted).rectangle((260, 136, 327, 152), fill='white')
        self.assertFalse(self.diff(shifted)['ok'])

    def test_reintroduced_transparency_and_wrong_color_fail(self):
        actual = self.good.copy()
        ImageDraw.Draw(actual).text((490, 195), '08:30', fill='gray')   # Nattljus's name row, not a masked slider
        self.assertFalse(self.diff(actual)['ok'])
        actual = Image.new('RGB', self.good.size, (40, 50, 60))
        self.assertFalse(self.diff(actual)['ok'])

    def test_live_background_values_sliders_and_microphone_state_are_masked(self):
        actual = self.good.copy()
        draw = ImageDraw.Draw(actual)
        draw.rectangle((0, 0, 180, 599), fill='red')
        for x1, y1, x2, y2 in self.policy['scenes']['settings_dashboard']['pixels']['exclude']:
            draw.rectangle((x1, y1, x2 - 1, y2 - 1), fill='red')
        self.assertTrue(self.diff(actual)['ok'])

    def test_small_rgb565_rounding_is_tolerated_but_empty_masks_and_wrong_size_fail(self):
        close = Image.new('RGB', self.good.size, (24, 28, 40))
        ImageDraw.Draw(close).rectangle((258, 136, 325, 152), fill='white')
        self.assertTrue(self.diff(close)['ok'])
        with self.assertRaises(ValueError):
            visual.pixel_diff(self.good, self.good, Image.new('L', self.good.size), 8, 8)
        with self.assertRaises(ValueError):
            self.diff(Image.new('RGB', (100, 100)))


def settings_ui():
    return {'page': 'page_glass_v2', 'widgets': [
        {'path': 'top/1', 'type': 'object', 'x': 0, 'y': 0, 'width': 1024, 'height': 600, 'clickable': True},
        {'path': 'top/1/0', 'type': 'object', 'x': 192, 'y': 51, 'width': 640, 'height': 450, 'clickable': True},
        {'path': 'top/1/0/1/1', 'type': 'label', 'x': 257, 'y': 135, 'width': 75, 'height': 19,
         'text': 'Skärmljus', 'clickable': False},
        {'path': 'top/1/0/1/2', 'type': 'label', 'x': 765, 'y': 135, 'width': 40, 'height': 19, 'text': '23 %'},
        {'path': 'top/1/0/1/3', 'type': 'slider', 'x': 235, 'y': 174, 'width': 556, 'height': 8,
         'value': 23, 'min': 5, 'max': 100, 'clickable': True}]}


class VisualLayoutTest(unittest.TestCase):
    def test_right_anchored_sl_status_accepts_no_departures_without_moving_the_header(self):
        scene={'page':'page_glass_v2','dynamic_text':{'screen/6/2':{
            'values':['3 min att gå','Inga avgångar'],'max_width':120,'anchor':'right'}}}
        ui={'page':'page_glass_v2','widgets':[{'path':'screen/6/2','type':'label','x':406,'y':287,
            'width':80,'height':17,'text':'3 min att gå','clickable':False}]}
        expected=visual.contract(ui,scene)
        ui['widgets'][0].update(text='Inga avgångar',x=390,width=96)
        self.assertEqual(expected,visual.contract(ui,scene))
        ui['widgets'][0]['x']+=1
        self.assertTrue(visual.layout_diff(expected,visual.contract(ui,scene)))
        ui['widgets'][0]['text']='Unknown'
        with self.assertRaises(ValueError):visual.contract(ui,scene)

    def test_settings_compare_opaque_interior_and_ignore_only_live_outer_edge(self):
        policy = json.loads(visual.POLICY_PATH.read_text())
        scene = policy['scenes']['settings_dashboard']
        mask = visual.pixel_mask(policy['size'], scene['pixels'])
        self.assertEqual(mask.getpixel((806, 44)), 0)   # just above the sheet (y 44 since Ljud ut)
        self.assertEqual(mask.getpixel((500, 55)), 255)
        original = Image.new('RGB', (1024, 600), (16, 24, 32))
        edge = original.copy(); edge.putpixel((806, 44), (255, 255, 255))
        self.assertTrue(visual.pixel_diff(original, edge, mask, 8, 8)[0]['ok'])
        interior = original.copy()
        ImageDraw.Draw(interior).rectangle((500, 55, 504, 59), fill='white')
        self.assertFalse(visual.pixel_diff(original, interior, mask, 8, 8)[0]['ok'])

    def test_tavla_plaque_follows_the_days_title_width_but_keeps_its_place(self):
        # 2026-10-30: the gate failed after a new NASA picture, "screen/2 width 231->292", on an unchanged layout.
        policy = json.loads(visual.POLICY_PATH.read_text())
        scene = policy['scenes']['tavla']
        plaque = {'path': 'screen/2', 'type': 'object', 'x': 28, 'y': 509, 'width': 231, 'height': 63, 'clickable': True}
        ui = {'page': 'page_tavla', 'widgets': [
            {'path': 'screen', 'type': 'object', 'x': 0, 'y': 0, 'width': 1024, 'height': 600, 'clickable': False},
            {'path': 'screen/0', 'type': 'image', 'x': 0, 'y': 0, 'width': 1024, 'height': 600, 'clickable': False},
            plaque]}
        expected = visual.contract(ui, scene)
        plaque['width'] = 292
        self.assertEqual(expected, visual.contract(ui, scene))
        for field, value in [('x', 40), ('y', 480), ('height', 90), ('clickable', False)]:
            other = copy.deepcopy(ui); other['widgets'][2][field] = value
            self.assertTrue(visual.layout_diff(expected, visual.contract(other, scene)))
        other = copy.deepcopy(ui); other['widgets'][2]['width'] = 1000
        with self.assertRaises(ValueError): visual.contract(other, scene)
        self.assertEqual(policy['scenes']['missing_image']['dynamic_width'], scene['dynamic_width'])

    def test_calendar_heading_accepts_today_and_tomorrow_but_keeps_position_and_height(self):
        scene = {'page': 'page_glass_v2', 'dynamic_text': {'screen/5/0':
                 {'values': ['IDAG', 'I MORGON'], 'max_width': 198}}}
        ui = {'page': 'page_glass_v2', 'widgets': [{'path': 'screen/5/0', 'type': 'label',
              'x': 782, 'y': 40, 'width': 36, 'height': 16, 'text': 'IDAG', 'clickable': False}]}
        expected = visual.contract(ui, scene)
        ui['widgets'][0].update(text='I MORGON', width=80)
        self.assertEqual(expected, visual.contract(ui, scene))
        for field, value in [('x', 783), ('y', 41), ('height', 17), ('clickable', True)]:
            other = copy.deepcopy(ui); other['widgets'][0][field] = value
            self.assertTrue(visual.layout_diff(expected, visual.contract(other, scene)))
        for field, value in [('text', 'UNKNOWN'), ('width', 199), ('type', 'button'), ('x', 1000)]:
            other = copy.deepcopy(ui); other['widgets'][0][field] = value
            with self.assertRaises(ValueError): visual.contract(other, scene)
        ui['widgets'] = []
        with self.assertRaises(ValueError): visual.contract(ui, scene)

    def setUp(self):
        self.policy = json.loads(visual.POLICY_PATH.read_text())
        self.scene = self.policy['scenes']['settings_dashboard']

    def test_live_value_and_label_width_changes_do_not_change_contract(self):
        before = settings_ui()
        after = copy.deepcopy(before)
        after['widgets'][3].update(text='100 %', x=755, width=50)
        after['widgets'][4]['value'] = 100
        self.assertEqual(visual.contract(before, self.scene), visual.contract(after, self.scene))

    def test_moved_clipped_missing_controls_or_changed_slider_range_fail(self):
        before = visual.contract(settings_ui(), self.scene)
        for field, value in [('x', 240), ('width', 400), ('max', 90), ('clickable', False)]:
            with self.subTest(field=field):
                after = settings_ui()
                after['widgets'][-1][field] = value
                self.assertTrue(visual.layout_diff(before, visual.contract(after, self.scene)))
        after = settings_ui()
        after['widgets'].pop(2)
        self.assertTrue(visual.layout_diff(before, visual.contract(after, self.scene)))

    def test_missing_static_anchor_wrong_page_and_truncated_tree_fail_closed(self):
        scene = {'page': 'page_glass_v2', 'labels': ['Skärmljus']}
        for ui in ({'page': 'page_glass_v2', 'widgets': []},
                   {**settings_ui(), 'page': 'page_orb'}, {**settings_ui(), 'truncated': True}):
            with self.assertRaises(ValueError): visual.contract(ui, scene)
        with self.assertRaises(ValueError): visual.contract(settings_ui(), {'page': 'page_glass_v2'})

    def test_geometry_tolerance_never_masks_missing_widgets_or_text_changes(self):
        before = {'x': {'x': 5, 'text': 'Klar'}}
        self.assertFalse(visual.layout_diff(before, {'x': {'x': 6, 'text': 'Klar'}}, 1))
        self.assertTrue(visual.layout_diff(before, {'x': {'x': 6, 'text': 'Bad'}}, 1))
        self.assertTrue(visual.layout_diff(before, {}, 1000))

    def test_contract_rejects_clipped_empty_or_noninteger_geometry_before_recording(self):
        for field, value in [('x', -1), ('x', 1000), ('y', 590), ('width', 0),
                             ('height', -2), ('x', True), ('height', 20.0)]:
            with self.subTest(field=field, value=value):
                ui = settings_ui()
                ui['widgets'][2][field] = value
                with self.assertRaises(ValueError): visual.contract(ui, self.scene)


class VisualArtifactsTest(unittest.TestCase):
    def make_capture(self, folder, reference=False):
        policy = {'size': [1024, 600], 'channel_tolerance': 8, 'max_changed_pixels': 8,
                  'geometry_tolerance': 0, 'scenes': {'settings': {
                      'page': 'page_glass_v2', 'subtree': 'top/1',
                      'pixels': {'include': [192, 51, 832, 501], 'radius': 28, 'exclude': []}}}}
        folder.mkdir()
        scene = folder / 'settings'
        scene.mkdir()
        visual.private_image(scene / 'screen.png', Image.new('RGB', (1024, 600), (16, 24, 32)))
        visual.private_json(scene / 'ui.json', settings_ui())
        manifest = {'format': 1, 'policy_sha256': visual.policy_digest(policy), 'scenes': {
            'settings': {'image': 'settings/screen.png', 'ui': 'settings/ui.json', 'contract': {}}}}
        visual.private_json(folder / ('reference.json' if reference else 'capture.json'), manifest)
        return policy

    def test_explicit_policy_rebind_preserves_source_and_every_reference_pixel(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'old';out=root/'new'
            policy=self.make_capture(source,reference=True)
            before={str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()}
            policy['version']=42;visual.new_folder(out)
            visual.rebind_reference(source,out,policy)
            self.assertEqual(before,{str(p.relative_to(source)):p.read_bytes() for p in source.rglob('*') if p.is_file()})
            self.assertEqual((source/'settings/screen.png').read_bytes(),(out/'settings/screen.png').read_bytes())
            self.assertEqual(visual.load_capture(out,policy,reference=True)['policy_sha256'],visual.policy_digest(policy))
            self.assertTrue(json.loads((out/'rebind.json').read_text())['unchanged_png_sha256'])

    def test_bad_candidate_produces_failure_diff_gallery_without_mutating_reference(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            reference, candidate, output = root / 'reference', root / 'candidate', root / 'result'
            policy = self.make_capture(reference, reference=True)
            self.make_capture(candidate)
            original = (reference / 'reference.json').read_bytes()
            with Image.open(candidate / 'settings/screen.png') as actual:
                ImageDraw.Draw(actual).rectangle((300, 100, 350, 120), fill='white')
                actual.save(candidate / 'settings/screen.png')
            visual.new_folder(output)
            result = visual.compare(reference, candidate, output, policy)
            self.assertFalse(result['ok'])
            self.assertEqual((reference / 'reference.json').read_bytes(), original)
            for name in ('report.json', 'report.html', 'settings-diff.png'):
                self.assertEqual((output / name).stat().st_mode & 0o777, 0o600)
            self.assertTrue((output / 'settings-diff.png').exists())

    def test_stale_incomplete_or_path_escaping_reference_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'reference'
            policy = self.make_capture(folder, reference=True)
            path = folder / 'reference.json'
            original = json.loads(path.read_text())
            for change in ({'policy_sha256': 'old'}, {'scenes': {}}, {'scenes': {
                    'settings': {'image': '../screen.png', 'ui': 'settings/ui.json'}}}):
                path.write_text(json.dumps({**original, **change}))
                with self.assertRaises(ValueError): visual.load_capture(folder, policy, reference=True)

    def test_baseline_directory_cannot_be_silently_reused(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'reference'
            visual.new_folder(folder)
            with self.assertRaises(FileExistsError): visual.new_folder(folder)

    def test_failed_capture_restores_idle_page_and_never_records_reference(self):
        class Client:
            page = 'page_hemmet'
            calls = []
            def status(self): return {'page': self.page}
            def ui(self): return {'widgets': []}
            def tap_text(self, text):
                self.calls.append(text)
                self.page = 'page_glass_v2' if text == 'Gå hemifrån' else 'page_hemmet'
            def inspect(self, *args, **kwargs): raise OSError('capture failed')
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            client = Client()
            policy = {'scenes': {'dashboard': {'page': 'page_glass_v2'}}}
            with self.assertRaises(OSError): visual.capture(client, folder, policy)
            self.assertEqual(client.page, 'page_hemmet')
            self.assertFalse((folder / 'reference.json').exists())
            self.assertEqual(client.calls, ['Gå hemifrån', 'Hemmet'])

    def test_post_flash_runs_visual_gate_and_propagates_failure_without_reflashing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for folder in ('tools', '.venv/bin', 'styles/glass-v2', '.panel-visual/reference-jarvis-line-v7-hemmet'):
                (root / folder).mkdir(parents=True)
            shutil.copy(ROOT / 'tools/flash_panel.sh', root / 'tools/flash_panel.sh')
            scripts = {
                '.venv/bin/python': '#!/bin/sh\ncase "$1" in *panel_lock.py)\n'
                                    'shift\nif [ "$1" = verify ]; then exit 0; fi\n'
                                    'while [ "$1" != -- ]; do shift; done\nshift\nexec "$@"\n'
                                    ';; esac\necho checks >> "$EVENTS"\n',
                '.venv/bin/esphome': '#!/bin/sh\necho "$1" >> "$EVENTS"\n'
                                    'echo "${HALLPANEL_BUILD_ID:-none}" > "$EVENTS.build"\n'
                                    'if [ "$1" = upload ]; then exit "${UPLOAD_CODE:-0}"; fi\n',
                'tools/panel': '#!/bin/sh\necho "$*" >> "$EVENTS"\n'
                               'if [ "$1" = preflight ]; then\n'
                               'if [ "${UNPLUG_AFTER_COMPILE:-0}" = 1 ] && grep -q \'^compile$\' "$EVENTS"; then exit 22; fi\n'
                               'exit "${PREFLIGHT_CODE:-0}"; fi\n'
                               'if [ "$1 $2" = "gates --record-build" ]; then echo 0123456789abcdef0123456789abcdef; exit 0; fi\n'
                               'if [ "$1 $2" = "gates --resolve-url" ]; then echo http://192.0.2.7:8080; exit 0; fi\n'
                               'if [ "$1 $2" = "gates --night-flag" ]; then echo "${NIGHT_FLAG:-}"; exit 0; fi\n'
                               'if [ "$1" = responsiveness ]; then exit "${RESPONSE_CODE:-0}"; fi\n'
                               'if [ "$1" = throughput ]; then exit "${CAPACITY_CODE:-0}"; fi\n'
                               'if [ "$1 $2" = "crash capture" ]; then exit 0; fi\n'
                               'if [ "$1 $2" = "crash scan" ]; then exit "${CRASH_CODE:-0}"; fi\n'
                               'exit "${VISUAL_CODE:-19}"\n'}
            for name, source in scripts.items():
                path = root / name
                path.write_text(source)
                path.chmod(0o755)
            ref = root / '.panel-visual/reference-jarvis-line-v7-hemmet/reference.json'
            ref.write_text('{}')
            events = root / 'events'
            # The caller's CRASH_SERIAL (a panel on USB) must not leak into the fake install (2026-10-28).
            env = {**{k: v for k, v in os.environ.items() if k != 'CRASH_SERIAL'}, 'EVENTS': str(events),
                   'CRASH_SETTLE_S': '0'}
            command = ['sh', str(root / 'tools/flash_panel.sh'), 'fake-device']
            target_check = 'preflight --device fake-device'
            preflight = 'responsiveness --check ' + str(root / '.panel-ci/baselines/responsiveness.json')
            capacity_preflight = 'throughput --check ' + str(root / '.panel-ci/baselines/throughput.json') + ' --require-target'
            # Build id and URL are fixed before the costly checks; the gates use the resolved address.
            target_check = [target_check, 'gates --record-build --device fake-device',
                            'gates --resolve-url --url http://hallpanel.local:8080']
            gates = 'gates --url http://192.0.2.7:8080 --wait-ready 90'
            flash = (ROOT / 'tools/flash_panel.sh').read_text()
            self.assertIn('"$ROOT/tools/panel" crash capture --device "$DEVICE" --out "$CRASH_LOG" & CAPTURE=$!', flash)
            def scan(events_text):
                # The capture runs in the background: its line can land anywhere, or not at all when the script
                # stops it before the fake has written it (seen under parallel load, 2026-10-28). It never starts
                # twice; that it is launched is checked on the script itself below.
                lines = events_text.splitlines()
                self.assertLessEqual(sum(line.startswith('crash capture --device fake-device --out ') for line in lines), 1)
                return [('crash scan' if line.startswith('crash scan ') else line) for line in lines
                        if not line.startswith('crash capture ')]
            night = 'gates --night-flag --url http://192.0.2.7:8080'
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 19)
            self.assertEqual(scan(events.read_text()), [*target_check, 'checks', preflight, capacity_preflight, 'compile',
                                                        'preflight --device fake-device', night, 'upload', gates, 'crash scan'])
            self.assertEqual(result.stdout.splitlines()[-1], 'FLASH FAILED at gates (exit 19)')
            self.assertEqual((root / 'events.build').read_text().strip(), '0123456789abcdef0123456789abcdef')
            events.unlink()
            result = subprocess.run(command, env={**env, 'UPLOAD_CODE': '17'}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 17)
            self.assertEqual(events.read_text().splitlines(), [*target_check, 'checks', preflight, capacity_preflight, 'compile',
                                                              'preflight --device fake-device', night, 'upload'])
            self.assertEqual(result.stdout.splitlines()[-1], 'FLASH FAILED at upload (exit 17)')
            events.unlink()
            result = subprocess.run(command, env={**env, 'VISUAL_CODE': '0'}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(scan(events.read_text()), [*target_check, 'checks', preflight, capacity_preflight, 'compile',
                                                        'preflight --device fake-device', night, 'upload', gates, 'crash scan'])
            self.assertEqual(result.stdout.splitlines()[-1], 'FLASH OK: installed on fake-device, all gates passed')
            events.unlink()
            # Gates that passed on a rolled-back or restarted panel do not make a crashed candidate an install.
            result = subprocess.run(command, env={**env, 'VISUAL_CODE': '0', 'CRASH_CODE': '1'}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout.splitlines()[-1], 'FLASH FAILED at crash-check (exit 1)')
            events.unlink()
            result = subprocess.run(command, env={**env, 'VISUAL_CODE': '0', 'NIGHT_FLAG': '--restore-night'},
                                    capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(scan(events.read_text())[-2], gates + ' --restore-night')
            events.unlink()
            ref.unlink()
            result = subprocess.run(command, env=env, capture_output=True, text=True)
            self.assertEqual(result.returncode, 1)
            self.assertIn('visual reference missing', result.stderr)
            self.assertEqual(events.read_text().splitlines(), [*target_check, 'checks', preflight, capacity_preflight])
            events.unlink()
            result = subprocess.run(command, env={**env, 'RESPONSE_CODE': '18'}, capture_output=True, text=True)
            self.assertEqual(result.returncode, 18)
            self.assertEqual(events.read_text().splitlines(), [*target_check, 'checks', preflight])
            self.assertEqual(result.stdout.splitlines()[-1], 'FLASH FAILED at gate-baselines (exit 18)')

            events.unlink()
            result = subprocess.run(command, env={**env, 'PREFLIGHT_CODE': '21'}, capture_output=True, text=True)
            self.assertEqual(result.returncode,21)
            self.assertEqual(events.read_text().splitlines(),[target_check[0]])
            self.assertEqual(result.stdout.splitlines()[-1], 'FLASH FAILED at preflight (exit 21)')
            events.unlink();ref.write_text('{}')
            result = subprocess.run(command, env={**env, 'UNPLUG_AFTER_COMPILE': '1'}, capture_output=True, text=True)
            self.assertEqual(result.returncode,22)
            self.assertEqual(events.read_text().splitlines(),[*target_check,'checks',preflight,capacity_preflight,'compile',
                                                             'preflight --device fake-device'])
            self.assertEqual(result.stdout.splitlines()[-1], 'FLASH FAILED at preflight-after-compile (exit 22)')

    def test_busy_panel_is_rejected_before_navigation(self):
        class Client:
            def status(self): return {'page': 'page_orb', 'uptime_ms': 50000}
            def ui(self): return {'widgets': []}
        with self.assertRaises(TimeoutError): visual.wait_ready(Client(), 0)
        with tempfile.TemporaryDirectory() as tmp, self.assertRaises(ValueError):
            visual.capture(Client(), Path(tmp), {'scenes': {}})

    def test_unstable_pixel_capture_is_rejected_before_reference_creation(self):
        class Client:
            def inspect(self, folder, lossless=False):
                folder.mkdir()
                visual.private_json(folder / 'ui.json', settings_ui())
                visual.private_image(folder / 'screen.png', Image.new('RGB', (1024, 600), 'black'))
                return {'ui_path': str(folder / 'ui.json'), 'screenshot': {'path': str(folder / 'screen.png')}}
            def screenshot(self, path, lossless=False):
                visual.private_image(path, Image.new('RGB', (1024, 600), 'white'))
        policy = {'size': [1024, 600], 'channel_tolerance': 8, 'max_changed_pixels': 8,
                  'settle_ms': 0, 'stability_ms': 0}
        scene = {'page': 'page_glass_v2', 'subtree': 'top/1',
                 'pixels': {'include': [192, 51, 832, 501], 'radius': 28, 'exclude': []}}
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'scene'
            with self.assertRaisesRegex(ValueError, 'Unstable screenshot'):
                visual.stable_capture(Client(), folder, policy, scene)
            self.assertFalse((Path(tmp) / 'reference.json').exists())


if __name__ == '__main__':
    unittest.main()


class FailedScenesTest(unittest.TestCase):
    def test_a_failed_check_names_its_scenes_layout_and_pixels(self):
        import panel_visual
        report = {'ok': False, 'scenes': {
            'dashboard': {'ok': True, 'layout_changes': []},
            'tavla': {'ok': False, 'layout_changes': [{'path': 'screen/2', 'field': 'x', 'expected': 0, 'actual': 28}],
                      'pixels': {'ok': False, 'changed_pixels': 309, 'bounds': [929, 41, 980, 53]}}}}
        self.assertEqual(panel_visual.failed_scenes(report), {'tavla': {
            'layout_changes': ['screen/2 x 0->28'], 'changed_pixels': 309, 'bounds': [929, 41, 980, 53]}})


class CalendarHeadingPolicyTest(unittest.TestCase):
    def test_every_heading_the_server_emits_is_allowed_by_the_policy(self):
        # 2026-11-02: the server gained "KOMMANDE" but the policy still allowed only IDAG and I MORGON, so the
        # visual gate stopped after the flash ("Invalid dynamic label screen/5/0"). Catch it before the flash.
        sys.path.insert(0, str(ROOT / 'server' / 'agents'))
        import home_status
        from datetime import datetime
        tz = home_status.TZ
        now = datetime(2026, 10, 21, 19, 50, tzinfo=tz)
        today = [(datetime(2026, 10, 21, 20, 30, tzinfo=tz), 'Jobb', 'A', 'a')]
        tomorrow = [(datetime(2026, 10, 22, 9, 0, tzinfo=tz), 'Jobb', 'B', 'b')]
        heads = {home_status.calendar_panel(events, now, later=later)['cal_head']
                 for events, later in ((today, None), (tomorrow, None), ([], None), ([], []))}
        policy = json.loads((ROOT / 'tests' / 'visual' / 'policy.json').read_text())
        allowed = set(policy['scenes']['dashboard']['dynamic_text']['screen/5/0']['values'])
        self.assertEqual(heads - allowed, set())


class AnchorsNeverHideTest(unittest.TestCase):
    def test_policy_screen_anchors_point_at_widgets_that_never_hide(self):
        # 2026-11-02: Hemmet's house line went in before the cards, so the policy's screen/4..7 pointed at it and the
        # check failed with "Missing widget screen/4" whenever the line hid (a hidden widget leaves the tree).
        sys.path.insert(0, str(ROOT / 'styles' / 'glass-v2'))
        import gen
        pages = {'page_glass_v2': gen.page['widgets'], 'page_hemmet': gen.hemmet_page()['widgets'],
                 'page_natt': gen.natt_page()['widgets']}
        policy = json.loads((ROOT / 'tests' / 'visual' / 'policy.json').read_text())
        checked = 0
        for name, scene in policy['scenes'].items():
            widgets = pages.get(scene['page'])
            if widgets is None:
                continue
            for path in scene.get('paths', []):
                parts = path.split('/')
                if parts[0] != 'screen' or len(parts) != 2:
                    continue
                obj = next(iter(widgets[int(parts[1])].values()))
                with self.subTest(scene=name, path=path):
                    self.assertFalse(obj.get('hidden'), obj.get('id'))
                    # 2026-11-03: n_wake moved Natt's full-screen tap to screen/5 and the policy kept screen/4, now
                    # the reminder line; with no reminder it was empty and the gate failed after a good flash.
                    kind = next(iter(widgets[int(parts[1])]))
                    self.assertTrue(kind != 'label' or path in scene.get('dynamic_text', {}),
                                    f'{path} is live text ({obj.get("id")}), not a stable anchor')
                    checked += 1
        self.assertGreater(checked, 4)
