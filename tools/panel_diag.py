#!/usr/bin/env python3
"""Host side of the panel's diagnostics (docs/remote-panel.md, "Diagnostics"). Used by tools/panel:

  tools/panel logs [--previous] [--json]          the panel's log ring; --previous: the boot before this one
  tools/panel crash-report [--elf ELF]            why the last boot ended, the crash record, decoded to source lines
  tools/panel tasks [--seconds 2]                 CPU share per task and core, stack margin, heap
  tools/panel clip --out DIR [--seconds 2] [--region X Y W H] [--scale 1|2] [--quality 75]
  tools/panel redraw --out DIR [--seconds 3]      heat map of where LVGL draws, over a screenshot
  tools/panel touches [--out FILE] [--last-seconds 60]   the real finger's recent samples
  tools/panel replay FILE [--since-ms A --until-ms B]    play recorded samples back through LVGL
  tools/panel settings [KEY VALUE]                day_volume, brightness, night_light (%), night_start/night_end (HH:MM)

Everything written is private (files 600, folders 700): screens and touches can show Joel's own data.
"""
import glob
import json
import os
import re
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ELF = ROOT / 'styles/glass-v2/.esphome/build/hallpanel/build/firmware.elf'
ADDR2LINE = str(Path.home() / 'Library/Caches/esphome/idf/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-addr2line')
FRAME_HEADER = struct.Struct('<5I')
RESET_REASONS = {0: 'unknown', 1: 'power on', 2: 'external pin', 3: 'software restart', 4: 'panic (crash)',
                 5: 'interrupt watchdog', 6: 'task watchdog', 7: 'other watchdog', 8: 'deep sleep', 9: 'brownout',
                 10: 'SDIO', 11: 'USB', 12: 'JTAG', 13: 'eFuse', 14: 'power glitch', 15: 'CPU lockup'}
CODE_ADDRESS = re.compile(r'(?:\bPC|\bBT\d+):\s+(0x[0-9A-F]{8})')
SETTINGS = ('day_volume', 'brightness', 'night_light', 'night_start', 'night_end')


def reset_reason(code):
    return RESET_REASONS.get(code, f'unknown ({code})')


def private_dir(path):
    path = Path(path).expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(path.iterdir()):
        raise ValueError(f'Output directory must be empty: {path}')
    path.chmod(0o700)
    return path


def private_write(path, data):
    mode = 'w' if isinstance(data, str) else 'wb'
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), mode) as out:
        out.write(data)


# ---- clips ----
def parse_clip(body):
    """(header, frames) from POST /clip's body: one JSON line, then per frame a 20-byte header and the JPEG."""
    newline = body.find(b'\n')
    if newline < 0:
        raise ValueError('Clip has no header')
    header = json.loads(body[:newline])
    if header.get('format') != 'hpclip1':
        raise ValueError('Not a panel clip')
    frames, at = [], newline + 1
    while at < len(body):
        if at + FRAME_HEADER.size > len(body):
            raise ValueError('Truncated clip frame header')
        size, sequence, presented, grabbed, cost = FRAME_HEADER.unpack_from(body, at)
        at += FRAME_HEADER.size
        if at + size > len(body):
            raise ValueError('Truncated clip frame')
        frames.append({'sequence': sequence, 'presented_us': presented, 'grabbed_us': grabbed, 'grab_cost_us': cost,
                       'jpeg': body[at:at + size]})
        at += size
    if 'frames' in header and header['frames'] != len(frames):
        raise ValueError('Clip frame count does not match its header')
    return header, frames


def clip_timing(frames):
    """Intervals between presentations (ms) and gaps in the submission sequence."""
    intervals = [round(((b['presented_us'] - a['presented_us']) & 0xffffffff) / 1000, 1) for a, b in zip(frames, frames[1:])]
    gaps = sum(max(0, ((b['sequence'] - a['sequence']) & 0xffffffff) - 1) for a, b in zip(frames, frames[1:]))
    ordered = sorted(intervals)
    pick = lambda q: ordered[min(len(ordered) - 1, int(q * len(ordered)))] if ordered else None
    return {'frames': len(frames), 'sequence_gaps': gaps, 'intervals_ms': intervals,
            'median_interval_ms': pick(0.5), 'p95_interval_ms': pick(0.95),
            'max_interval_ms': max(intervals) if intervals else None,
            'max_grab_cost_us': max((f.get('grab_cost_us', 0) for f in frames), default=None)}


