"""Wall-clock boot of a software restart, including the part before the panel's millis() starts.

Every boot_*_ms in /status counts from millis() 0, which is ~1.3 s after reset (bootloader, image check,
XIP copy to PSRAM). This restarts the panel, pings it every 100 ms and reports, per boot:
shutdown_ms (restart request -> last ping before the reset), reset_to_millis0_ms (first ping after the
reset minus boot_wifi_ip_ms, minus shutdown) and the boot phases from the log ring. Resolution is the
100 ms ping interval. `--power` waits for a power cut by hand instead and reports the panel's own milestones
(reset_reason 1) and the ping outage. Each run restarts the panel: hold the device lock
(`tools/panel lock --lock device run -- tools/panel boot-wall 3`). Setup phases drift ~50 ms and downloads by
seconds over an evening, so compare a candidate with a control measured in the same session, never with an
older run: `--label old --out ab.jsonl` per round, then `--summarize ab.jsonl`.
See docs/baseline/wifi-boot-2026-10-26.md.
"""
import argparse
import json
import statistics
import subprocess
import sys
import time
from pathlib import Path

from panel_remote import PanelClient

PANEL = Path(__file__).resolve().parent / 'panel'
FIELDS = ('boot_ui_ms', 'boot_wifi_setup_ms', 'boot_wifi_ip_ms', 'boot_wifi_ms', 'boot_bg_ms', 'boot_shown_ms',
          'boot_ready_ms', 'reset_reason')


def one_boot(power=False):
    client = PanelClient()
    ip = client.url.split('//')[1].split(':')[0]
    before = client.status()['boot_id']
    ping = subprocess.Popen(['ping', '-i', '0.1', '-W', '100', ip], stdout=subprocess.PIPE, text=True, bufsize=1)
    try:
        if power:
            # Joel pulls the power and plugs it back in: the time before the cut is unknown, so only the
            # panel's own milestones and the outage as seen by ping are reported.
            print('Pull the panel\'s power now, wait 5 s, plug it back in, then wait for this run\'s result.',
                  file=sys.stderr, flush=True)
        else:
            client.request('/restart', {'reason': 'diagnostics'})
        client.close()
        sent = time.monotonic()
        last, last_before, first = sent, None, None
        deadline = sent + (300 if power else 60)
        for line in ping.stdout:
            now = time.monotonic()
            if now > deadline:
                break
            if 'bytes from' not in line:
                continue
            if last_before is None and now - last > 1.0:
                last_before = last
            last = now
            if last_before is not None:
                first = now
                break
    finally:
        ping.terminate()
    if first is None:
        raise RuntimeError('No ping gap and reply within the wait')
    time.sleep(25)   # let the boot settle before /status; rapid polling during boot is avoided on purpose
    status = None
    for _ in range(20):
        try:
            status = PanelClient().status()
            break
        except Exception:
            time.sleep(2)
    if not status or status['boot_id'] == before:
        raise RuntimeError('The panel did not report a new boot')
    # A second power cut during the wait above gives a boot read half-way (2026-10-30: boot_bg_ms 0 at 5 s).
    if status['uptime_ms'] < 20000:
        raise RuntimeError(f"Status read {status['uptime_ms']} ms into a newer boot; cut the power once per run")
    row = {key: status.get(key) for key in FIELDS}
    row['uptime_ms'] = status['uptime_ms']
    if power:
        row['ping_gap_ms'] = round((first - last_before) * 1000)
    else:
        row['shutdown_ms'] = round((last_before - sent) * 1000)
        row['reset_to_millis0_ms'] = round((first - last_before) * 1000) - status['boot_wifi_ip_ms']
    logs = subprocess.run([str(PANEL), 'logs'], capture_output=True, text=True).stdout
    row['phases'] = [line.split(']: ')[-1] for line in logs.splitlines()
                     if 'Phase ' in line or 'Running through setup' in line]
    return row


SUMMARY = ('reset_to_millis0_ms', 'boot_ui_ms', 'boot_wifi_ip_ms', 'boot_bg_ms', 'boot_shown_ms', 'boot_ready_ms')


def summarize(path):
    """Median (min-max) per label of a JSONL file written with --label/--out, for an interleaved A/B."""
    rows = {}
    for line in Path(path).read_text().splitlines():
        if line.startswith('{'):
            row = json.loads(line)
            rows.setdefault(row.get('label', '-'), []).append(row)
    for label, group in rows.items():
        print(f'{label}: {len(group)} boots')
        for key in SUMMARY:
            values = sorted(r[key] for r in group if r.get(key) is not None)
            if values:
                print(f'  {key:<22} median {statistics.median(values):>8} ({values[0]}-{values[-1]})')
        shown = sorted(r['reset_to_millis0_ms'] + r['boot_shown_ms'] for r in group if 'reset_to_millis0_ms' in r)
        if shown:
            print(f"  {'reset -> shown':<22} median {statistics.median(shown):>8} ({shown[0]}-{shown[-1]})")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('runs', nargs='?', type=int, default=3)
    parser.add_argument('--power', action='store_true',
                        help='no software restart: wait (up to 300 s) for a power cut by hand and the boot after it')
    parser.add_argument('--label', help='tag each row, e.g. old/new for an interleaved A/B')
    parser.add_argument('--out', help='also append each row to this JSONL file')
    parser.add_argument('--summarize', metavar='FILE', help='only print median (min-max) per label of FILE')
    args = parser.parse_args()
    if args.summarize:
        return summarize(args.summarize)
    for n in range(args.runs):
        if n:
            time.sleep(10)
        row = {**({'label': args.label} if args.label else {}), **one_boot(args.power)}
        print(json.dumps(row), flush=True)
        if args.out:
            with open(args.out, 'a') as f:
                f.write(json.dumps(row) + '\n')


if __name__ == '__main__':
    main()
