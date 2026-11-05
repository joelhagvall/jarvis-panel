#!/usr/bin/env python3
"""The post-install gate chain, as one command for flash_panel.sh and for agents.

  tools/panel gates [--url URL] [--wait-ready 90]  visual, responsiveness, throughput, the Spelas nu record
                                                  (turntable-fps: about 60 a second) and a 2-minute soak
  tools/panel gates --from throughput              rerun from one gate while diagnosing (an install needs all four)
  tools/panel gates --resolve-url [--url URL]      print the debug URL with its host resolved to an IPv4
  tools/panel gates --record-build --device DEV    print the candidate build id; save .panel-ci/last-flash.json

  tools/panel gates --night-flag                   print --restore-night when the panel is on Natt now
  tools/panel gates --any-build                    skip the build check (a panel flashed without flash_panel.sh)

Before the gates, the panel must run the build .panel-ci/last-flash.json names. Right after an upload the old
firmware answers for a few seconds, ready and on a day page, so visual started on it and failed with HTTP 423
when the reboot dropped its lease (twice on 2026-10-28). And a candidate that crashes before ESPHome confirms
it (60 s) is rolled back: the gates then passed on the old firmware (2026-10-28, reset reason 4, Abort).

Night mode is no reason to postpone the gates. A panel on page_natt is woken with the same full-screen
tap a finger uses and put back with the real Natt tab afterwards, also after a failed gate. It does
not return by itself: the gates use up its 120 s idle trigger (2026-10-26). Pass --restore-night when
an upload restarted a panel that was on Natt. A panel left on Spelas nu (full screen, where the test modes
answer 409) is swiped to Hemmet first. The last output line is always "GATES OK" or
"GATES FAILED at <gate> (exit N)".
"""
import argparse
from datetime import datetime
import ipaddress
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_URL = os.environ.get('HALLPANEL_DEBUG_URL', 'http://hallpanel.local:8080')
DAY_PAGES = ('page_glass_v2', 'page_hemmet')


def gate_commands(url, wait_ready):
    wait = str(wait_ready)
    # The music row's cover first, while the boot's own music state is fresh (tools/music_cover.py).
    return [('music', ['music-cover', '--url', url]),
            ('visual', ['visual', 'check', '--url', url, '--wait-ready', wait]),
            ('responsiveness', ['responsiveness', '--url', url, '--wait-ready', wait]),
            ('throughput', ['throughput', '--url', url, '--require-target']),
            # Spelas nu's record at about 60 a second, in the synthetic turntable mode (tools/turntable_fps.py).
            ('turntable', ['turntable-fps', '--url', url]),
            ('soak', ['throughput', '--url', url, '--seconds', '120', '--require-soak'])]


def resolve_url(url, resolver=socket.getaddrinfo):
    """Resolve the host once, before an upload: mDNS often fails for a while right after one."""
    parts = urlsplit(url)
    host = parts.hostname
    if not host:
        raise ValueError('URL needs a host')
    try:
        ipaddress.ip_address(host)
        return url
    except ValueError:
        pass
    address = resolver(host, parts.port or 80, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
    netloc = address + (f':{parts.port}' if parts.port else '')
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))


def wake_target(ui):
    """Natt's real exit control: the deepest clickable full-screen object."""
    targets = [w for w in ui['widgets'] if w.get('type') == 'object' and w.get('clickable')
               and (w['x'], w['y'], w['width'], w['height']) == (0, 0, 1024, 600)]
    if not targets:
        raise RuntimeError('Night-mode exit control unavailable')
    return max(targets, key=lambda w: w['path'].count('/'))['path']


def leave_night(client, wait_ready, clock=time.monotonic, sleep=time.sleep):
    """Return True when the panel was on Natt and a real tap brought it back to a day page."""
    deadline = clock() + wait_ready
    while True:
        try:
            page = client.status()['page']
            break
        except OSError:
            # Only reads repeat while the panel restarts; input is never retried.
            if clock() >= deadline:
                raise
            sleep(1)
    if page != 'page_natt':
        return False
    client.tap_id(wake_target(client.ui()))
    until = clock() + 3
    while clock() < until:
        if client.status()['page'] in DAY_PAGES:
            return True
        sleep(0.1)
    raise RuntimeError('Night-mode exit tap did not reach a day page')


