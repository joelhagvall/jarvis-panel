#!/usr/bin/env python3
"""Free physical UI regression checks. References are explicit; a failed run never updates them."""
import argparse
from datetime import datetime
import hashlib
import html
import json
import os
import shutil
from pathlib import Path
import sys
import tempfile
import time

import numpy as np
from PIL import Image, ImageDraw

from panel_remote import PanelClient, ROOT, private_json
from panel_ui_test import bar_page, expect_page, select_tab

POLICY_PATH = ROOT / 'tests/visual/policy.json'
DEFAULT_BASELINE = ROOT / '.panel-visual/reference-panel-v19'
FIELDS = ('type', 'x', 'y', 'width', 'height', 'clickable', 'text', 'min', 'max')
TABS = {'page_glass_v2': 'Gå hemifrån', 'page_hemmet': 'Hemmet', 'page_musik': 'Spelas nu', 'page_tavla': 'Tavla'}


def policy_digest(policy):
    return hashlib.sha256(json.dumps(policy, sort_keys=True).encode()).hexdigest()


def new_folder(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, mode=0o700, exist_ok=False)
    path.chmod(0o700)
    return path


def private_image(path, image):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as out:
        image.save(out, format='PNG')


def contract(ui, scene):
    """Select only declared stable controls. Changing live text cannot move the reference anchors."""
    if ui['page'] != scene['page'] or ui.get('truncated'):
        raise ValueError('Unexpected page or truncated widget tree')
    widgets = ui['widgets']
    paths = {w['path']: w for w in widgets}
    wanted = set(scene.get('paths', []))
    subtree = scene.get('subtree')
    if subtree:
        if subtree not in paths:
            raise ValueError(f'Missing widget {subtree}')
        wanted.update(p for p in paths if p == subtree or p.startswith(subtree + '/'))
        wanted.difference_update(scene.get('dynamic_paths', []))
    for label in scene.get('labels', []):
        found = [w['path'] for w in widgets if w.get('text') == label]
        if not found:
            raise ValueError(f'Missing static label {label!r}')
        wanted.update(found)
    dynamic_text = scene.get('dynamic_text', {})
    dynamic_width = scene.get('dynamic_width', {})
    wanted.update(dynamic_text)
    result = {}
    for path in sorted(wanted):
        if path not in paths:
            raise ValueError(f'Missing widget {path}')
        widget = paths[path]
        validate_bounds(widget)
        result[path] = {k: widget[k] for k in FIELDS if k in widget}
        if path in dynamic_text:
            rule = dynamic_text[path]
            if widget.get('type') != 'label' or widget.get('text') not in rule['values'] or widget['width'] > rule['max_width']:
                raise ValueError(f'Invalid dynamic label {path}')
            # Text determines this label's content width. Position, height, type,
            # bounds and every other declared anchor still use the exact reference.
            result[path].pop('text', None)
            if rule.get('anchor') == 'right':
                result[path]['x'] += widget['width']
            elif rule.get('anchor', 'left') != 'left':
                raise ValueError(f'Unknown dynamic anchor {path}')
            result[path].pop('width', None)
        elif path in dynamic_width:
            # Tavla's plaque is as wide as the day's NASA title (SIZE_CONTENT); it grows to the right
            # from a fixed left edge. Everything else about it stays exact.
            if widget['width'] > dynamic_width[path]['max_width']:
                raise ValueError(f'Dynamic-width widget too wide {path}')
            result[path].pop('width', None)
    if not result:
        raise ValueError('Empty layout contract')
    return result


def validate_bounds(widget, size=(1024, 600)):
    """Fail explicit stable anchors that are empty, off screen or clipped."""
    fields = [widget.get(key) for key in ('x', 'y', 'width', 'height')]
    if any(type(value) is not int for value in fields):
        raise ValueError('Layout anchor has invalid geometry')
    x, y, width, height = fields
    if width <= 0 or height <= 0 or x < 0 or y < 0 or x + width > size[0] or y + height > size[1]:
        raise ValueError(f'Clipped or empty layout anchor {widget.get("path")}')


def layout_diff(expected, actual, tolerance=0):
    changes = []
    for path in sorted(expected.keys() | actual.keys()):
        a, b = expected.get(path), actual.get(path)
        if a is None or b is None:
            changes.append({'path': path, 'field': 'widget', 'expected': a, 'actual': b})
            continue
        for field in sorted(a.keys() | b.keys()):
            before, after = a.get(field), b.get(field)
            equal = before == after
            if field in ('x', 'y', 'width', 'height') and type(before) is int and type(after) is int:
                equal = abs(before - after) <= tolerance
            if not equal:
                changes.append({'path': path, 'field': field, 'expected': before, 'actual': after})
    return changes