def write_clip(header, frames, folder):
    folder = private_dir(folder)
    width, height = header['width'], header['height']
    names = []
    for i, frame in enumerate(frames):
        name = f'frame-{i:04d}-seq{frame["sequence"]}.jpg'
        private_write(folder / name, frame['jpeg'])
        names.append(name)
    timing = clip_timing(frames)
    meta = {**header, 'timing': timing, 'frames_detail': [{k: v for k, v in f.items() if k != 'jpeg'} | {'file': n}
                                                           for f, n in zip(frames, names)]}
    private_write(folder / 'clip.json', json.dumps(meta, indent=2))
    video = None
    if frames and shutil.which('ffmpeg'):
        # Real presentation times: each frame lasts until the next was presented.
        lines = []
        for i, name in enumerate(names):
            lines.append(f"file '{name}'")
            duration = timing['intervals_ms'][i] / 1000 if i < len(timing['intervals_ms']) else 1 / 60
            lines.append(f'duration {max(duration, 0.001):.4f}')
        lines.append(f"file '{names[-1]}'")
        private_write(folder / 'frames.txt', '\n'.join(lines) + '\n')
        video = folder / 'clip.mp4'
        result = subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'concat', '-safe', '0', '-i', 'frames.txt',
                                 '-vf', f'crop={width}:{height}:0:0,format=yuv420p', '-fps_mode', 'vfr',
                                 '-c:v', 'libx264', '-crf', '18', 'clip.mp4'], cwd=folder, capture_output=True, text=True)
        if result.returncode:
            video = None
            private_write(folder / 'ffmpeg-error.txt', result.stderr)
        else:
            os.chmod(folder / 'clip.mp4', 0o600)
    # Measured 2026-10-29 on the orb at 60 fps: full screen at scale 2 cost ~12 ms per frame and halved the
    # rate; the orb's 384x368 region at scale 2 cost ~2.5 ms and kept 16.6 ms intervals. Say so when it happens.
    slow = (timing['max_grab_cost_us'] or 0) > 8000
    note = ('The copy cost up to %.1f ms per frame and slowed the panel; intervals are not its normal timing. '
            'Use a smaller --region or --scale 2.' % (timing['max_grab_cost_us'] / 1000)) if slow else None
    return {'folder': str(folder), 'video': str(video) if video else None, 'observer_note': note, **{k: header[k] for k in
            ('width', 'height', 'frames', 'missed_presentations', 'busy_drops', 'truncated') if k in header},
            'timing': {k: v for k, v in timing.items() if k != 'intervals_ms'}}


# ---- touches ----
def replay_script(samples, since_ms=None, until_ms=None):
    """'t,x,y,p;...' from recorded [t, x, y, pressed] samples, starting at the first press in the window and
    ending at the last complete release, times relative to that press."""
    inside = lambda t: (since_ms is None or t >= since_ms) and (until_ms is None or t <= until_ms)
    # Begin at a press that starts inside the window (the sample before it is a release, or there is none),
    # never in the middle of a press that began earlier.
    window = [s for i, s in enumerate(samples) if inside(s[0])]
    first = next((i for i, s in enumerate(samples) if inside(s[0]) and s[3] and (i == 0 or not samples[i - 1][3])), None)
    window = [] if first is None else [s for s in samples[first:] if inside(s[0])]
    while window and window[-1][3]:
        window.pop()
    if len(window) < 2:
        raise ValueError('No complete press and release in that window')
    start = window[0][0]
    return ';'.join(f'{t - start},{x},{y},{1 if p else 0}' for t, x, y, p in window)