def night_tab(ui):
    """The Natt tab button: the clickable parent of the top-layer label 'Natt'."""
    label = next((w for w in ui['widgets'] if w.get('text') == 'Natt' and w['path'].startswith('top/')), None)
    if label is None:
        raise RuntimeError('Natt tab not visible')
    parent = label['path'].rsplit('/', 1)[0]
    if not any(w['path'] == parent and w.get('clickable') for w in ui['widgets']):
        raise RuntimeError('Natt tab not clickable')
    return parent


def leave_music(client, clock=time.monotonic, sleep=time.sleep):
    """Return True when the panel was on Spelas nu and a swipe to the right brought it to Hemmet. The panel's
    test modes run only from a day page or Tavla and answer 409 elsewhere: a panel left on Spelas nu failed
    throughput twice with "HTTP Error 409: Conflict" (2026-10-28). Spelas nu is full screen without the tab bar,
    so it is left as panel_ui_test.bar_page does, with the swipe a hand makes."""
    if client.status()['page'] != 'page_musik':
        return False
    client.swipe(220, 300, 820, 300, 350)
    until = clock() + 3
    while clock() < until:
        if client.status()['page'] in DAY_PAGES:
            return True
        sleep(0.1)
    raise RuntimeError('The swipe did not leave Spelas nu')


def restore_night(client, clock=time.monotonic, sleep=time.sleep):
    page = client.status()['page']
    if page == 'page_natt':
        return
    if page not in DAY_PAGES:
        raise RuntimeError(f'cannot reach the Natt tab from {page}')
    client.tap_id(night_tab(client.ui()))
    until = clock() + 3
    while clock() < until:
        if client.status()['page'] == 'page_natt':
            return
        sleep(0.1)
    raise RuntimeError('the Natt tab did not open page_natt')


def wait_build(url, expected, seconds, client_factory, clock=time.monotonic, sleep=time.sleep):
    """Wait until the panel runs build `expected`; return (ok, running build, reset reason)."""
    deadline = clock() + seconds
    running = reason = None
    while True:
        try:
            with client_factory(url) as client:
                status = client.status()
            running, reason = status.get('build_id'), status.get('reset_reason')
            if running == expected:
                return True, running, reason
        except OSError:
            pass   # Only reads repeat while the panel restarts.
        if clock() >= deadline:
            return False, running, reason
        sleep(1)


def flashed_build(state=ROOT / '.panel-ci'):
    try:
        return json.loads((state / 'last-flash.json').read_text()).get('build_id')
    except (OSError, ValueError):
        return None


def run_gates(url, wait_ready, client_factory, runner, out=sys.stdout, start=None, night=False, expect_build=None,
              clock=time.monotonic, sleep=time.sleep):
    """Run the gates in order; return the first failing exit code. night: put the panel back on Natt.
    expect_build: wait for the candidate before starting and verify it after every gate, failing at "build"
    if it rolls back or stops answering."""
    say = lambda line: print(line, file=out, flush=True)
    if expect_build:
        ok, running, reason = wait_build(url, expect_build, wait_ready, client_factory, clock, sleep)
        if not ok:
            say(f'GATE build: the panel runs {running}, not the flashed {expect_build} (reset reason {reason}; '
                '4 = panic: the candidate probably crashed and was rolled back; read the crash with '
                'esphome logs, see docs/restart-crash.md)')
            say('GATES FAILED at build (exit 3)')
            return 3
        say(f'GATE build: ok ({running})')
    gates = gate_commands(url, wait_ready)
    if start is not None:
        names = [name for name, _ in gates]
        if start not in names:
            raise ValueError(f'Unknown gate {start}; choose one of {", ".join(names)}')
        gates = gates[names.index(start):]
    failed, code, note, state = None, 0, '', {'night': night}
    try:
        for name, args in gates:
            with client_factory(url) as client:
                if leave_night(client, wait_ready):
                    state['night'] = True
                    say(f'GATE {name}: left night mode with a real tap')
                if leave_music(client, clock, sleep):
                    say(f'GATE {name}: left Spelas nu with a swipe')
            say(f'GATE {name}: running')
            code = runner(args)
            if expect_build:
                same,running,reason=wait_build(url,expect_build,0,client_factory,clock,sleep)
                if not same:
                    failed,code='build',3
                    say(f'GATE build: after {name}, panel runs {running}, expected {expect_build} (reset reason {reason})')
                    break
            if code:
                failed = name
                try:
                    with client_factory(url) as client:
                        if client.status()['page'] == 'page_natt':
                            note = '; the panel entered night mode during this gate'
                except OSError:
                    note = '; the panel did not answer afterwards'
                break
            say(f'GATE {name}: ok')
    finally:
        # Also after a failed gate or an error: never leave the hallway lit at night.
        if state['night']:
            try:
                with client_factory(url) as client:
                    restore_night(client)
                say('Night mode restored with the Natt tab.')
            except (OSError, RuntimeError, KeyError) as error:
                say(f'WARNING: night mode not restored ({error}); tap Natt on the panel.')
    if failed:
        if failed != 'visual':
            say('A *_roundtrip_ms failure is host latency: run tools/panel host-link alone, '
                'never beside a gate, before suspecting the firmware.')
            # 2026-10-31: throughput stopped on "Benchmark preempted or activity unknown: voice_active" while Joel
            # spoke to Jarvis after a flash; the rerun passed every gate.
            say('A "preempted ... voice_active" (or other activity) failure is someone using the panel, not the '
                'firmware: run tools/panel gates again when it is quiet.')
        say(f'GATES FAILED at {failed} (exit {code}){note}')
        return code
    say('GATES OK' if start is None else f'GATES OK from {start} (a partial run, not an install verification)')
    return 0


