#!/usr/bin/env python3
"""Read-only Mac -> panel link check before host-latency gates (about 9 s).

Reports whether the Mac reaches the panel over Wi-Fi or a wired port, the Mac's Wi-Fi
band/channel, the panel's RSSI and a short ping to the panel and, at the same time, the router.
It sends no input, takes no lease and restarts nothing. Network names, BSSIDs and addresses
other than the panel IP are never printed. The panel's ESP32-C6 radio is 2.4 GHz only, so a Mac on 2.4 GHz shares its airtime.
"""
import argparse
import ipaddress
import json
from pathlib import Path
import re
import socket
import subprocess
import sys
import urllib.parse

from panel_metrics import percentile

PING_COUNT = 20
PING_INTERVAL_S = 0.2
# Diagnostic thresholds only, not release gates. On 2026-10-26 a clear 5 GHz link measured
# 5.7/13 ms ping avg/max; the same Mac on the panel's 2.4 GHz channel measured 90/245 ms.
PING_P95_WARN_MS = 50
PING_LOSS_WARN_PERCENT = 5
RSSI_WARN_DBM = -75
FIX = 'switch the Mac to 5 GHz or Ethernet before latency gates'
BANDS = {'2': '2.4 GHz', '5': '5 GHz', '6': '6 GHz'}


def parse_channel(text):
    """'6 (2GHz, 20MHz)' -> channel, band, width. Unknown text -> None."""
    match = re.match(r'\s*(\d+)\s*\((\d+)GHz(?:,\s*(\d+)MHz)?\)', str(text or ''))
    if not match:
        return None
    return {'channel': int(match[1]), 'band': BANDS.get(match[2], match[2] + ' GHz'),
            'width_mhz': int(match[3]) if match[3] else None}


def mac_wifi(profile):
    """Allow-listed Wi-Fi facts from `system_profiler SPAirPortDataType -json`. Never copies names."""
    interfaces = [i for entry in (profile or {}).get('SPAirPortDataType', [])
                  for i in entry.get('spairport_airport_interfaces', []) if isinstance(i, dict)]
    # awdl0/llw0 are AirDrop/low-latency helpers, not the station link.
    candidates = [i for i in interfaces if not str(i.get('_name', '')).startswith(('awdl', 'llw'))]
    if not candidates:
        return None
    current = next((i for i in candidates if i.get('spairport_status_information') == 'spairport_status_connected'),
                   candidates[0])
    info = current.get('spairport_current_network_information') or {}
    result = {'interface': current.get('_name'),
              'connected': current.get('spairport_status_information') == 'spairport_status_connected',
              'band': None, 'channel': None, 'width_mhz': None, 'phy_mode': info.get('spairport_network_phymode'),
              'signal_dbm': None, 'noise_dbm': None}
    channel = parse_channel(info.get('spairport_network_channel'))
    if result['connected'] and channel:
        result.update(channel)
    levels = re.findall(r'(-?\d+)\s*dBm', str(info.get('spairport_signal_noise') or ''))
    if len(levels) == 2:
        result['signal_dbm'], result['noise_dbm'] = int(levels[0]), int(levels[1])
    return result


def parse_route_interface(text):
    match = re.search(r'^\s*interface:\s*(\S+)', text or '', re.M)
    return match[1] if match else None


def parse_route_gateway(text):
    match = re.search(r'^\s*gateway:\s*(\S+)', text or '', re.M)
    try:
        return str(ipaddress.IPv4Address(match[1])) if match else None
    except ValueError:
        return None


def mac_link(wifi, route_interface):
    """Which medium carries Mac -> panel traffic: wifi, wired (any other interface) or unknown."""
    if route_interface is None:
        return {'medium': 'unknown', 'interface': None, 'wifi': wifi}
    if wifi and route_interface == wifi['interface']:
        return {'medium': 'wifi', 'interface': route_interface, 'wifi': wifi}
    return {'medium': 'wired', 'interface': route_interface, 'wifi': wifi}


