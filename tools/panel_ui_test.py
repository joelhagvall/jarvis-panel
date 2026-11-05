#!/usr/bin/env python3
"""Explicit, free smoke test of the physical screen. Navigates real tabs/settings; no model calls."""
import argparse
import asyncio
from datetime import datetime
import json
import os
from pathlib import Path
import secrets
import sys
import time
import urllib.error
import urllib.parse

from panel_remote import PanelClient, ROOT


async def _native_brightness(url, restore=None):
    """Read/restore the exact backlight float through its existing encrypted API, once."""
    from aioesphomeapi import APIClient, LightInfo, LightState
    from ruamel.yaml import YAML
    try:
        key = YAML(typ='safe').load((ROOT/'styles/glass-v2/secrets.yaml').read_text())['api_key']
    except Exception:
        raise RuntimeError('Cannot read private native API configuration') from None
    api = APIClient(urllib.parse.urlsplit(url).hostname, 6053, password=None, noise_psk=key,
                    client_info='hallpanel-test-restore')
    try:
        await asyncio.wait_for(api.connect(login=True, log_errors=False), 10)
        entities, _ = await asyncio.wait_for(api.list_entities_services(), 10)
        lights = [e for e in entities if isinstance(e, LightInfo) and e.name == 'Skärmljus']
        if len(lights) != 1:
            raise RuntimeError('Expected exactly one panel backlight')
        ready, result = asyncio.Event(), {}
        def state(value):
            if isinstance(value, LightState) and value.key == lights[0].key and value.state:
                if restore is None or abs(value.brightness-restore) < .00001:
                    result['brightness'] = value.brightness
                    ready.set()
        api.subscribe_states(state)
        if restore is not None:
            if not .05 <= restore <= 1:
                raise ValueError('Unsafe backlight restoration value')
            # A write is issued once. An ambiguous disconnect is never retried.
            api.light_command(lights[0].key, state=True, brightness=restore, transition_length=0)
        await asyncio.wait_for(ready.wait(), 10)
        value = result['brightness']
        if not .05 <= value <= 1:
            raise ValueError('Backlight is outside day-test bounds')
        return value
    except Exception as error:
        raise RuntimeError('Native brightness operation failed: '+type(error).__name__) from None
    finally:
        await api.disconnect()


def restore_brightness(client, brightness, before):
    current = client.status()
    if (current.get('boot_id') != before.get('boot_id') or current.get('physical_touch_active')
            or any(current.get(key) for key in ('voice_active','alarm_active','announcement_active'))
            or current.get('test_mode', 'idle') != 'idle'
            or (current.get('uptime_ms', 0)-current.get('physical_touch_idle_ms', 0)) > before.get('uptime_ms', 0)):
        raise RuntimeError('Panel preempted the test; do not overwrite its brightness')
    return asyncio.run(_native_brightness(client.url, brightness))


def expect_page(client, page):
    deadline = time.monotonic() + 2
    while time.monotonic() < deadline:
        if client.status()['page'] == page:
            return
        time.sleep(0.05)
    raise AssertionError(f'Expected physical page {page}')


def bar_page(client):
    """Reach a page with visible tabs through its actual swipe handler."""
    page = client.status()['page']
    if page == 'page_natt':
        # A failed navigation regression can accidentally enter Natt. Its real
        # full-screen tap exits night mode and restores backlight/volume.
        targets = [w for w in client.ui()['widgets'] if w['type'] == 'object'
                   and w.get('clickable') and (w['x'], w['y'], w['width'], w['height']) == (0, 0, 1024, 600)]
        if not targets:
            raise RuntimeError('Night-mode restoration control unavailable')
        client.tap_id(max(targets, key=lambda w: w['path'].count('/'))['path'])
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            page = client.status()['page']
            if page in ('page_glass_v2', 'page_hemmet'):
                return
            time.sleep(0.05)
        raise RuntimeError('Night-mode restoration failed')
    # Tavla and Spelas nu have no tab bar: swipe right until a page with one (Tavla -> Spelas nu -> Hemmet since
    # the Spelas nu tab, 2026-10-28).
    for _ in range(2):
        if page not in ('page_tavla', 'page_musik'):
            break
        client.swipe(220, 300, 820, 300, 350)
        page = client.status()['page']
    if page not in ('page_glass_v2', 'page_hemmet'):
        raise RuntimeError('Run from the dashboard, Hemmet, Spelas nu or Tavla while voice/night mode is idle')


def select_tab(client, page):
    """Recheck after leaving Tavla: the swipe itself may already reach Hemmet."""
    names = {'page_glass_v2':'Gå hemifrån', 'page_hemmet':'Hemmet', 'page_musik':'Spelas nu', 'page_tavla':'Tavla'}
    if page not in names:
        raise ValueError('Expected a normal tab page')
    if client.status()['page'] != page:
        bar_page(client)
        if client.status()['page'] != page:
            client.tap_text(names[page])
    expect_page(client, page)


