#!/usr/bin/env python3
"""Read-only target checks. Never opens serial ports, contacts a panel or resets it."""
import argparse
import json
import ipaddress
from pathlib import Path
import re
import sys


ROOT = Path(__file__).resolve().parents[1]

def is_serial_target(device):
    return str(device).startswith('/dev/') or re.fullmatch(r'COM[0-9]+', str(device), re.I) is not None


def usb_ports():
    from serial.tools import list_ports
    return [{'device': p.device, 'vid': p.vid, 'pid': p.pid, 'serial_number': p.serial_number}
            for p in list_ports.comports() if p.vid is not None and p.pid is not None]


def validate_target(device=None, *, serial_number=None):
    device = device or 'hallpanel.local'
    if not is_serial_target(device):
        try:
            ipaddress.ip_address(device)
        except ValueError:
            if re.fullmatch(r'[A-Za-z0-9_.-]+', device) is None:
                raise ValueError('Use an OTA hostname or IP address, not a URL or credentials') from None
        if serial_number is not None:
            raise ValueError('USB serial number requires an explicit serial --device')
        return {'device': device, 'transport': 'network', 'network_contacted': False}
    matches = [p for p in usb_ports() if Path(p['device']).resolve() == Path(device).resolve()]
    if len(matches) != 1 or not Path(device).exists():
        raise ValueError('USB target absent or ambiguous; list current ports with tools/panel preflight --usb')
    port = matches[0]
    if serial_number is not None and port['serial_number'] != serial_number:
        raise ValueError('USB serial number differs from the explicitly selected device')
    # Presence/optional serial match do not establish ESP32 model or network identity.
    return {'device': device, 'transport': 'usb', 'usb': port, 'board_identity_verified': False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', help='Explicit upload target; default hallpanel.local, never first USB port')
    parser.add_argument('--usb', action='store_true', help='List USB metadata without opening ports')
    parser.add_argument('--require-usb', action='store_true', help='Fail unless an explicit serial target is present')
    parser.add_argument('--serial-number', help='Require this known USB serial number on the explicit target')
    args = parser.parse_args(argv)
    try:
        if Path(sys.prefix).resolve() != (ROOT / '.venv').resolve():
            raise RuntimeError('Use tools/panel preflight (project virtual environment required)')
        if args.require_usb and not is_serial_target(args.device or ''):
            raise ValueError('USB work requires explicit --device; --usb only lists candidates')
        result = validate_target(args.device, serial_number=args.serial_number)
        if args.usb:
            result['available_usb_ports'] = usb_ports()
        print(json.dumps({'ok': True, 'python': sys.executable, **result}))
        return 0
    except (OSError, ValueError, RuntimeError, ImportError) as exc:
        print(json.dumps({'ok': False, 'error': str(exc)}))
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
