"""Generated board contract and the atlas's frame boundaries, without a board or network."""
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "orbs"))
import atlas


class PanelPerformanceTest(unittest.TestCase):
    def test_board_has_three_boot_downloads_no_page_fades_and_five_ms_touch(self):
        result = subprocess.run([sys.executable, str(ROOT / "board.py"), "--voice", "jarvis_orb.py", "--remote",
                                 "http://example.invalid/assets"], cwd=ROOT / "styles/glass-v2",
                                capture_output=True, text=True, check=True)
        cfg = json.loads(result.stdout)
        self.assertEqual(cfg["display"][0]["frame_buffers"], 2)
        self.assertEqual(cfg["lvgl"]["buffer_size"], "50%")
        self.assertIn("direct_display::install(id(panel), true)", result.stdout)
        # LVGL 9.4 dereferences transition->props on press even if a local style stores nullptr.
        # This exact call crashed the real music pause button after all passive release gates had passed.
        self.assertNotRegex(result.stdout,r'lv_obj_set_style_transition\([^;]*\bnullptr\b')
        self.assertEqual(cfg["touchscreen"][0]["update_interval"], "5ms")
        self.assertEqual(cfg['wifi']['power_save_mode'], 'NONE')
        self.assertIn('frame_pacing_install(); bench_install();', result.stdout)
        # Each page change logs its render time per draw task type, outside the refresh (bench/page_draw.h).
        self.assertTrue(any(inc.endswith('bench/page_draw.h') for inc in cfg['esphome']['includes']))
        self.assertIn('page_draw::install(', result.stdout)
        self.assertIn('if (active == id(page_hemmet)->obj) return \\"page_hemmet\\";', result.stdout)
        self.assertIn('page_draw::report();', result.stdout)
        # Whole labels as ready masks, proved bit-identical by tools/check_label_cache.py.
        self.assertTrue(any(i.endswith('styles/glass-v2/label_cache.h') for i in cfg['esphome']['includes']))
        self.assertIn('label_cache::install();', result.stdout)
        # Tabs change state at once: a running press transition made every tab change wait for its own frame.
        tabs = next(t['lambda'] for b in cfg['esphome']['on_boot'] for t in b['then']
                    if 'instant_button(tab)' in t.get('lambda', ''))
        for tab in ['tab_0', 'tab_1', 'tab_2', 'tab_3', 'tab_4', 'tab_cog', 'bar_mic']:
            self.assertIn(f'id({tab})', tabs)
        # Tabs act on release, so each lights up on press (slow-motion video, 2026-11-02: nothing moved for 0.4-0.6 s).
        def buttons(node):
            if isinstance(node, dict):
                if isinstance(node.get('button'), dict):
                    yield node['button']
                for value in node.values():
                    yield from buttons(value)
            elif isinstance(node, list):
                for value in node:
                    yield from buttons(value)
        bar = {b['id']: b for b in buttons(cfg['lvgl']['top_layer']) if b.get('id', '').startswith('tab_')}
        self.assertEqual(sorted(bar), ['tab_0', 'tab_1', 'tab_2', 'tab_3', 'tab_4', 'tab_cog'])
        for key, button in bar.items():
            self.assertEqual(button.get('pressed', {}).get('bg_opa'), '34%', key)
        self.assertEqual(sum(i.endswith('music_buttons.h') for i in cfg['esphome']['includes']), 1)
        # Every font's glyphs unpack once (styles/glass-v2/glyph_cache.h), installed before LVGL draws.
        self.assertTrue(any(inc.endswith('styles/glass-v2/glyph_cache.h') for inc in cfg['esphome']['includes']))
        install = next(b for b in cfg['esphome']['on_boot'] if 'glyph_cache::install' in json.dumps(b))
        self.assertGreater(install['priority'], 400)
        for font in cfg['font']:
            self.assertIn(f"glyph_cache::install(id({font['id']})->get_lv_font());", install['then'][0]['lambda'])
        sdk = cfg['esp32']['framework']['sdkconfig_options']
        self.assertEqual(sdk['CONFIG_COMPILER_OPTIMIZATION_PERF'], 'y')
        self.assertEqual(sdk['CONFIG_COMPILER_OPTIMIZATION_SIZE'], 'n')
        # The choice member that drops DHCP's ~1 s ARP check; the `DOES_ARP_CHECK: n` spelling is ignored.
        self.assertEqual(sdk['CONFIG_LWIP_DHCP_DOES_NOT_CHECK_OFFERED_IP'], 'y')
        self.assertNotIn('CONFIG_LWIP_DHCP_DOES_ARP_CHECK', sdk)
        # No 32 MB PSRAM write/read-back before app_main on every boot.
        self.assertEqual(sdk['CONFIG_SPIRAM_MEMTEST'], 'n')
        # The C6 must be reset at every boot: without it the hosted link never came up (OTA rolled back).
        self.assertNotIn('CONFIG_ESP_HOSTED_SLAVE_RESET_ONLY_IF_NECESSARY', sdk)
        # Online images decode through the local runtime_image with direct RGB565 writes (tests/test_qoi_rgb565.py).
        local = [c for e in cfg['external_components'] for c in e.get('components', [])]
        self.assertIn('runtime_image', local)
        self.assertEqual(cfg['panel_debug'], {'id': 'physical_ui'})
        self.assertIn('direct_display::copy_presented', result.stdout)
        self.assertNotIn('panel_debug_token', result.stdout)
        images = [image for image in cfg["image"] if image.get("platform") == "online_image"]
        self.assertEqual([image["id"] for image in images], ["bg", "art", "voice_bg", "earth"])
        # Tavla's Earth picture (tavla_earth.py) is never in the boot chain: only its two guarded scripts fetch
        # it, the daily one after the backgrounds are in.
        self.assertNotIn('"earth"', json.dumps(cfg['esphome'].get('on_boot', [])) + json.dumps(images[:3]))
        fetchers = [sc['id'] for sc in cfg['script'] if '"component.update": "earth"' in json.dumps(sc)]
        self.assertEqual(fetchers, ['earth_fetch', 'earth_now'])
        fetch = json.dumps(next(sc for sc in cfg['script'] if sc['id'] == 'earth_fetch'))
        self.assertLess(fetch.index('id(assets_loaded)'), fetch.index('"component.update": "earth"'))
        self.assertIn('!id(earth_busy)', fetch)
        # The extra cover is on demand, outside the boot/background chains.
        boot_chain = json.dumps(cfg['esphome'].get('on_boot', [])) + json.dumps(images[:3])
        self.assertNotIn('np_art', boot_chain)
        artwork = next(i for i in cfg["image"] if i["id"] == "np_art")
        self.assertEqual(artwork['platform'], 'async_artwork')
        # 288 px for the Spelas nu record; the row gets a 144 px copy (cover_small.h, 2026-10-28).
        self.assertNotIn('buffer_size', artwork)
        # Every copy of the Spelas nu row shows the new cover; Hemmet's stayed empty when only one was updated
        # (2026-10-28).
        finished = json.dumps(artwork['on_download_finished'])
        for image in ('np_small_art_frame_image', 'np_small_art_frame_home_image'):
            self.assertIn(f'panel_cover::show(id({image}))', finished)
        self.assertIn('panel_vinyl::page.source(', finished)     # the Spelas nu record (vinyl.h); the sheet is gone
        self.assertNotIn('panel_vinyl::sheet', finished)
        # The cover download is raised above core 0's idle task only while Spelas nu shows the record.
        setup = next(sc for sc in cfg['script'] if sc['id'] == 'vinyl_setup')
        self.assertIn('id(np_art).set_foreground(on)', json.dumps(setup))
        self.assertIn('"artwork",6144,this,0,&task_,0,MALLOC_CAP_SPIRAM|MALLOC_CAP_8BIT)', (ROOT / 'components/async_artwork/async_artwork.cpp').read_text())
        # One guarded fetch script: run when HA's cover key or the music arrives, the interval only retries.
        update = next(item for item in cfg['script'] if '"component.update": "np_art"' in json.dumps(item))
        self.assertEqual(update['id'], 'music_art_fetch')
        self.assertIn('panel_music::state.active() && panel_music::artwork.begin()', json.dumps(update))
        self.assertFalse(any('"component.update": "np_art"' in json.dumps(item) for item in cfg['interval']))
        self.assertTrue(any('music_art_fetch' in json.dumps(item) for item in cfg['interval']))
        key = next(t for t in cfg['text_sensor'] if t.get('id') == 'music_artwork')
        self.assertIn('music_art_fetch', json.dumps(key['on_value']))
        self.assertIn("orb_atlas_v8.lz4", result.stdout)
        self.assertIn("packed_orb::direct();", result.stdout)
        # An announcement keeps the orb speaking until it has played (no assistant run to wait for).
        done = next(sc for sc in cfg["script"] if sc["id"] == "voice_done")
        self.assertIn("MEDIA_PLAYER_STATE_ANNOUNCING", json.dumps(done["then"][:2]))
        self.assertTrue(any(str(i).endswith("orb_director.h") for i in cfg["esphome"]["includes"]))
        self.assertIn('-DLV_USE_LZ4=1', cfg['esphome']['build_flags'])
        self.assertIn('-DLV_USE_LZ4_INTERNAL=1', cfg['esphome']['build_flags'])
        self.assertIn('-DLV_BIN_DECODER_RAM_LOAD=1', cfg['esphome']['build_flags'])
        self.assertFalse(any(image["id"] == "bg_orb" for image in cfg["image"]))
        orb = next(page for page in cfg["lvgl"]["pages"] if page["id"] == "page_orb")
        self.assertEqual(orb["widgets"][0]["image"]["src"], "voice_bg")
        orb_background = orb["widgets"][0]["image"]["id"]
        art = next(image for image in images if image["id"] == "art")
        self.assertIn('voice_bg', json.dumps(art["on_download_finished"]))
        voice = next(image for image in images if image["id"] == "voice_bg")
        self.assertIn(orb_background, json.dumps(voice["on_download_finished"]))
        listening = next(widget['label'] for widget in orb['widgets']
                         if 'label' in widget and widget['label'].get('id') == 'you_lbl')
        self.assertEqual(listening['text_opa'], 'COVER')
        self.assertEqual(listening['text_font'], 'i600_22')
        self.assertNotIn('LV_OPA_60', json.dumps(cfg['voice_assistant']['on_listening']))
        stt = cfg['voice_assistant']['on_stt_end']
        phase = next(i for i, action in enumerate(stt) if 'sync_loop_phase' in action.get('lambda', ''))
        shown = next(i for i, action in enumerate(stt) if action.get('lvgl.widget.show') == 'orb_think')
        self.assertLess(phase, shown)  # synchronize the old visible phase before the new sphere is shown
        boot = next(script for script in cfg['script'] if script['id'] == 'boot_done')
        ready = boot['then'][0]['if']['then'][-1]['if']['then']
        self.assertNotIn('assets_loaded', json.dumps(ready))
        self.assertNotIn('wait_until', json.dumps(ready))
        self.assertIn('bg_valid', json.dumps(ready[0]['if']['condition']))
        self.assertIn('voice_assistant.is_running', json.dumps(ready[0]['if']['condition']))
        self.assertIn('MEDIA_PLAYER_STATE_ANNOUNCING', json.dumps(ready[0]['if']['condition']))
        self.assertIn('ready_sound', json.dumps(ready[0]['if']['then']))
        self.assertIn('boot_sound_ms', json.dumps(ready[0]['if']['then']))
        self.assertIn('boot_was_software', json.dumps(boot))
        self.assertIn('no time from Home Assistant', json.dumps(boot))
        shown = boot['then'][0]['if']['then']
        self.assertNotIn('lv_refr_now', json.dumps(shown))
        render_wait=next(a['wait_until'] for a in shown if a.get('wait_until',{}).get('condition',{}).get('lambda')=='return id(boot_shown_ms) != 0;')
        self.assertEqual(render_wait['timeout'], '1s')
        self.assertIn('boot_shown_ms', json.dumps(ready[0]['if']['condition']))
        volume = next(item for item in cfg["globals"] if item["id"] == "day_volume")
        self.assertTrue(volume["restore_value"])
        self.assertEqual(volume["initial_value"], "0.2")
        def visit(node):
            if isinstance(node, dict):
                for key, value in node.items():
                    if key == "lvgl.page.show" and isinstance(value, dict):
                        self.assertEqual(value.get("animation"), "NONE")
                        self.assertNotIn("time", value)
                    visit(value)
            elif isinstance(node, list):
                for value in node:
                    visit(value)
        visit(cfg)

    def test_atlas_fits_decoder_and_preserves_one_shot_endpoints(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = atlas.build(Path(tmp) / "orb_atlas.png", delta=False)
            with Image.open(path) as image:
                self.assertEqual(image.size, (220, 220 * sum(atlas.COUNTS.values())))
                self.assertLessEqual(image.height, 32767)
                offset = 0
                for state, count in atlas.COUNTS.items():
                    with Image.open(ROOT / "orbs/glass-jarvis-opaque" / f"{state}_00.png") as original:
                        self.assertEqual(image.crop((0, offset, 220, offset + 220)).tobytes(),
                                         atlas.rgb565_precision(original.convert("RGB")).tobytes())
                    if state in ("morph", "settle"):
                        with Image.open(ROOT / "orbs/glass-jarvis-opaque" /
                                        f"{state}_{atlas.SOURCE_COUNTS[state] - 1:02d}.png") as original:
                            y = offset + (count - 1) * 220
                            self.assertEqual(image.crop((0, y, 220, y + 220)).tobytes(),
                                             atlas.rgb565_precision(original.convert("RGB")).tobytes())
                    offset += count * 220

    def test_delta_is_lossless_at_rgb565_precision(self):
        from array import array
        # Nontrivial changes and repeated frames exercise cross-frame reconstruction, including
        # transitions between animation states. This compares the exact bytes consumed by C++.
        original = Image.new("RGB", (220, 660))
        for y, colour in ((0, (12, 147, 253)), (220, (249, 38, 74)), (440, (249, 38, 74))):
            original.paste(colour, (0, y, 220, y + 220))
        def words(image):
            rgb = image.tobytes()
            return array("H", (((rgb[i] >> 3) << 11) | ((rgb[i + 1] >> 2) << 5) | (rgb[i + 2] >> 3)
                               for i in range(0, len(rgb), 3)))
        encoded = words(atlas.delta_encode(original))
        for i in range(220 * 220, len(encoded)):
            encoded[i] ^= encoded[i - 220 * 220]
        self.assertEqual(encoded, words(original))


if __name__ == "__main__":
    unittest.main()
