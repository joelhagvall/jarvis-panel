#!/usr/bin/env python3
"""Generate a small headless LVGL preview using the actual firmware boot widgets.

Usage: .venv/bin/python tools/preview_boot.py --out /tmp/NEW_DIR
Compile the returned config with ESPHome, then run its host program. The program
writes a BMP through SDL's snapshot API and exits; no desktop capture is needed.
"""
import argparse
import copy
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]


def generate(folder):
    os.environ['HALLPANEL_BOARD'] = '1'
    sys.path.insert(0, str(ROOT / 'styles/glass-v2'))
    import jarvis_orb
    cfg = jarvis_orb.config(remote='http://example.invalid/assets')
    cover = next(w for w in cfg['lvgl']['top_layer']['widgets'] if w.get('obj', {}).get('id') == 'boot_cover')
    cover = copy.deepcopy(cover)
    image = next(i for i in cfg['image'] if i['id'] == 'blank')
    image = {**image, 'file': str(ROOT / 'styles/glass-v2/assets/mic_small.png')}
    # Same cached font files consumed by the panel build, available offline.
    fonts = []
    for weight, size in ((400, 56), (500, 16)):
        fonts.append({'file': str(ROOT / f'styles/glass-v2/.esphome/font/Inter@{weight}@False@v1.ttf'),
                      'id': jarvis_orb.gen.f(weight, size), 'size': size, 'bpp': 4,
                      'glyphs': jarvis_orb.gen.GLYPHS})
    result = {
        'esphome': {'name': 'hallpanel-boot-preview', 'includes': [str(ROOT / 'styles/glass-v2/boot_logo.h')],
                    'build_flags': cfg['esphome']['build_flags'],
                    'on_boot': [next(b for b in cfg['esphome']['on_boot'] if b['priority'] == 394),
                              {'priority': -100, 'then': [
            {'delay': '500ms'}, {'snapshot.take': {'id': 'preview_display', 'filename': 'boot.bmp'}},
            {'lambda': 'std::exit(0);'}]}]},
        'host': {}, 'logger': {'level': 'INFO'},
        'display': [{'platform': 'sdl', 'id': 'preview_display', 'headless': True,
                     'dimensions': {'width': 1024, 'height': 600},
                     'update_interval': 'never', 'auto_clear_enabled': False}],
        'font': fonts, 'image': [image],
        'lvgl': {'displays': ['preview_display'], 'buffer_size': '100%',
                 'pages': [{'id': 'preview', 'widgets': [cover]}]},
    }
    folder.mkdir(parents=True, exist_ok=False)
    config = folder / 'boot-preview.yaml'
    config.write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return config


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    print(generate(args.out.resolve()))
