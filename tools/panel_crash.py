#!/usr/bin/env python3
"""Catch a firmware that crashes while it is installed, and say where.

  tools/panel crash capture --device HOST --out FILE   record esphome logs until stopped (SIGTERM), reconnecting
  tools/panel crash scan FILE [--elf ELF]              exit 1 when the capture shows a new crash or an OTA rollback
  tools/panel crash serial --port /dev/cu.usbmodemN --out FILE   record the USB console until stopped (SIGTERM)

The network log never shows a panic's own text ("assert failed: <function> <file>:<line> (<expression>)"): ESP-IDF
prints it only on the console, the USB Serial/JTAG port. With the panel on USB, flash_panel.sh records that too
(CRASH_SERIAL=/dev/cu.usbmodemN) and scan prints the assert and panic lines it finds there.

flash_panel.sh runs capture from the end of the upload through the gates, then scan. On 2026-10-28 a
candidate aborted within a minute, ESPHome rolled the panel back and nothing recorded why: the crash is
only printed by the next boot, when a log client connects (`CRASH DETECTED ON PREVIOUS BOOT`, the reason,
`pc` and `btN` addresses, `OTA rollback detected!`).

A capture is a list of connections, split by `--- disconnected` lines. The report in the first one is
about the boot before the candidate (the old firmware's restart into it) and only noted. A report with a
signature (reason, pc, backtrace) not seen before, or a rollback, after that is the candidate's crash:
scan prints it, decodes the addresses with the build's ELF when the RISC-V addr2line is installed, copies
the ELF next to the capture (the next compile replaces it) and exits 1.
"""
import argparse
import glob
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ELF = ROOT / 'styles/glass-v2/.esphome/build/hallpanel/build/firmware.elf'
ADDR2LINE = str(Path.home() / 'Library/Caches/esphome/idf/tools/riscv32-esp-elf/*/riscv32-esp-elf/bin/riscv32-esp-elf-addr2line')
MARK = '--- disconnected'
KEEP = 20


def capture(device, out, esphome=ROOT / '.venv/bin/esphome', cwd=ROOT / 'styles/glass-v2'):
    """Append timestamped log lines to out until SIGTERM, reconnecting after each disconnect."""
    out = Path(out)
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    for old in sorted(out.parent.glob('*.log'))[:-KEEP]:
        old.unlink()
    stop, child = [False], [None]

    def terminate(*_):
        stop[0] = True
        if child[0] and child[0].poll() is None:
            child[0].terminate()
    signal.signal(signal.SIGTERM, terminate)
    signal.signal(signal.SIGINT, terminate)
    with open(out, 'a', buffering=1) as log:
        while not stop[0]:
            child[0] = subprocess.Popen([str(esphome), 'logs', 'panel.yaml', '--device', device], cwd=cwd,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, errors='replace')
            for line in child[0].stdout:
                log.write(time.strftime('%H:%M:%S ') + line)
            child[0].wait()
            log.write(time.strftime('%H:%M:%S ') + MARK + '\n')
            if not stop[0]:
                time.sleep(2)
    return 0


CRASH = re.compile(r'CRASH DETECTED ON PREVIOUS BOOT')
PANIC_LINE = re.compile(r'assert failed|Guru Meditation|abort\(\) was called|Backtrace:|MEPC|panic', re.I)


def capture_serial(port, out, baud=115200):
    """The USB console, timestamped, until SIGTERM. DTR and RTS stay high, as macOS raises them on open: with
    both set low (pyserial's usual "no reset") the open reset the panel (2026-10-28, uptime 81 s -> 6 s), which
    during a candidate's first minute would look like a failed boot and roll it back."""
    import serial
    out = Path(out)
    out.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    stop = [False]
    signal.signal(signal.SIGTERM, lambda *_: stop.__setitem__(0, True))
    signal.signal(signal.SIGINT, lambda *_: stop.__setitem__(0, True))
    with open(out, 'a', buffering=1) as log:
        while not stop[0]:
            try:
                link = serial.Serial()
                link.port, link.baudrate, link.timeout = port, baud, 0.5
                link.dtr = link.rts = True              # as macOS sets them on open: no edge, so no reset
                link.open()
                with link:
                    pending = b''
                    while not stop[0]:
                        pending += link.read(4096)
                        *lines, pending = pending.split(b'\n')
                        for line in lines:
                            log.write(time.strftime('%H:%M:%S ') + line.decode(errors='replace').rstrip('\r') + '\n')
            except (OSError, serial.SerialException) as err:
                log.write(time.strftime('%H:%M:%S ') + f'--- serial unavailable ({err})\n')
                time.sleep(1)          # the port vanishes while the panel reboots
    return 0