def pixel_mask(size, pixels):
    mask = Image.new('L', tuple(size), 0)
    draw = ImageDraw.Draw(mask)
    x1, y1, x2, y2 = pixels['include']
    if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
        raise ValueError('Invalid pixel inclusion rectangle')
    draw.rounded_rectangle((x1, y1, x2 - 1, y2 - 1), radius=pixels['radius'], fill=255)
    for x1, y1, x2, y2 in pixels.get('exclude', []):
        if not (0 <= x1 < x2 <= size[0] and 0 <= y1 < y2 <= size[1]):
            raise ValueError('Invalid pixel exclusion rectangle')
        draw.rectangle((x1, y1, x2 - 1, y2 - 1), fill=0)
    if not mask.getbbox():
        raise ValueError('Pixel policy checks no pixels')
    return mask


def pixel_diff(expected, actual, mask, tolerance, max_changed):
    if expected.size != actual.size or mask.size != expected.size:
        raise ValueError('Screenshot dimensions changed')
    a = np.asarray(expected.convert('RGB'), dtype=np.int16)
    b = np.asarray(actual.convert('RGB'), dtype=np.int16)
    included = np.asarray(mask) > 0
    if not np.any(included):
        raise ValueError('Pixel policy checks no pixels')
    changed = (np.max(np.abs(a - b), axis=2) > tolerance) & included
    count = int(np.count_nonzero(changed))
    points = np.argwhere(changed)
    bounds = None if not count else [int(points[:, 1].min()), int(points[:, 0].min()),
                                   int(points[:, 1].max()) + 1, int(points[:, 0].max()) + 1]
    # Dimmed current pixels, with every changed pixel highlighted in magenta.
    marked = (b.clip(0, 255) // 3).astype(np.uint8)
    marked[~included] = 0
    marked[changed] = (255, 0, 180)
    return {'ok': count <= max_changed, 'changed_pixels': count,
            'checked_pixels': int(np.count_nonzero(included)), 'bounds': bounds}, Image.fromarray(marked)


def wait_ready(client, seconds):
    deadline = time.monotonic() + seconds
    while True:
        try:
            status, ui = client.status(), client.ui()
            # The boot cover is a top-layer object with its own 56px Jarvis title.
            boot = any(w.get('text') == 'Jarvis' and w['path'].startswith('top/') for w in ui['widgets'])
            if status['page'] in TABS and status['uptime_ms'] >= 25000 and not boot:
                return
        except OSError:
            pass  # Only reads are repeated while the device boots.
        if time.monotonic() >= deadline:
            raise TimeoutError('Panel not ready for visual testing; no reference was changed')
        time.sleep(1)


def open_settings(client):
    ui = client.ui()
    widgets = ui['widgets']
    night = next(w for w in widgets if w.get('text') == 'Natt')
    parent, index = night['path'].rsplit('/', 1)[0].rsplit('/', 1)
    gear_path = parent + '/' + str(int(index) + 1)
    gear = next(w for w in widgets if w['path'] == gear_path and w['type'] == 'button')
    client.tap(gear['x'] + gear['width'] // 2, gear['y'] + gear['height'] // 2)
    if not any(w.get('text') == 'Inställningar' for w in client.ui()['widgets']):
        raise ValueError('Settings did not open')


def navigate_tab(client, page):
    select_tab(client, page)


def stable_capture(client, folder, policy, scene):
    """Wait for a declared quiet interval, then compare two independent captures."""
    time.sleep(policy.get('settle_ms', 250) / 1000)
    snap = client.inspect(folder, lossless=True)
    ui = json.loads(Path(snap['ui_path']).read_text())
    selected = contract(ui, scene)
    if 'pixels' in scene:
        second = folder / 'stability.png'
        time.sleep(policy.get('stability_ms', 150) / 1000)
        client.screenshot(second, lossless=True)
        with Image.open(Path(snap['screenshot']['path'])) as a, Image.open(second) as b:
            stats, _ = pixel_diff(a, b, pixel_mask(policy['size'], scene['pixels']),
                                  policy['channel_tolerance'], policy['max_changed_pixels'])
        if not stats['ok']:
            raise ValueError('Unstable screenshot; no reference was changed')
    return selected


def capture(client, folder, policy):
    """Capture idle views and explicitly declared bounded, visual-only test scenes."""
    original = client.status()['page']
    if original not in TABS:
        raise ValueError('Run from dashboard, Hemmet or Tavla while voice/night mode is idle')
    if any(w.get('text') == 'Inställningar' for w in client.ui()['widgets']):
        raise ValueError('Close settings before starting a visual run')
    manifest = {'format': 1, 'policy_sha256': policy_digest(policy),
                'captured_at': datetime.now().astimezone().isoformat(), 'scenes': {}}
    try:
        bar_page(client)
        for name, scene in policy['scenes'].items():
            # Stop instead of disrupting an incoming voice interaction or another user's navigation.
            if client.status()['page'] not in TABS:
                raise ValueError('Panel left idle tab pages during capture')
            synthetic = scene.get('test_mode')
            navigate_tab(client, 'page_glass_v2' if synthetic else scene['page'])
            modal = bool(scene.get('subtree'))
            try:
                if synthetic:
                    client.test_mode(synthetic, duration_ms=20000)
                    expect_page(client, scene['page'])
                if modal:
                    open_settings(client)
                entry = {'image': f'{name}/screen.png', 'ui': f'{name}/ui.json',
                         'contract': stable_capture(client, folder / name, policy, scene),
                         'synthetic_ui': bool(synthetic)}
                manifest['scenes'][name] = entry
            finally:
                if synthetic:
                    client.test_mode('restore')
                # Do not tap through an overlay if a real voice interaction replaced the view.
                if modal and client.status()['page'] == scene['page'] and any(
                        w.get('text') == 'Inställningar' for w in client.ui()['widgets']):
                    client.tap_text('Klar')
    finally:
        page = client.status()['page']
        if page in TABS:
            navigate_tab(client, original)
        else:
            raise ValueError('Panel became busy; left current interaction untouched')
    private_json(folder / 'capture.json', manifest)
    return manifest


def load_capture(folder, policy, reference=False):
    path = folder / ('reference.json' if reference else 'capture.json')
    manifest = json.loads(path.read_text())
    if manifest.get('format') != 1 or manifest.get('policy_sha256') != policy_digest(policy):
        raise ValueError('Reference/capture policy differs; review and record a new explicit reference')
    if set(manifest.get('scenes', {})) != set(policy['scenes']):
        raise ValueError('Reference/capture is incomplete')
    # Recompute contracts from source UI rather than trusting an empty/tampered saved contract.
    for name, scene in policy['scenes'].items():
        entry = manifest['scenes'][name]
        if entry.get('image') != f'{name}/screen.png' or entry.get('ui') != f'{name}/ui.json':
            raise ValueError('Unexpected capture file layout')
        entry['contract'] = contract(json.loads((folder / entry['ui']).read_text()), scene)
        with Image.open(folder / entry['image']) as image:
            if image.size != tuple(policy['size']) or image.format != 'PNG':
                raise ValueError('Visual tests require exact full-size PNG captures')
    return manifest


def rebind_reference(source, folder, policy):
    """Explicit reviewed selector migration: copy old evidence byte-for-byte, never recapture/replace pixels."""
    source=Path(source).resolve()
    old=json.loads((source/'reference.json').read_text())
    if old.get('format')!=1 or set(old.get('scenes',{}))!=set(policy['scenes']):
        raise ValueError('Reference rebind requires the same complete scene set')
    for name,entry in old['scenes'].items():
        if entry.get('image')!=f'{name}/screen.png' or entry.get('ui')!=f'{name}/ui.json':
            raise ValueError('Unexpected source reference file layout')
        shutil.copytree(source/name,folder/name)
    manifest={**old,'policy_sha256':policy_digest(policy),'rebound_from':str(source),
              'previous_policy_sha256':old['policy_sha256']}
    private_json(folder/'capture.json',manifest)
    validated=load_capture(folder,policy)
    image_hashes={}
    for name in policy['scenes']:
        for original in (source/name).rglob('*.png'):
            relative=original.relative_to(source)
            expected=hashlib.sha256(original.read_bytes()).hexdigest()
            if hashlib.sha256((folder/relative).read_bytes()).hexdigest()!=expected:
                raise ValueError('Rebind changed reference pixels')
            image_hashes[str(relative)]=expected
    private_json(folder/'reference.json',validated)
    private_json(folder/'rebind.json',{'source':str(source),'unchanged_png_sha256':image_hashes,
                                     'policy_sha256':policy_digest(policy)})
    return validated


def compare(reference, candidate, folder, policy):
    baseline = load_capture(reference, policy, reference=True)
    current = load_capture(candidate, policy)
    report = {'ok': True, 'baseline': str(reference), 'candidate': str(candidate), 'scenes': {}}
    for name, scene in policy['scenes'].items():
        changes = layout_diff(baseline['scenes'][name]['contract'], current['scenes'][name]['contract'],
                              policy['geometry_tolerance'])
        result = {'ok': not changes, 'layout_changes': changes, 'pixel_check': 'not_applicable_live_background'}
        if 'pixels' in scene:
            with Image.open(reference / baseline['scenes'][name]['image']) as a, \
                    Image.open(candidate / current['scenes'][name]['image']) as b:
                stats, diff = pixel_diff(a, b, pixel_mask(policy['size'], scene['pixels']),
                                         policy['channel_tolerance'], policy['max_changed_pixels'])
            result['pixels'] = stats
            result['pixel_check'] = 'compared'
            result['ok'] &= stats['ok']
            private_image(folder / f'{name}-diff.png', diff)
        report['scenes'][name] = result
        report['ok'] &= result['ok']
    private_json(folder / 'report.json', report)
    # Local, static gallery: no remote URLs, scripts, credentials or embedded UI text.
    cards = []
    for name, result in report['scenes'].items():
        a = html.escape(os.path.relpath(reference / name / 'screen.png', folder), quote=True)
        b = html.escape(os.path.relpath(candidate / name / 'screen.png', folder), quote=True)
        diff = f'<img alt="Diff" src="{name}-diff.png">' if 'pixels' in result else ''
        cards.append(f'<h2>{name}: {"PASS" if result["ok"] else "FAIL"}</h2>'
                     f'<p>{len(result["layout_changes"])} layout changes; '
                     f'{result.get("pixels", {}).get("changed_pixels", "—")} changed pixels</p>'
                     f'<div><img alt="Reference" src="{a}"><img alt="Current" src="{b}">{diff}</div>')
    document = '<!doctype html><meta charset="utf-8"><title>Hallpanel visual regression</title>' \
               '<style>body{background:#101820;color:white;font:16px system-ui}div{display:flex;flex-wrap:wrap}' \
               'img{width:32%;min-width:300px;height:auto;object-fit:contain;margin:4px}</style>' \
               '<h1>Hallpanel visual regression</h1><p>Reference · Current · Diff (magenta)</p>' + ''.join(cards)
    with os.fdopen(os.open(folder / 'report.html', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as out:
        out.write(document)
    return report


def failed_scenes(report):
    """What failed, per scene, so a failed gate names it without opening report.json (read by hand twice, 2026-10-29)."""
    out = {}
    for name, scene in report.get('scenes', {}).items():
        if scene.get('ok', True):
            continue
        pixels = scene.get('pixels') or {}
        out[name] = {'layout_changes': [f"{c['path']} {c['field']} {c['expected']}->{c['actual']}"
                                        for c in scene.get('layout_changes', [])],
                     **({'changed_pixels': pixels.get('changed_pixels'), 'bounds': pixels.get('bounds')}
                        if pixels and not pixels.get('ok', True) else {})}
    return out


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('record', 'capture', 'check', 'compare', 'rebind'):
        sub = commands.add_parser(name)
        sub.add_argument('--out', type=Path, required=name in ('record', 'capture', 'rebind'))
        if name=='rebind':
            sub.add_argument('--source',type=Path,required=True)
            continue
        if name in ('check', 'compare'):
            sub.add_argument('--baseline', type=Path, default=DEFAULT_BASELINE)
        if name == 'compare':
            sub.add_argument('--candidate', type=Path, required=True)
        else:
            sub.add_argument('--url', default=os.environ.get('HALLPANEL_DEBUG_URL', 'http://hallpanel.local:8080'))
            sub.add_argument('--wait-ready', type=int, default=0)
    args = parser.parse_args()
    folder = None
    try:
        policy = json.loads(POLICY_PATH.read_text())
        if args.command in ('check', 'compare'):
            load_capture(args.baseline.expanduser().resolve(), policy, reference=True)
        folder = new_folder(args.out) if args.out else Path(tempfile.mkdtemp(prefix='hallpanel-visual-'))
        report = None
        if args.command=='rebind':
            rebind_reference(args.source,folder,policy)
        elif args.command != 'compare':
            with PanelClient(args.url, persistent=True) as client:
                wait_ready(client, args.wait_ready)
                manifest = capture(client, folder, policy)
            if args.command == 'record':
                private_json(folder / 'reference.json', manifest)
        if args.command in ('check', 'compare'):
            candidate = args.candidate.expanduser().resolve() if args.command == 'compare' else folder
            report = compare(args.baseline.expanduser().resolve(), candidate, folder, policy)
        print(json.dumps({'ok': report['ok'] if report else True, 'command': args.command,
                          'out': str(folder), 'report': str(folder / 'report.html') if report else None,
                          **({'failed': failed_scenes(report)} if report and not report['ok'] else {})}, indent=2))
        return 0 if report is None or report['ok'] else 1
    except (OSError, ValueError, KeyError, StopIteration, AssertionError) as error:
        if folder:
            private_json(folder / 'error.json', {'ok': False, 'error_type': type(error).__name__, 'error': str(error)})
        print(f'Visual test failed: {error}; evidence: {folder}; reference unchanged', file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
