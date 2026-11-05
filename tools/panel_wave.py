#!/usr/bin/env python3
"""See the panel-drawn voice wave on the real screen, without sound, a microphone or a model.

  tools/panel wave [--out NEW_DIR] [--seconds 12] [--url URL]
  tools/panel wave --measure [--seconds 20]     no captures: how fast the panel draws the wave

--measure samples status twice a second while speaking_live runs and reports the wave's render time per
image (orb_prefetch_last_mix_us: p50, p95, max), wave images per second (prepared images, the decoded
counter) and the share of presented frames that showed an old wave (orb_wave_held). The raw samples go to
samples.json. Compare runs of the same --seconds only.

Puts the panel in the test mode speaking_live (tools/panel_test_codegen.py): the voice page with the real
director (orb_director.h) and the wave drawn by the prefetch worker (voice_wave.h), driven by a synthetic
voice (synthetic_speech) with syllables and pauses. Captures the orb about twice a second (the panel's
limit), then restores the page. Writes the frames, an animated wave.gif, a contact sheet sheet.png and
status.json (render time and held frames) to a new private folder and prints its path.

What it does not show: how the wave follows Jarvis's real voice through the microphone, or the sound.
The panel refuses while voice, an announcement, an alarm, night mode or settings are up.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
from panel_remote import PanelClient, private_json  # noqa: E402

ORB = (512 - 140, 205 - 90, 512 + 140, 205 + 90)   # the line's box with a margin, gen.ORB_CENTER
STATUS_KEYS = ('orb_prefetch_last_mix_us', 'orb_prefetch_last_decode_us', 'orb_wave_held',
               'orb_prefetch_errors', 'orb_prefetch_fallbacks', 'build_id', 'uptime_ms')


def capture(client, folder, seconds, clock=time.monotonic, sleep=time.sleep):
    from PIL import Image
    shots = []
    client.test_mode('speaking_live', min(30000, int(seconds * 1000) + 3000))
    try:
        sleep(0.8)   # the swing into the wave takes 0.65 s
        end = clock() + seconds
        while clock() < end:
            began = clock()
            path = folder / f'frame_{len(shots):03d}.png'
            client.screenshot(path, lossless=True)
            with Image.open(path) as image:
                image.crop(ORB).save(path)
            shots.append(path)
            sleep(max(0.0, 0.52 - (clock() - began)))   # at most two captures a second
        status = client.status()
    finally:
        client.test_mode('restore', 100)
    return shots, {key: status.get(key) for key in STATUS_KEYS}


def measure(client, seconds, clock=time.monotonic, sleep=time.sleep):
    """Status samples while speaking_live runs, without captures, and their summary."""
    samples = []
    client.test_mode('speaking_live', min(30000, int(seconds * 1000) + 3000))
    try:
        sleep(1.5)   # past the swing, into the wave
        end = clock() + seconds
        while clock() < end:
            status = client.status()
            samples.append({'t': clock(), 'mix_us': status.get('orb_prefetch_last_mix_us'),
                            'copy_us': status.get('orb_wave_copy_us'), 'prep_us': status.get('orb_wave_prep_us'),
                            'pixels_us': status.get('orb_wave_pixels_us'),
                             'decoded': status.get('orb_prefetch_decoded'), 'held': status.get('orb_wave_held'),
                             'presented': status.get('presentation', {}).get('presented_total'),
                             'page': status.get('page'), 'build_id': status.get('build_id')})
            sleep(0.5)
    finally:
        client.test_mode('restore', 100)
    return samples, summarize(samples)


def summarize(samples):
    if len(samples) < 3 or any(s['page'] != 'page_orb' for s in samples):
        raise ValueError('speaking_live did not hold the voice page for the whole measurement')
    mix = sorted(s['mix_us'] / 1000 for s in samples)
    pick = lambda q: round(mix[min(len(mix) - 1, int(q * len(mix)))], 1)
    first, last = samples[0], samples[-1]
    seconds = last['t'] - first['t']
    part = lambda key: (round(sorted(x[key] for x in samples)[len(samples) // 2] / 1000, 2)
                        if all(x.get(key) is not None for x in samples) else None)
    presented = last['presented'] - first['presented']
    return {'samples': len(samples), 'seconds': round(seconds, 1), 'render_ms_p50': pick(0.5),
            'render_ms_p95': pick(0.95), 'render_ms_max': round(mix[-1], 1),
            'copy_ms_p50': part('copy_us'), 'prep_ms_p50': part('prep_us'), 'pixels_ms_p50': part('pixels_us'),
            'wave_images_per_s': round((last['decoded'] - first['decoded']) / seconds, 1),
            'presented_per_s': round(presented / seconds, 1),
            'held_share': round((last['held'] - first['held']) / max(1, presented), 3),
            'build_id': last['build_id']}


def assemble(shots, folder):
    from PIL import Image
    frames = [Image.open(path).convert('RGB') for path in shots]
    frames[0].save(folder / 'wave.gif', save_all=True, append_images=frames[1:], duration=520, loop=0)
    columns = 4
    w, h = frames[0].size
    sheet = Image.new('RGB', (columns * (w + 4), ((len(frames) + columns - 1) // columns) * (h + 4)))
    for i, frame in enumerate(frames):
        sheet.paste(frame, ((i % columns) * (w + 4), (i // columns) * (h + 4)))
    sheet.save(folder / 'sheet.png')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default=os.environ.get('HALLPANEL_DEBUG_URL', 'http://hallpanel.local:8080'))
    parser.add_argument('--out', type=Path)
    parser.add_argument('--seconds', type=float, default=None)
    parser.add_argument('--measure', action='store_true', help='no captures; time the drawn wave')
    args = parser.parse_args(argv)
    if args.seconds is None:
        args.seconds = 20 if args.measure else 12
    if not 1 <= args.seconds <= 26:
        parser.error('--seconds must be 1-26 (the test mode lasts at most 30 s)')
    if args.out:
        args.out.mkdir(mode=0o700, parents=True, exist_ok=False)
        folder = args.out
    else:
        folder = Path(tempfile.mkdtemp(prefix='hallpanel-wave-'))
    with PanelClient(args.url, persistent=True) as client:
        if args.measure:
            samples, summary = measure(client, args.seconds)
            private_json(folder / 'samples.json', samples)
            private_json(folder / 'summary.json', summary)
            print(json.dumps({'ok': True, 'out': str(folder), **summary}, indent=2))
            return 0
        shots, status = capture(client, folder, args.seconds)
    assemble(shots, folder)
    private_json(folder / 'status.json', {'frames': len(shots), **status})
    print(json.dumps({'ok': True, 'out': str(folder), 'frames': len(shots), **status}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