def serial_panics(path):
    """The assert/panic lines (with the line after each, which carries the cause) from a console capture."""
    try:
        lines = Path(path).read_text(errors='replace').splitlines()
    except OSError:
        return []
    found = []
    for i, line in enumerate(lines):
        if PANIC_LINE.search(line):
            found += [l for l in lines[i:i + 2] if l not in found]
    return found[:40]
FIELD = re.compile(r'\]:\s+(Reason|pc|bt\d+|Crashed core):\s+(.+?)\s*$')
ROLLBACK = re.compile(r'OTA rollback detected!.*')
# The boot's own reset reason, published at boot. The crash report alone is not enough: ESPHome keeps the last
# one and prints it at every boot, so a candidate that crashed exactly like an earlier build looked old
# (2026-10-28 10:32, the same Abort twice).
RESET = re.compile(r"'Senaste omstart' >> '([^']*)'")
CRASHED = ("panic", "watchdog", "brownout")


def reports(text):
    """Per connection: (crash reports as dicts, rollback lines)."""
    connections, current = [], {'crashes': [], 'rollbacks': [], 'resets': []}
    crash = None
    connected = False
    for line in text.splitlines():
        if re.search(r'\bINFO Successfully connected to [^\n]+ @ ',line):
            if connected:
                connections.append(current)
                current, crash = {'crashes': [], 'rollbacks': [], 'resets': []}, None
            connected = True
        if line.rstrip().endswith(MARK):
            connections.append(current)
            current, crash = {'crashes': [], 'rollbacks': [], 'resets': []}, None
            connected = False
            continue
        if reset := RESET.search(line):
            current['resets'].append(reset.group(1))
        if CRASH.search(line):
            crash = {'reason': None, 'core': None, 'pc': None, 'bt': []}
            current['crashes'].append(crash)
            continue
        found = FIELD.search(line)
        if crash is not None and found:
            key, value = found.groups()
            if key == 'Reason': crash['reason'] = value
            elif key == 'Crashed core': crash['core'] = value
            elif key == 'pc': crash['pc'] = value
            else: crash['bt'].append(value)
            continue
        if crash is not None and '[esp32.crash' not in line:
            crash = None
        rolled = ROLLBACK.search(line)
        if rolled:
            current['rollbacks'].append(rolled.group(0))
    connections.append(current)
    return connections


def signature(crash):
    return (crash['reason'], crash['pc'], tuple(crash['bt']))


def new_crashes(connections):
    """Crash reports and rollbacks that belong to the candidate: anything after the first connection that the
    first connection did not already report."""
    seen = {signature(c) for c in connections[0]['crashes']} if connections else set()
    crashes, rollbacks = [], []
    for connection in connections[1:]:
        rebooted_by_crash = any(any(word in r.lower() for word in CRASHED) for r in connection['resets'])
        for crash in connection['crashes']:
            if signature(crash) not in seen or rebooted_by_crash:
                seen.add(signature(crash))
                if crash not in crashes:
                    crashes.append(crash)
        if rebooted_by_crash and not connection['crashes']:
            crashes.append({'reason': f"reset: {connection['resets'][0]}", 'core': None, 'pc': None, 'bt': []})
        rollbacks += connection['rollbacks']
    return crashes, rollbacks


def code_ranges(elf, nm=None):
    """(start, end) of every function in the ELF, from nm's sizes; [] when nm is missing."""
    tool = nm or next(iter(sorted(glob.glob(ADDR2LINE.replace('addr2line', 'nm')))), None)
    if not tool:
        return []
    out = subprocess.run([tool, '-S', '--defined-only', str(elf)], capture_output=True, text=True).stdout
    ranges = []
    for line in out.splitlines():
        parts = line.split()
        if len(parts) >= 4 and parts[2] in 'tTwW':
            start = int(parts[0], 16)
            ranges.append((start, start + int(parts[1], 16)))
    return ranges