def brightness_targets(slider):
    """Exercise a full step and the wide 50% stop; native API restores off-step values."""
    old, low, high = slider['value'], slider['min'], slider['max']
    target = old + 10 if old + 10 <= high else old - 10
    target = min(high, max(low, ((target + 2) // 5) * 5))
    return [(target, target), (48, 50), (52, 50)]


def run(client, folder, brightness=False):
    folder.mkdir(parents=True, exist_ok=False)
    folder.chmod(0o700)
    before = client.status()
    original = before['page']
    if original not in ('page_glass_v2', 'page_hemmet', 'page_musik', 'page_tavla'):
        raise RuntimeError('Run while voice/night mode is idle')
    report = {'started': datetime.now().astimezone().isoformat(), 'before': before, 'checks': [], 'captures': []}
    modal = False
    try:
        report['before_snapshot'] = client.inspect(folder / 'before')
        if before.get('protocol', 1) >= 2 and before.get('lease_required'):
            # Use a distinct raw lease identity, even when the parent inherited its
            # lease through a trusted subprocess. No UI/input request is queued.
            with PanelClient(client.url, token=client.token) as contender:
                try:
                    contender.request('/lease', {'action': 'acquire', 'id': secrets.token_hex(32), 'ttl_ms': 30000})
                except urllib.error.HTTPError as error:
                    assert error.code == 409, 'A concurrent device lease did not return 409'
                else:
                    raise AssertionError('A second agent acquired the already-held device lease')
            report['checks'].append('concurrent agent rejected by device lease')
        else:
            report['device_lease'] = 'unsupported legacy firmware'
        # Check actual unauthorized writes as well as reads. Both must fail before queueing work.
        with PanelClient(client.url, token='incorrect-test-token') as wrong:
            for path, form in (('/status', None), ('/tap', {'x': 676, 'y': 546, 'duration_ms': 60})):
                try:
                    wrong.request(path, form)
                except urllib.error.HTTPError as error:
                    assert error.code == 401, 'Unauthenticated endpoint did not reject with 401'
                else:
                    raise AssertionError('Unauthenticated endpoint accepted a request')
        report['checks'].append('unauthenticated read and input rejected')
        try:
            client.request('/tap', {'x': 1024, 'y': 0, 'duration_ms': 60})
        except urllib.error.HTTPError as error:
            assert error.code == 400, 'Invalid coordinate did not return 400'
        else:
            raise AssertionError('Invalid touch was accepted')
        report['checks'].append('out-of-range touch rejected')

        if client.status()['page'] == 'page_hemmet':
            select_tab(client, 'page_glass_v2')
        select_tab(client, 'page_hemmet')
        report['captures'].append(client.screenshot(folder / 'hemmet.jpg'))
        report['checks'].append('Hemmet tab and screenshot')

        widgets = client.ui()['widgets']
        night = next(w for w in widgets if w.get('text') == 'Natt')
        night_button = night['path'].rsplit('/', 1)[0]
        parent, index = night_button.rsplit('/', 1)
        gear_path = parent + '/' + str(int(index) + 1)
        gear = next(w for w in widgets if w['path'] == gear_path and w['type'] == 'button')
        assert gear['clickable'] and gear['width'] == 48 and gear['height'] == 48, 'Settings gear layout changed'
        client.tap(gear['x'] + gear['width'] // 2, gear['y'] + gear['height'] // 2)
        modal = True
        controls = client.ui()['widgets']
        assert any(w.get('text') == 'Inställningar' for w in controls), 'Settings did not open'
        report['captures'].append(client.screenshot(folder / 'settings.png', lossless=True))
        report['checks'].append('settings overlay included in exact framebuffer capture')

        if brightness:
            slider = next(w for w in controls if w['type'] == 'slider')
            old = slider['value']
            targets = brightness_targets(slider)
            exact_brightness = asyncio.run(_native_brightness(client.url))
            if int(exact_brightness*100+.5) != old:
                raise RuntimeError('Native backlight and slider disagree before test')
            report['brightness_original'] = {'percent': old, 'native': exact_brightness}
            x = lambda value: round(slider['x'] + (value - slider['min']) * (slider['width'] - 1) /
                                    (slider['max'] - slider['min']))
            y = slider['y'] + slider['height'] // 2
            value = lambda: next(w for w in client.ui()['widgets'] if w['path'] == slider['path'])['value']
            try:
                report['captures'].append(client.screenshot(folder / 'brightness-before.jpg'))
                for index, (raw, expected) in enumerate(targets):
                    client.swipe(x(value()), y, x(raw), y, 350)
                    assert value() == expected, 'Actual brightness drag missed the detent'
                    report['captures'].append(client.screenshot(folder / f'brightness-after-{index}.jpg'))
            finally:
                restore_brightness(client, exact_brightness, before)
                # Sync through the existing settings-open script; do not fake widget state.
                client.tap_text('Klar')
                client.tap_id(gear_path)
                assert value() == old, 'Original brightness was not restored'
                report['captures'].append(client.screenshot(folder / 'brightness-restored.jpg'))
            report['checks'].append('real brightness detents: 48 and 52 snap to 50; exact value restoration')

        client.tap_text('Klar')
        modal = False
        report['captures'].append(client.screenshot(folder / 'settings-closed.jpg'))
        client.tap_text('Tavla')
        expect_page(client, 'page_tavla')
        report['captures'].append(client.screenshot(folder / 'tavla.jpg'))
        client.swipe(220, 300, 820, 300, 350)
        expect_page(client, 'page_musik')
        report['captures'].append(client.screenshot(folder / 'swipe-after.jpg'))
        client.tap(700, 300)              # Spelas nu: a tap beside the controls goes back to the tab page
        expect_page(client, 'page_hemmet')
        report['checks'].append('Tavla tab, real right-swipe to Spelas nu, and its tap back')
        # Read the live bar geometry. One gesture must advance exactly one tab,
        # even when LVGL updates cur_page synchronously during the same script.
        widgets = client.ui()['widgets']
        labels = {label: next(w for w in widgets if w.get('text') == label)
                  for label in ('Gå hemifrån', 'Natt')}
        centers = {label: (w['x'] + w['width']//2, w['y'] + w['height']//2)
                   for label, w in labels.items()}
        left, right = centers['Gå hemifrån'], centers['Natt']
        client.swipe(*left, *right, 350)
        expect_page(client, 'page_glass_v2')
        report['captures'].append(client.screenshot(folder / 'swipe-dashboard-before.jpg'))
        client.swipe(*right, *left, 350)
        expect_page(client, 'page_hemmet')
        report['captures'].append(client.screenshot(folder / 'swipe-dashboard-to-home.jpg'))
        client.swipe(*right, *left, 350)
        expect_page(client, 'page_musik')
        report['captures'].append(client.screenshot(folder / 'swipe-home-to-music.jpg'))
        client.swipe(820, 300, 220, 300, 350)
        expect_page(client, 'page_tavla')
        report['captures'].append(client.screenshot(folder / 'swipe-music-to-frame.jpg'))
        client.swipe(220, 300, 820, 300, 350)
        expect_page(client, 'page_musik')
        client.swipe(220, 300, 820, 300, 350)
        expect_page(client, 'page_hemmet')
        report['checks'].append('one swipe advances one tab in both directions without skipping')
        # Repeated captures must release their transient PSRAM buffers after each response.
        for i in range(5):
            report['captures'].append(client.screenshot(folder / f'memory-{i}.jpg'))
        after = client.status()
        assert after['psram_free'] >= before['psram_free'] - 65536, 'Capture PSRAM did not return to its prior level'
        report['checks'].append('repeated capture buffers released')
        report['ok'] = True
    except Exception as error:
        report['ok'] = False
        report['error'] = str(error)
        raise
    finally:
        try:
            if modal: client.tap_text('Klar')
            select_tab(client, original)
            report['after'] = client.status()
            report['after_snapshot'] = client.inspect(folder / 'after')
        except Exception as error:
            report['ok'] = False
            report['restore_error'] = str(error)
            raise
        finally:
            path = folder / 'report.json'
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
                json.dump(report, output, ensure_ascii=False, indent=2)
    return report


def chat_scroll_check(client):
    """Drag the overflowing bubble both ways through the real pointer path."""
    expect_page(client, 'page_glass_v2')
    before = client.ui()
    label = next(w for w in before['widgets'] if w.get('text', '').startswith('First paragraph:'))
    path = '/'.join(label['path'].split('/')[:3])
    viewport = next(w for w in before['widgets'] if w['path'] == path)
    x = viewport['x'] + 70
    top, bottom = viewport['y'] + 16, viewport['y'] + viewport['height'] - 16
    client.swipe(x, bottom, x, top, 600, settle=0.4)
    expect_page(client, 'page_glass_v2')
    moved = next(w for w in client.ui()['widgets'] if w.get('text') == label['text'])
    assert moved['y'] < label['y'] - 20, 'Chat did not scroll toward the end'
    client.swipe(x, top, x, bottom, 600, settle=0.4)
    expect_page(client, 'page_glass_v2')
    returned = next(w for w in client.ui()['widgets'] if w.get('text') == label['text'])
    assert returned['y'] > moved['y'] + 20, 'Chat did not scroll toward earlier lines'
    return {'before_y': label['y'], 'after_up_y': moved['y'], 'after_down_y': returned['y']}


def run_test_mode(client, folder):
    """Exercise the bounded visual test callback; all network/voice faults are synthetic."""
    from panel_remote import private_json
    folder.mkdir(parents=True, mode=0o700, exist_ok=False)
    original = client.status()['page']
    if original not in ('page_glass_v2', 'page_hemmet', 'page_musik', 'page_tavla'):
        raise RuntimeError('Synthetic UI test requires an idle tab page')
    cases = {
        'chat': ('page_glass_v2', 'Of course. We can continue in English.'),
        'listening': ('page_orb', 'Jag lyssnar…'),
        'thinking': ('page_orb', 'Test av Hallpanel'),
        'speaking': ('page_orb', 'Det här är ett lokalt test utan ljud eller modell.'),
        'night': ('page_natt', None), 'ota': ('page_ota', 'Jarvis uppdaterar sig'),
        'wifi_disconnected': ('page_glass_v2', 'Nätet är nere – timers och larm fungerar'),
        'ha_disconnected': ('page_glass_v2', 'Ingen kontakt med Jarvis'),
        'api_timeout': ('page_orb', 'Det gick inte just nu.'),
        'missing_image': ('page_tavla', None),
    }
    report = {'ok': False, 'synthetic_ui': True, 'actual_audio': False,
              'actual_network_faults': False, 'before': client.status(), 'checks': []}
    try:
        report['before_snapshot'] = client.inspect(folder / 'before')
        for mode, (page, label) in cases.items():
            try:
                client.test_mode(mode, duration_ms=10000)
                expect_page(client, page)
                ui = client.ui()
                if label is not None:
                    assert any(w.get('text') == label for w in ui['widgets']), f'{mode}: expected UI text missing'
                snap = client.inspect(folder / mode)
                report['checks'].append({'mode': mode, 'page': page, 'snapshot': snap})
            finally:
                client.test_mode('restore')
            expect_page(client, original)
        try:
            client.test_mode('chat_scroll', duration_ms=30000)
            movement = chat_scroll_check(client)
            snap = client.inspect(folder / 'chat-scroll')
            report['checks'].append({'mode': 'chat_scroll', 'movement': movement, 'snapshot': snap})
        finally:
            client.test_mode('restore')
        expect_page(client, original)
        # The timer starts on the device before the HTTP response arrives.
        # A one-second TTL can expire before a subsequent status round trip
        # observes it (seen with 1–2 s Wi-Fi response latency on the real panel).
        # Keep the timeout bounded, but allow both requests to complete first.
        expiry_ms = 5000
        client.test_mode('listening', duration_ms=expiry_ms)
        expect_page(client, 'page_orb')
        time.sleep(expiry_ms / 1000 + 0.2)
        expect_page(client, original)
        report['checks'].append({'mode': 'expiry', 'duration_ms': expiry_ms, 'restored_page': original})
        report['ok'] = True
    except Exception as error:
        report['error_type'] = type(error).__name__
        report['error'] = str(error)
        raise
    finally:
        try:
            client.test_mode('restore')
            expect_page(client, original)
            report['after_snapshot'] = client.inspect(folder / 'after')
        except Exception as error:
            report['ok'] = False
            report['restore_error'] = str(error)
            raise
        finally:
            private_json(folder / 'report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--live', action='store_true', help='Operate the actual panel; does not call paid models')
    parser.add_argument('--brightness-drag', action='store_true', help='Verify brightness detents including 48/52 to 50 percent, then restore')
    parser.add_argument('--test-mode', action='store_true', help='Also verify bounded synthetic voice/fault UI and timeout restore')
    parser.add_argument('--url', default='http://hallpanel.local:8080')
    parser.add_argument('--out', type=Path, default=Path('/tmp') / datetime.now().strftime('hallpanel-ui-%Y%m%d-%H%M%S'))
    args = parser.parse_args()
    if not args.live:
        parser.error('Use --live to exercise the physical UI; offline tests are tests/test_panel_remote.py')
    try:
        with PanelClient(args.url, persistent=True) as client:
            result = run(client, args.out, args.brightness_drag)
            if args.test_mode:
                run_test_mode(client, args.out / 'synthetic')
        print(json.dumps({'ok': result['ok'], 'checks': result['checks'], 'report': str(args.out / 'report.json')},
                         ensure_ascii=False, indent=2))
        return 0
    except (OSError, ValueError, AssertionError, RuntimeError) as error:
        print(f'Physical UI test failed: {error}; evidence: {args.out}', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