def gestures(samples):
    """[{start_ms, end_ms, from, to, samples, kind}] from recorded samples: one per press-to-release."""
    found, current = [], None
    for t, x, y, pressed in samples:
        if pressed and current is None:
            current = {'start_ms': t, 'from': [x, y], 'samples': 0}
        if current is not None:
            current.update(end_ms=t, to=[x, y])
            current['samples'] += 1
            if not pressed:
                dx, dy = x - current['from'][0], y - current['from'][1]
                current['kind'] = ('tap' if max(abs(dx), abs(dy)) <= 40 else
                                   ('swipe left' if dx < 0 else 'swipe right') if abs(dx) >= abs(dy) else
                                   ('swipe up' if dy < 0 else 'swipe down'))
                found.append(current)
                current = None
    return found


def gesture_fires_ms(points, min_velocity, distance=50):
    """When LVGL 9.5's indev_gesture would send the gesture for these [t, x, y, p] reads, ms after the first,
    or None. It zeroes the running sum on any read that moved less than min_velocity on both axes."""
    sx = sy = 0
    for a, b in zip(points, points[1:]):
        vx, vy = b[1] - a[1], b[2] - a[2]
        if abs(vx) < min_velocity and abs(vy) < min_velocity:
            sx = sy = 0
        sx, sy = sx + vx, sy + vy
        if abs(sx) > distance or abs(sy) > distance:
            return b[0] - points[0][0]
    return None


def touch_summary(touches, logs, since_ms):
    """What Joel did and what the panel said meanwhile: warnings, errors and touch-to-frame latency.
    2026-10-29: after "I swiped around", the gestures alone did not answer "did you see anything wrong?"."""
    lines = []
    for line in logs['lines']:
        uptime = int(line.split(' ', 1)[0])
        text = line.split(' ', 2)[2] if line.count(' ') >= 2 else line
        if uptime >= since_ms and ('[W]' in text or '[E]' in text or '[latency:' in text or 'Page record left' in text):
            lines.append({'uptime_ms': uptime, 'text': text})
    latency = [float(l['text'].rsplit(' ', 2)[-2]) for l in lines if 'next refresh ready' in l['text']]
    found = gestures([s for s in touches['samples'] if s[0] >= since_ms])
    for g in found:
        if g['kind'].startswith('swipe'):
            reads = [s for s in touches['samples'] if g['start_ms'] <= s[0] <= g['end_ms']]
            g['lvgl_gesture_after_ms'] = gesture_fires_ms(reads, 0)  # the panel's setting since 2026-10-29
    return {'gestures': found,
            'warnings_errors': [l for l in lines if '[W]' in l['text'] or '[E]' in l['text']],
            'touch_to_frame_ms': sorted(latency),
            'notes': [l for l in lines if 'Page record left' in l['text']]}


def swipe_script(x1, x2, ms=150, step=7):
    points = [f'{t},{x1 + (x2 - x1) * t // ms},300,1' for t in range(0, ms + 1, step)]
    return ';'.join(points + [f'{ms + step},{x2},300,0'])


def swipe_tour(client, rounds=1):
    """Each adjacent page change by a replayed 4 px/ms swipe, Gå hemifrån -> Tavla and back. Never swipes left
    on Tavla (that is Natt). Starts only from Gå hemifrån and ends there (2026-10-29 page-change study)."""
    import time
    order = ['page_glass_v2', 'page_hemmet', 'page_musik', 'page_tavla']
    if client.status()['page'] != order[0]:
        raise ValueError('Start the swipe tour on Gå hemifrån (page_glass_v2)')
    def fresh(before, after, key):
        n = min(after[key]['total'] - before[key]['total'], len(after[key]['us']))
        return [round(x / 1000, 1) for x in after[key]['us'][len(after[key]['us']) - n:]] if n > 0 else []
    rows = []
    steps = [(order[i], 'left') for i in range(3)] + [(order[i], 'right') for i in (3, 2, 1)]
    for _ in range(rounds):
        for page, direction in steps:
            status = client.status()
            if status['page'] != page:
                raise ValueError(f'Expected {page}, found {status["page"]}; stopped')
            before = status['responsiveness']
            client.replay(swipe_script(800, 200) if direction == 'left' else swipe_script(200, 800))
            time.sleep(0.8)
            after_status = client.status()
            after = after_status['responsiveness']
            work = fresh(before, after, 'render_work')
            rows.append({'from': page, 'to': after_status['page'], 'direction': direction,
                         'press_to_frame_ms': fresh(before, after, 'remote_press'), 'frames': len(work),
                         'render_work_ms': work})
    return rows