def decode(crash, elf, addr2line=None, ranges=None):
    """addr2line's lines; an address outside every function is marked: a backtrace from stack scanning picks up
    data words too (2026-10-28: most of the Abort's bt words were not code)."""
    tool = addr2line or next(iter(sorted(glob.glob(ADDR2LINE))), None)
    addresses = [a for a in [crash['pc'], *crash['bt']] if a]
    if not tool or not Path(elf).is_file() or not addresses:
        return None
    result = subprocess.run([tool, '-pfiaC', '-e', str(elf), *addresses], capture_output=True, text=True)
    if result.returncode != 0:
        return None
    ranges = code_ranges(elf) if ranges is None else ranges
    lines = []
    for address, line in zip(addresses, result.stdout.replace(str(Path.home()) + '/', '~/').splitlines()):
        value = int(address, 16)
        if ranges and '??' in line and not any(start <= value < end for start, end in ranges):
            line += '   (not code in this ELF: stack noise)'
        lines.append(line)
    return '\n'.join(lines)


def running_build(url):
    """(build_id, reset_reason) the panel reports now, or (None, None)."""
    sys.path.insert(0, str(ROOT / 'tools'))
    from panel_remote import PanelClient
    try:
        with PanelClient(url, persistent=True) as client:
            status = client.status()
        return status.get('build_id'), status.get('reset_reason')
    except (OSError, ValueError, KeyError):
        return None, None


def scan(path, elf=ELF, out=sys.stdout, addr2line=None, build=None, running=None):
    """running: (build_id, reset_reason) the panel reports after the gates; another build than `build` is a
    rollback even when the log missed the reboot (the rolled-back boot printed its reason before reconnecting)."""
    path = Path(path)
    if not path.is_file():
        print(f'Crash scan: no capture at {path} (USB install or capture failed); nothing checked', file=out)
        return 0
    connections = reports(path.read_text(errors='replace'))
    earlier = connections[0]['crashes'] if connections else []
    for crash in earlier:
        print(f'Crash scan: the boot before the candidate had crashed ({crash["reason"]}, pc {crash["pc"]}); '
              'not the candidate', file=out)
    crashes, rollbacks = new_crashes(connections)
    console = serial_panics(path.with_suffix('.serial.log'))
    for line in console:
        print(f'CONSOLE: {line}', file=out)
    if build and running and running[0] and running[0] != build:
        rollbacks.append(f"the panel runs {running[0]}, not the candidate {build} (reset reason {running[1]})")
        if not crashes and str(running[1]) == "4":
            crashes = [dict(c) for c in connections[-1]['crashes'][-1:]] or [
                {'reason': 'panic (reset reason 4)', 'core': None, 'pc': None, 'bt': []}]
    if not crashes and not rollbacks:
        print(f'Crash scan: no crash or rollback while installing ({len(connections)} connection(s), {path})', file=out)
        return 0
    for crash in crashes:
        print(f'CANDIDATE CRASHED: {crash["reason"]} on core {crash["core"]}, pc {crash["pc"]}', file=out)
        decoded = decode(crash, elf, addr2line)
        print(decoded.rstrip() if decoded else '  (addresses not decoded: addr2line or the ELF is missing)', file=out)
    for line in rollbacks:
        print(f'CANDIDATE ROLLED BACK: {line}', file=out)
    if Path(elf).is_file():
        kept = path.with_suffix('.elf')
        shutil.copyfile(elf, kept)
        os.chmod(kept, 0o600)
        print(f'The candidate ELF is kept at {kept}; the capture is {path}.', file=out)
    return 1


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest='command', required=True)
    console = commands.add_parser('serial')
    console.add_argument('--port', required=True)
    console.add_argument('--out', type=Path, required=True)
    record = commands.add_parser('capture')
    record.add_argument('--device', required=True)
    record.add_argument('--out', type=Path, required=True)
    check = commands.add_parser('scan')
    check.add_argument('path', type=Path)
    check.add_argument('--elf', type=Path, default=ELF)
    check.add_argument('--build', help='the candidate build id; with --url, a panel on another build is a rollback')
    check.add_argument('--url')
    args = parser.parse_args(argv)
    if args.command == 'capture':
        return capture(args.device, args.out)
    if args.command == 'serial':
        return capture_serial(args.port, args.out)
    running = running_build(args.url) if args.url and args.build else None
    return scan(args.path, args.elf, build=args.build, running=running)


if __name__ == '__main__':
    raise SystemExit(main())