def parse_ping(text):
    times = [float(v) for v in re.findall(r'time[=<]([\d.]+)\s*ms', text or '')]
    counts = re.search(r'(\d+) packets transmitted, (\d+) (?:packets )?received', text or '')
    if not counts:
        return None
    sent, received = int(counts[1]), int(counts[2])
    result = {'sent': sent, 'received': received,
              'loss_percent': round(100 * (sent - received) / sent, 1) if sent else None,
              'min_ms': None, 'avg_ms': None, 'p50_ms': None, 'p95_ms': None, 'max_ms': None}
    if times:
        result.update(min_ms=min(times), avg_ms=round(sum(times) / len(times), 2), p50_ms=percentile(times, .5),
                      p95_ms=percentile(times, .95), max_ms=max(times))
    return result


def panel_facts(status):
    """Read-only Wi-Fi fields of /status: RSSI and the channel (boot_wifi_channel, 0 when unknown)."""
    # The panel reports the channel it associated on at boot; it stays on that AP's channel.
    channel = status.get('wifi_channel', status.get('boot_wifi_channel'))
    return {'wifi_connected': status.get('wifi_connected'), 'rssi_dbm': status.get('wifi_rssi'),
            'rssi_age_ms': status.get('wifi_rssi_age_ms'),
            'channel': channel if type(channel) is int and channel > 0 else None}


def slow(ping):
    return ping['p95_ms'] > PING_P95_WARN_MS or ping['loss_percent'] > PING_LOSS_WARN_PERCENT


def assess(link, panel, ping, gateway_ping=None):
    """Plain-English actions; an empty list means the host link is fit for latency gates."""
    warnings, notes = [], []
    wifi = link.get('wifi') or {}
    panel_channel = (panel or {}).get('channel')
    if panel is not None and panel_channel is None:
        notes.append('Panel /status does not report its Wi-Fi channel; the panel is 2.4 GHz only (ESP32-C6)')
    if link['medium'] == 'unknown':
        notes.append('Could not determine which Mac interface reaches the panel')
    # A wired route makes the Mac's own Wi-Fi channel irrelevant; an unknown route does not.
    if link['medium'] != 'wired' and wifi.get('band') == '2.4 GHz':
        channel = wifi.get('channel')
        if panel_channel is None:
            warnings.append(f'Mac is on 2.4 GHz channel {channel}, the only band the panel can use, so they likely '
                            f'share airtime; {FIX}')
        elif abs(panel_channel - channel) < 5:  # 20 MHz 2.4 GHz channels overlap unless five apart.
            shares = 'shares' if panel_channel == channel else 'overlaps'
            warnings.append(f"Mac {shares} the panel's 2.4 GHz channel {panel_channel}"
                            f"{'' if panel_channel == channel else f' (Mac on {channel})'}; {FIX}")
    if link['medium'] == 'wifi' and wifi and not wifi.get('band'):
        notes.append('Mac Wi-Fi band/channel unavailable')
    if panel is None:
        warnings.append('Panel /status unreachable; check the panel before latency gates')
    elif type(panel.get('rssi_dbm')) is int and panel['rssi_dbm'] < RSSI_WARN_DBM:
        warnings.append(f"Panel RSSI {panel['rssi_dbm']} dBm is weak (below {RSSI_WARN_DBM}); host latency "
                        'will be noisy, check the panel\'s access point')
    if ping is None or not ping['received']:
        warnings.append('Mac -> panel ping got no replies; check the route before latency gates')
    elif slow(ping):
        measured = (f"Mac -> panel ping p95 {ping['p95_ms']} ms, loss {ping['loss_percent']}% (a clear link "
                    f'measured 13 ms max), so host-latency gates would measure the network, not the panel')
        # The router ping, taken at the same time, says which half of the path is slow.
        if gateway_ping and gateway_ping['received'] and not slow(gateway_ping):
            warnings.append(f"{measured}; Mac -> router is clear (p95 {gateway_ping['p95_ms']} ms), so the delay "
                            "is on the panel's 2.4 GHz side or the panel itself; rerun host-link before latency gates")
        elif gateway_ping and gateway_ping['received']:
            warnings.append(f"{measured}; Mac -> router is slow too (p95 {gateway_ping['p95_ms']} ms), so fix "
                            "the Mac's own link (5 GHz or Ethernet) first")
        else:
            warnings.append(measured + '; fix the link first')
    return warnings, notes