BOOT_EVENTS = ('wifi', 'online_image', 'Downloading', 'packed_orb', 'sound', 'i2s', 'allocate', 'Page record',
               'speaker', 'api cleared', 'CRASH', 'abort')


def boot_heap(client, timeline=False, restarts=0):
    """The boot's RAM low point; --timeline puts the 50 ms series (every 4th) beside log events; --restarts
    repeats a clean restart and reports each boot (2026-10-29: how the 64 kB TCP window was found and checked)."""
    import time
    if not 0 <= restarts <= 5:
        raise ValueError('At most 5 restarts in one run')
    def one():
        for _ in range(30):
            heap = client.tasks(0.5).get('boot_heap')
            if heap:
                return heap
            time.sleep(3)  # the sampler reports after the first 40 s
        raise RuntimeError('No boot-heap result within 90 s')
    if restarts:
        rows = []
        for _ in range(restarts):
            boot = client.restart()
            heap = one()
            rows.append({'boot_id': boot['boot_id'], **{k: heap[k] for k in
                         ('internal_min', 'internal_min_at_ms', 'dma_min', 'dma_min_at_ms', 'largest_block_at_min')}})
        return rows
    heap = one()
    if not timeline:
        return {k: v for k, v in heap.items() if not k.endswith('_kb')}
    logs = client.logs()
    lines = [f"low point: internal {heap['internal_min']} B at {heap['internal_min_at_ms']} ms, "
             f"DMA {heap['dma_min']} B at {heap['dma_min_at_ms']} ms", 't ms  internal kB  DMA kB  events']
    step = heap['timeline_step_ms']
    events = [(int(l.split(' ', 1)[0]), l.split(' ', 2)[2][:110]) for l in logs['lines']
              if any(k in l for k in BOOT_EVENTS) and l.split(' ', 1)[0].isdigit()]
    for i in range(0, len(heap['internal_kb']), 4):
        t = i * step
        window = [e for at, e in events if t <= at < t + 4 * step]
        lows = [v for v in heap['internal_kb'][i:i + 4] if v is not None]
        dmas = [v for v in heap['dma_kb'][i:i + 4] if v is not None]
        if lows:
            lines.append(f"{t:6} {min(lows):>8} {min(dmas):>7}  " + ' | '.join(window))
    return '\n'.join(lines)


def memory(client):
    """One answer to "how is the RAM?" (2026-10-29): heap per kind in kB, the boot's low point and uptime."""
    tasks, status = client.tasks(0.5), client.status()
    kb = lambda v: round(v / 1024, 1)
    rows = {name: {'free_kb': kb(h['free']), 'lowest_since_boot_kb': kb(h['minimum_free']),
                   'largest_block_kb': kb(h['largest_free_block']), 'allocated_kb': kb(h['allocated'])}
            for name, h in tasks['heap'].items()}
    boot = tasks.get('boot_heap')
    return {'uptime_min': status['uptime_ms'] // 60000, 'heap': rows,
            'boot_low_point': None if not boot else {'internal_kb': kb(boot['internal_min']),
                                                    'at_ms': boot['internal_min_at_ms'], 'dma_kb': kb(boot['dma_min'])},
            'lp_ram': '16 kB log ring + 8.5 kB older boot + abort record of 32 kB (diagnostics.cpp)',
            'note': 'lowest_since_boot covers only this uptime; read again after voice and music'}


# Limits for `tools/panel health` (2026-10-29, Joel's "andra stats man måste hålla koll på?"). Judgement from this
# panel's measurements, not vendor limits: see docs/remote-panel.md "Health".
HEALTH_LIMITS = {'stack_min_free_bytes': 256, 'internal_lowest_kb': 32, 'dma_lowest_kb': 24, 'psram_lowest_kb': 2048,
                 'render_p50_ms': 16.0, 'stall_ms': 150, 'wifi_rssi_dbm': -70}