def night_flag(url, client_factory):
    """--restore-night when the panel is on Natt now; nothing when it is not or cannot be read."""
    try:
        with client_factory(url) as client:
            return '--restore-night' if client.status()['page'] == 'page_natt' else ''
    except (OSError, KeyError, ValueError):
        return ''


def record_build(device, state=ROOT / '.panel-ci'):
    sys.path.insert(0, str(ROOT / 'tools'))
    from panel_ci import inventory
    candidate = inventory()
    record = {'recorded_at': datetime.now().astimezone().isoformat(), 'device': device,
              'build_id': candidate['build_id'], 'head': candidate['head'],
              'branch': candidate['branch'], 'uncommitted': candidate['changes']}
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    path = state / 'last-flash.json'
    temporary = path.with_suffix('.tmp')
    with os.fdopen(os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as handle:
        json.dump(record, handle, indent=2)
    os.replace(temporary, path)
    changes = candidate['changes']
    summary = f'Building {candidate["build_id"]} from {candidate["head"][:7]}'
    if changes:
        summary += f' plus {len(changes)} uncommitted file(s), possibly from another session:'
    print(summary, file=sys.stderr)
    for line in changes[:30]:
        print('  ' + line, file=sys.stderr)
    if len(changes) > 30:
        print(f'  ... {len(changes) - 30} more in {path}', file=sys.stderr)
    return candidate['build_id']


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--url', default=DEFAULT_URL)
    parser.add_argument('--wait-ready', type=int, default=90)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--resolve-url', action='store_true')
    mode.add_argument('--record-build', action='store_true')
    mode.add_argument('--night-flag', action='store_true')
    parser.add_argument('--device', default='hallpanel.local')
    parser.add_argument('--from', dest='start', choices=('music', 'visual', 'responsiveness', 'throughput', 'turntable', 'soak'))
    parser.add_argument('--restore-night', action='store_true', help='put the panel on Natt after the gates')
    parser.add_argument('--any-build', action='store_true', help='skip the check for the last flashed build')
    args = parser.parse_args(argv)
    if args.resolve_url:
        try:
            print(resolve_url(args.url))
        except (OSError, ValueError) as error:
            # Keep the hostname; the gates then wait for it as before.
            print(f'Could not resolve {args.url} ({error}); keeping the hostname', file=sys.stderr)
            print(args.url)
        return 0
    if args.record_build:
        print(record_build(args.device))
        return 0
    from panel_remote import PanelClient
    if args.night_flag:
        print(night_flag(args.url, lambda url: PanelClient(url, persistent=True)))
        return 0
    panel = str(ROOT / 'tools/panel')
    runner = lambda gate_args: subprocess.call([panel, *gate_args])
    try:
        return run_gates(args.url, args.wait_ready, lambda url: PanelClient(url, persistent=True), runner,
                         start=args.start, night=args.restore_night,
                         expect_build=None if args.any_build else flashed_build())
    except (OSError, RuntimeError, KeyError, ValueError) as error:
        print(f'GATES FAILED at night-check ({error})', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