def resolve(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != 'http' or not parsed.hostname or parsed.path not in ('', '/') or parsed.query or \
            parsed.username or parsed.password:
        raise ValueError('Use a panel base URL such as http://hallpanel.local:8080')
    port = parsed.port or 80
    try:
        address = str(ipaddress.IPv4Address(parsed.hostname))
    except ValueError:
        # IPv4 only: macOS otherwise waits ~5 s for the board's missing mDNS AAAA record.
        address = socket.getaddrinfo(parsed.hostname, port, family=socket.AF_INET, type=socket.SOCK_STREAM)[0][4][0]
    return address, port


def command_output(command, timeout):
    try:
        return subprocess.run(command, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ''


def run_checks(address, port, count):
    # Strictly sequential: system_profiler's Wi-Fi query took the Mac's radio off channel and
    # raised a parallel ping from 6-8 ms to 44-73 ms average (2026-10-26). Ping first, profile last.
    route = command_output(['route', '-n', 'get', address], 5)
    gateway = parse_route_gateway(command_output(['route', '-n', 'get', 'default'], 5))
    # macOS -t is a total deadline in seconds; on Linux it would set the TTL instead.
    deadline = ['-t', str(int(count * PING_INTERVAL_S) + 4)] if sys.platform == 'darwin' else []
    ping = ['ping', '-n', '-c', str(count), '-i', str(PING_INTERVAL_S), *deadline]
    # Two small ICMP streams at 5/s each; the router stream locates a slow half of the path.
    gateway_process = None
    if gateway and gateway != address:
        try:
            gateway_process = subprocess.Popen([*ping, gateway], stdout=subprocess.PIPE,
                                               stderr=subprocess.DEVNULL, text=True)
        except OSError:
            pass
    ping_text = command_output([*ping, address], count * PING_INTERVAL_S + 10)
    gateway_text = ''
    if gateway_process:
        try:
            gateway_text = gateway_process.communicate(timeout=5)[0]
        except subprocess.TimeoutExpired:
            gateway_process.kill()
            gateway_process.communicate()
    panel, status_ms, panel_error = None, None, None
    try:
        from panel_remote import PanelClient
        with PanelClient(f'http://{address}:{port}', timeout=5, persistent=True) as client:
            body, _, status_ms = client.request('/status')
            panel = panel_facts(json.loads(body))
    except Exception as error:  # Report only the type (never token-bearing text); Mac facts still help.
        panel_error = type(error).__name__
    try:
        profile = json.loads(command_output(['system_profiler', 'SPAirPortDataType', '-json', '-detailLevel', 'basic'],
                                            15) or 'null')
    except ValueError:
        profile = None
    return {'profile': profile, 'route': route, 'panel': panel, 'status_http_ms': status_ms,
            'panel_error': panel_error, 'ping': parse_ping(ping_text), 'gateway_ping': parse_ping(gateway_text)}


def report(address, raw):
    link = mac_link(mac_wifi(raw['profile']), parse_route_interface(raw['route']))
    warnings, notes = assess(link, raw['panel'], raw['ping'], raw.get('gateway_ping'))
    panel = dict(raw['panel'] or {}, status_http_ms=round(raw['status_http_ms'], 1) if raw['status_http_ms'] else None)
    if raw['panel_error']:
        panel['error'] = raw['panel_error']
    # The router's address is not printed; only its ping statistics.
    return {'ok': not warnings, 'panel_ip': address, 'mac': link, 'panel': panel, 'ping': raw['ping'],
            'router_ping': raw.get('gateway_ping'), 'warnings': warnings, 'notes': notes}


def measurement_running():
    """A latency gate holds the device lock. This check's pings and system_profiler would distort it."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from panel_lock import ROOT as LOCK_ROOT, held
    return held(LOCK_ROOT / '.panel-ci/locks/device.lock')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://hallpanel.local:8080', help='Panel base URL; an IP is allowed')
    parser.add_argument('--count', type=int, default=PING_COUNT, help='Ping count, 5-50')
    args = parser.parse_args(argv)
    try:
        if not 5 <= args.count <= 50:
            raise ValueError('--count must be 5-50')
        if measurement_running():
            # On 2026-10-26 a host-link run beside a throughput gate failed that gate on status latency.
            raise RuntimeError('a panel measurement holds the device lock; wait with tools/panel lock wait --lock device')
        address, port = resolve(args.url)
        result = report(address, run_checks(address, port, args.count))
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps({'ok': False, 'error': type(error).__name__ + ': ' + str(error)}))
        return 1
    print(json.dumps(result, indent=2))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