STALL = re.compile(r'took a long time for an operation \((\d+) ms\)')


def health_report(status, tasks, logs, crash, limits=HEALTH_LIMITS):
    """[{name, value, limit, ok, note}] from one status/tasks/logs/crash read; all read-only."""
    checks = []
    def add(name, value, limit, ok, note=''):
        checks.append({'name': name, 'value': value, 'limit': limit, 'ok': bool(ok), 'note': note})
    reason = status['reset_reason']
    add('restart reason', f'{reason} ({reset_reason(reason)})', 'power on or software restart', reason in (1, 3))
    add('crash record', len(crash.get('esphome_record', [])), 0, not crash.get('esphome_record'),
        (crash.get('abort') or {}).get('text', ''))
    worst = min(tasks['tasks'], key=lambda t: t['stack_min_free_bytes'])
    add('smallest stack margin', f"{worst['stack_min_free_bytes']} B ({worst['name']})",
        f">= {limits['stack_min_free_bytes']} B", worst['stack_min_free_bytes'] >= limits['stack_min_free_bytes'],
        'IDF ipc0/ipc1 have 1024 B stacks; ~570 B free is normal')
    heap = tasks['heap']
    for name, kind in (('internal_lowest_kb', 'internal'), ('dma_lowest_kb', 'dma'), ('psram_lowest_kb', 'psram')):
        value = round(heap[kind]['minimum_free'] / 1024, 1)
        add(f'{kind} RAM lowest since boot', f'{value} kB', f'>= {limits[name]} kB', value >= limits[name])
    boot = tasks.get('boot_heap')
    if boot:
        value = round(boot['internal_min'] / 1024, 1)
        add('boot internal RAM low point', f"{value} kB at {boot['internal_min_at_ms']} ms",
            f">= {limits['internal_lowest_kb']} kB", value >= limits['internal_lowest_kb'])
    work = sorted(status['responsiveness']['render_work']['us'])
    p50 = round(work[len(work) // 2] / 1000, 1) if work else 0
    # The first two minutes include the boot's image and cover builds (19.4 ms at 50 s after a power cut,
    # 5.9 ms at 3 min, 2026-10-30): like the stall check, judge normal running only.
    booting = status['uptime_ms'] < 120000
    add('render work per frame (median)', f'{p50} ms', f"<= {limits['render_p50_ms']} ms (60 fps)",
        booting or p50 <= limits['render_p50_ms'],
        'not judged in the first 2 min of uptime' if booting else (f'max {round(work[-1] / 1000, 1)} ms' if work else ''))
    stalls = []
    for line in logs['lines']:
        match = STALL.search(line)
        if match and line.split(' ', 1)[0].isdigit() and int(line.split(' ', 1)[0]) > 60000:
            stalls.append(int(match.group(1)))  # after the boot's first minute: normal running
    long = [ms for ms in stalls if ms >= limits['stall_ms']]
    add('main-loop stalls after boot', f'{len(stalls)}, longest {max(stalls, default=0)} ms',
        f"none >= {limits['stall_ms']} ms", not long, 'from the log ring (~200 lines)')
    rssi = status.get('wifi_rssi')
    add('Wi-Fi signal', f'{rssi} dBm', f">= {limits['wifi_rssi_dbm']} dBm",
        rssi is not None and rssi >= limits['wifi_rssi_dbm'])
    gaps = sum('audio_gap' in line for line in logs['lines'])
    add('audio gaps', gaps, 0, gaps == 0, 'audio_gap lines in the log ring')
    return {'uptime_min': status['uptime_ms'] // 60000, 'ok': all(c['ok'] for c in checks), 'checks': checks}


def health_text(result):
    lines = [f"Panel health after {result['uptime_min']} min uptime: {'OK' if result['ok'] else 'ATTENTION'}"]
    for c in result['checks']:
        lines.append(f"{'ok ' if c['ok'] else 'NO '} {c['name']:<32} {str(c['value']):<28} limit {c['limit']}"
                     + (f"  ({c['note']})" if c['note'] and (not c['ok'] or 'not judged' in c['note']) else ''))
    return '\n'.join(lines)


# ---- crashes ----
def crash_addresses(lines):
    """Code addresses of this build's crash, in order. Case-sensitive on purpose: ESPHome prints another
    build's addresses as lowercase pc/btN, which no decoder may use; MTVAL is a data address."""
    found = []
    for line in lines:
        match = CODE_ADDRESS.search(line)
        if match:
            found.append(match.group(1))
    return found


def decode(addresses, elf=ELF):
    tools = sorted(glob.glob(ADDR2LINE))
    if not addresses or not tools or not Path(elf).exists():
        return None
    result = subprocess.run([tools[-1], '-pfiaC', '-e', str(elf), *addresses], capture_output=True, text=True)
    return result.stdout.strip().splitlines() if result.returncode == 0 else None


def elf_matches(status, elf=ELF):
    """True when the local ELF is the build running on the panel (its SHA-256 prefix in /status)."""
    import hashlib
    if not Path(elf).exists():
        return False
    digest = hashlib.sha256(Path(elf).read_bytes()).hexdigest()
    return bool(status.get('firmware_elf_sha256')) and digest.startswith(status['firmware_elf_sha256'])


# ---- redraw map ----
def redraw_overlay(result, screenshot, output):
    from PIL import Image, ImageDraw
    base = Image.open(screenshot).convert('RGB')
    layer = Image.new('RGBA', base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(layer)
    frames = max(1, result['frames'])
    cell, cols = result['cell'], result['cols']
    for index, count in enumerate(result['cells']):
        if not count:
            continue
        share = min(1.0, count / frames)
        x, y = (index % cols) * cell, (index // cols) * cell
        # Blue: drawn now and then; red: drawn in (almost) every frame.
        colour = (int(255 * share), 40, int(255 * (1 - share)), int(60 + 140 * share))
        draw.rectangle([x, y, x + cell - 1, y + cell - 1], fill=colour)
    Image.alpha_composite(base.convert('RGBA'), layer).convert('RGB').save(output, format='PNG')
    os.chmod(output, 0o600)


def redraw_summary(result):
    frames = result['frames']
    seconds = result['window_ms'] / 1000
    screen = result['screen_px']
    hot = sum(1 for c in result['cells'] if frames and c >= 0.9 * frames)
    return {'frames': frames, 'fps': round(frames / seconds, 1) if seconds else None,
            'mean_drawn_screen_pct': round(100 * result['drawn_px'] / (frames * screen), 1) if frames else 0,
            'max_frame_screen_pct': round(100 * max(result['frame_px'], default=0) / screen, 1),
            'cells_drawn_every_frame': hot, 'cells_total': len(result['cells'])}


# ---- tools/panel subcommands (wired in panel_remote.py) ----
COMMANDS = ('logs', 'crash-report', 'tasks', 'clip', 'redraw', 'touches', 'replay', 'settings', 'swipe-tour', 'restart', 'boot-heap', 'memory', 'health')


def add_parsers(commands):
    logs = commands.add_parser('logs', help="The panel's log lines; survives restarts, not power cuts")
    logs.add_argument('--previous', action='store_true', help='the boot before this one')
    logs.add_argument('--older', action='store_true', help='the boot before that (last 8 kB): the first crash of a chain')
    logs.add_argument('--json', action='store_true')
    crash = commands.add_parser('crash-report', help='Why the previous boot ended, decoded to source lines')
    crash.add_argument('--elf', type=Path, default=ELF)
    tasks = commands.add_parser('tasks', help='CPU share per task and core, stack margin, heap; measured on the page shown now (Spelas nu keeps core 1 ~93 %% busy, the dashboard ~13 %%), so open the page under test first')
    tasks.add_argument('--seconds', type=float, default=2.0)
    tasks.add_argument('--json', action='store_true')
    clip = commands.add_parser('clip', help='Short clip of presented frames as JPEGs + MP4 with real timing')
    clip.add_argument('--out', type=Path, required=True)
    clip.add_argument('--seconds', type=float, default=2.0)
    clip.add_argument('--region', type=int, nargs=4, metavar=('X', 'Y', 'W', 'H'), default=(0, 0, 1024, 600))
    clip.add_argument('--scale', type=int, choices=(1, 2), default=2)
    clip.add_argument('--quality', type=int, default=75)
    clip.add_argument('--test-mode', help='start this tools/panel test-mode first, in the same lease (e.g. animation_60)')
    redraw = commands.add_parser('redraw', help='Heat map of where LVGL draws, over a screenshot')
    redraw.add_argument('--out', type=Path, required=True)
    redraw.add_argument('--seconds', type=float, default=3.0)
    redraw.add_argument('--test-mode', help='start this tools/panel test-mode first, in the same lease')
    touches = commands.add_parser('touches', help="The real finger's recent samples (coordinates only)")
    touches.add_argument('--out', type=Path)
    touches.add_argument('--last-seconds', type=float)
    touches.add_argument('--summary', action='store_true',
                         help='gestures plus every warning/error and touch latency the log shows in the same window')
    replay = commands.add_parser('replay', help='Play recorded touch samples back through LVGL')
    replay.add_argument('file', type=Path, help='touches JSON from `tools/panel touches --out`')
    replay.add_argument('--since-ms', type=int)
    replay.add_argument('--until-ms', type=int)
    tour = commands.add_parser('swipe-tour', help='Replayed swipes Gå hemifrån <-> Tavla (never into Natt): '
                               'press-to-frame, render work and frames per page change')
    tour.add_argument('--rounds', type=int, default=1)
    health = commands.add_parser('health', help='Every health sign with its limit: crashes, stacks, RAM, rendering, '
                                 'main-loop stalls, Wi-Fi, audio gaps. Exit 1 when one is over its limit')
    health.add_argument('--json', action='store_true')
    commands.add_parser('memory', help='Internal, DMA and PSRAM: free now, lowest since boot, largest block, boot low point')
    commands.add_parser('restart', help='Clean software restart (like after an OTA); waits for the new boot')
    heap = commands.add_parser('boot-heap', help="This boot's internal/DMA RAM low point (after 40 s)")
    heap.add_argument('--timeline', action='store_true', help='the RAM timeline every 200 ms beside the log events')
    heap.add_argument('--restarts', type=int, default=0, help='restart N times (max 5) and give each boot\'s low point')
    settings = commands.add_parser('settings', help='Read, or set one of: ' + ', '.join(SETTINGS))
    settings.add_argument('key', nargs='?', choices=SETTINGS)
    settings.add_argument('value', nargs='?')


def _text_logs(result, previous, older=False):
    if older:
        block = result.get('older')
        if not block:
            return 'No older boot kept.'
        head = (f"Older boot {block['boot_id']}: started by {reset_reason(block['started_by_reset_reason'])}, "
                f"ended by {reset_reason(block['ended_by_reset_reason'])}; abort: {block['abort']}")
        return '\n'.join([head, *block['lines']])
    if previous:
        block = result.get('previous')
        if not block:
            return 'No previous boot kept (power cut, or the first boot of this firmware).'
        head = (f"Previous boot {block['boot_id']}: started by {reset_reason(block['started_by_reset_reason'])}, "
                f"ended by {reset_reason(block['ended_by_reset_reason'])}")
        return '\n'.join([head, *block['lines']])
    head = f"Boot {result['boot_id']} ({reset_reason(result['reset_reason'])}), {len(result['lines'])} lines"
    if result.get('dropped_lines'):
        head += f", {result['dropped_lines']} dropped while busy"
    return '\n'.join([head, *result['lines']])


def run(client, args):
    """Run one diagnostics subcommand; returns what to print (str) or a JSON-able value."""
    if args.command == 'logs':
        result = client.logs()
        return result if args.json else _text_logs(result, args.previous, args.older)
    if args.command == 'crash-report':
        report = client.crash()
        status = client.status()
        report['reset_reason_name'] = reset_reason(report['reset_reason'])
        addresses = crash_addresses(report.get('esphome_record', []))
        archived = ROOT / '.panel-ci/elf' / f"{status.get('build_id')}.elf"
        if args.elf == ELF and archived.exists():
            args.elf = archived  # the running build's own ELF, kept by flash_panel.sh
        if addresses:
            # The record decodes only against the ELF of the build that crashed; ESPHome marks another build's.
            same = elf_matches(status, args.elf)
            report['decoded'] = decode(addresses, args.elf) if same else None
            if not same:
                report['decode_note'] = 'local ELF is not the running build; keep the crashed build\'s ELF to decode'
        return report
    if args.command == 'tasks':
        result = client.tasks(args.seconds)
        if args.json:
            return result
        samples = max(1, result['samples'])
        rows = [f"{t['name']:<18} {100 * t['core0_samples'] / samples:5.1f}% {100 * t['core1_samples'] / samples:5.1f}%"
                f"  prio {t['priority']:>2}  stack free {t['stack_min_free_bytes']:>6} B  {t['state']}"
                for t in result['tasks']]
        heap = result['heap']
        tail = [f"internal heap: {heap['internal']['free']} B free, min {heap['internal']['minimum_free']}, "
                f"largest block {heap['internal']['largest_free_block']}",
                f"PSRAM: {heap['psram']['free']} B free, min {heap['psram']['minimum_free']}"]
        return '\n'.join([f"{'task':<18} core0  core1   ({result['samples']} samples over {result['window_ms']} ms, "
                          'interrupts count as the task they interrupted)', *rows, *tail])
    if args.command in ('clip', 'redraw') and args.test_mode:
        # Same client, same lease: a separate `tools/panel test-mode` call ends its lease on exit, and the
        # panel restores the page at once (2026-10-29: a redraw "of the orb" measured the idle dashboard).
        client.test_mode(args.test_mode, int(args.seconds * 1000) + 8000)
    if args.command == 'clip':
        header, frames = parse_clip(client.clip(args.seconds, tuple(args.region), args.scale, args.quality))
        return write_clip(header, frames, args.out)
    if args.command == 'redraw':
        folder = private_dir(args.out)
        result = client.redraw(args.seconds)
        private_write(folder / 'redraw.json', json.dumps(result))
        client.screenshot(folder / 'screen.png', lossless=True)
        redraw_overlay(result, folder / 'screen.png', folder / 'redraw.png')
        return {'folder': str(folder), 'overlay': str(folder / 'redraw.png'), **redraw_summary(result)}
    if args.command == 'touches':
        result = client.touches()
        if args.last_seconds is not None:
            since = result['now_ms'] - int(args.last_seconds * 1000)
            result['samples'] = [s for s in result['samples'] if s[0] >= since]
        if args.summary:
            since = result['now_ms'] - int((args.last_seconds or 120) * 1000)
            return touch_summary(result, client.logs(), since)
        if args.out:
            private_write(args.out, json.dumps(result))
            return {'path': str(args.out), 'samples': len(result['samples'])}
        return result
    if args.command == 'replay':
        samples = json.loads(args.file.read_text())['samples']
        script = replay_script(samples, args.since_ms, args.until_ms)
        return client.replay(script)
    if args.command == 'health':
        result = health_report(client.status(), client.tasks(0.5), client.logs(), client.crash())
        return result if args.json else health_text(result)
    if args.command == 'memory':
        return memory(client)
    if args.command == 'restart':
        return client.restart()
    if args.command == 'boot-heap':
        return boot_heap(client, args.timeline, args.restarts)
    if args.command == 'swipe-tour':
        return swipe_tour(client, args.rounds)
    if args.command == 'settings':
        if args.key and args.value is None:
            raise ValueError('Give a value too, e.g. tools/panel settings brightness 80')
        before = client.settings()
        if not args.key:
            return before
        return {'before': before, 'after': client.settings(args.key, args.value)}
    raise ValueError('Unknown diagnostics command')
