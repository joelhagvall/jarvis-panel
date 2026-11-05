"""Colour fidelity and calendar metadata regressions, without hardware or iCloud."""
from datetime import datetime
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

import numpy as np
from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'server'))
sys.path.insert(0, str(ROOT / 'server/agents'))
import calendar_palette as palette
import home_status
from panel_image import prepare
sys.path.insert(0, str(ROOT / 'tools'))
from panel_color import check, rgb565


def multistatus(name, value, status='HTTP/1.1 200 OK'):
    root = ET.Element('{DAV:}multistatus')
    response = ET.SubElement(root, '{DAV:}response')
    stat = ET.SubElement(response, '{DAV:}propstat')
    ET.SubElement(stat, '{DAV:}status').text = status
    prop = ET.SubElement(stat, '{DAV:}prop')
    node = ET.SubElement(prop, name)
    if isinstance(value, tuple):
        ET.SubElement(node, '{DAV:}href').text = value[0]
    else:
        node.text = value
    return root


class CalendarColourTest(unittest.TestCase):
    def test_caldav_discovery_reads_real_properties_and_ignores_404(self):
        calls = []
        def request(url, props, auth, depth=0):
            calls.append((url, depth))
            if len(calls) == 1:
                return multistatus('{DAV:}current-user-principal', ('/principal/',))
            if len(calls) == 2:
                return multistatus(f'{{{palette.CAL}}}calendar-home-set', ('https://p42-caldav.icloud.com/calendars/',))
            result = multistatus('{DAV:}displayname', 'Att göra')
            prop = result.find('.//{DAV:}prop')
            ET.SubElement(prop, f'{{{palette.APPLE}}}calendar-color').text = '#34C759FF'
            failed = multistatus(f'{{{palette.APPLE}}}calendar-color', '#FFFFFF', 'HTTP/1.1 404 Not Found')
            result.extend(failed)
            return result
        with tempfile.TemporaryDirectory() as tmp:
            credentials = Path(tmp) / 'credentials.json'
            credentials.write_text(json.dumps({'user': 'test', 'password': 'private'}))
            self.assertEqual(palette.fetch(credentials, request), {'Att göra': '#34C759'})
        self.assertEqual(calls[-1], ('https://p42-caldav.icloud.com/calendars/', 1))

    def test_colour_formats_and_trusted_hosts(self):
        for value, expected in [('#34c759ff', '#34C759'), ('#FFCC00', '#FFCC00'),
                                ('red', None), ('#12345ZFF', None), ('#12345600junk', None)]:
            self.assertEqual(palette.colour(value), expected)
        for url in ['http://caldav.icloud.com/', 'https://icloud.com.evil.test/',
                    'https://user:pass@caldav.icloud.com/', 'https://caldav.icloud.com:8443/']:
            self.assertFalse(palette.trusted(url))
        self.assertTrue(palette.trusted('https://p42-caldav.icloud.com/123/'))

    def test_failed_refresh_retains_last_palette_and_requests_do_not_wait(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / 'palette.json'
            cache.write_text('{"Jobb":"#FFCC00"}')
            instance = palette.Palette(Path(tmp) / 'missing', cache)
            entered, release = threading.Event(), threading.Event()
            def slow(*args):
                entered.set()
                release.wait(3)
                raise OSError('unavailable')
            with patch.object(palette, 'fetch', slow):
                self.assertEqual(instance.get(), {'Jobb': '#FFCC00'})
                self.assertTrue(entered.wait(1))
                self.assertEqual(instance.get(), {'Jobb': '#FFCC00'})
                release.set()
                # A direct refresh also verifies retention without a timing assertion.
                instance.refresh()
            self.assertEqual(json.loads(cache.read_text()), {'Jobb': '#FFCC00'})

    def test_protocol_carries_source_colour_without_changing_title_or_time(self):
        now = datetime(2026, 10, 24, 13, tzinfo=home_status.TZ)
        events = [(now, 'Att göra', 'Long title | with separator', 'uid')]
        data = home_status.calendar_panel(events, now, {'Att göra': '#34C759'})
        self.assertEqual(data['cal0'], 'Long title   with separator|13:00|Att göra|#34C759')
        self.assertTrue(home_status.calendar_panel(events, now, {})['cal0'].endswith('|Att göra|'))

    def test_actual_firmware_colour_parser(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            source.write_text('''#include "calendar_color.h"
#include <cassert>
int main() {
  assert(calendar_color::parse("#34C759") == 0x34C759);
  assert(calendar_color::parse("#ffcc00") == 0xFFCC00);
  assert(calendar_color::parse("#000000") == 0);
  for (const auto *s : {"", "#34C75Z", "#34C759FF", "34C759", "#-12345"})
    assert(calendar_color::parse(s) == 0x8E8E93);
}''')
            subprocess.run(['c++', '-std=c++17', '-I', str(ROOT / 'styles/glass-v2'), str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True)

    def test_generated_firmware_uses_palette_field(self):
        spec = importlib.util.spec_from_file_location('colour_gen', ROOT / 'styles/glass-v2/gen.py')
        gen = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(gen)
        code = gen.calendar_slot(0)
        self.assertIn('calendar_color::parse(p[3])', code)
        self.assertNotIn('p[2] ==', code)


class ImageColourTest(unittest.TestCase):
    def test_capture_checker_catches_wrong_calendar_colour_and_corrupt_background(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            source = Image.new('RGB', (32, 32), (25, 27, 31))
            source.save(folder / 'source.png')
            prepared = prepare(source)
            prepared.save(folder / 'prepared.png')
            screen = prepared.copy()
            from PIL import ImageDraw
            ImageDraw.Draw(screen).rectangle((20, 2, 23, 12), fill=tuple(rgb565([52, 199, 89])))
            screen.save(folder / 'screen.png')
            (folder / 'ui.json').write_text(json.dumps({'page':'page_glass_v2', 'widgets':[
                {'id':'screen/5/1', 'x':20, 'y':2, 'width':4, 'height':10}]}))
            payload = {'cal0':'Title|14:00|Att göra|#34C759'}
            args = (folder, folder/'source.png', folder/'prepared.png', (0, 16, 32, 32))
            self.assertTrue(check(*args, payload)['ok'])
            self.assertFalse(check(*args, {'cal0':'Title|14:00|Att göra|#FFCC00'})['ok'])
            screen.putpixel((0, 16), (255, 0, 0))
            screen.save(folder / 'screen.png')
            self.assertFalse(check(*args, payload)['ok'])

    def test_dark_gradient_preserves_block_average_and_reduces_contour_bias(self):
        ramp = np.repeat(np.arange(64, dtype=np.uint8), 8)
        pixels = np.broadcast_to(ramp[None, :, None], (64, len(ramp), 3)).copy()
        image = Image.fromarray(pixels)
        result = np.array(prepare(image))
        def block_mean(array):
            return array.reshape(8, 8, 64, 8, 3).mean(axis=(1, 3))
        plain = (pixels // [8, 4, 8]) * [8, 4, 8]
        self.assertLess(np.abs(block_mean(result) - block_mean(pixels)).max(), .07)
        self.assertGreater(np.abs(block_mean(plain) - block_mean(pixels)).mean(), 2)
        self.assertTrue(np.array_equal(np.array(image), pixels))

    def test_deterministic_idempotent_device_grid_and_bright_limits(self):
        pixels = np.random.default_rng(42).integers(0, 256, (21, 37, 3), dtype=np.uint8)
        image = Image.fromarray(pixels)
        result = prepare(image)
        self.assertEqual(result.tobytes(), prepare(image).tobytes())
        self.assertEqual(result.tobytes(), prepare(result).tobytes())
        indices = np.array(result).astype(np.uint16) >> [3, 2, 3]
        expanded = indices * [255, 255, 255] // [31, 63, 31]
        self.assertTrue(np.array_equal(expanded, np.array(result)))
        self.assertEqual(prepare(Image.new('RGB', (1, 1), 'white')).getpixel((0, 0)), (255, 255, 255))


if __name__ == '__main__':
    unittest.main()
