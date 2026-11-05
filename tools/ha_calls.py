#!/usr/bin/env python3
"""Show which Home Assistant actions arrive while you try something on the panel (read only).

  tools/panel ha-calls [--seconds 20] [--domain jarvis_agent --domain media_player]

Runs on Jarvis over ssh with its Home Assistant token (never printed) and prints each call_service event of the
chosen domains with its data. Nothing printed while you used a panel control means the panel did not send it, or
Home Assistant refused it before it ran: a response-only service drops a plain homeassistant.action silently
(2026-10-28, the Spotify volume slider; tests/test_panel_ha_actions.py)."""
import argparse
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REMOTE = r"""
import json, socket, base64, os, struct, time
token = open(os.path.expanduser("~/.config/homeassistant/token")).read().strip()
s = socket.create_connection(("127.0.0.1", 8123), timeout=20)
key = base64.b64encode(os.urandom(16)).decode()
s.sendall(("GET /api/websocket HTTP/1.1\r\nHost: 127.0.0.1\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
buf = b""
while b"\r\n\r\n" not in buf: buf += s.recv(4096)
rest = buf.split(b"\r\n\r\n", 1)[1]
def send(obj):
    data = json.dumps(obj).encode(); mask = os.urandom(4)
    head = bytes([0x81]) + (bytes([0x80 | len(data)]) if len(data) < 126 else bytes([0x80 | 126]) + struct.pack(">H", len(data)))
    s.sendall(head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))
def recv():
    global rest
    def need(n):
        global rest
        while len(rest) < n: rest += s.recv(65536)
        out, rest = rest[:n], rest[n:]; return out
    h = need(2); n = h[1] & 127
    if n == 126: n = struct.unpack(">H", need(2))[0]
    elif n == 127: n = struct.unpack(">Q", need(8))[0]
    return json.loads(need(n))
recv(); send({"type": "auth", "access_token": token}); recv()
send({"id": 1, "type": "subscribe_events", "event_type": "call_service"}); recv()
import sys
end = time.time() + float(sys.argv[1])
domains = set(sys.argv[2:])
s.settimeout(1)
while time.time() < end:
    try:
        m = recv()
    except (socket.timeout, TimeoutError):
        continue
    d = (m.get("event") or {}).get("data") or {}
    if d.get("domain") in domains:
        print(time.strftime("%H:%M:%S"), d.get("domain"), d.get("service"), json.dumps(d.get("service_data"))[:120], flush=True)
print("done")
"""


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seconds", type=float, default=20)
    parser.add_argument("--domain", action="append", default=None)
    parser.add_argument("--host", default="user@jarvis.local")
    args = parser.parse_args(argv)
    domains = args.domain or ["jarvis_agent", "media_player", "esphome"]
    command = ["sh", str(ROOT / "tools/jarvis_ssh.sh"), args.host, "python3", "-I", "-", str(args.seconds), *domains]
    return subprocess.run(command, input=REMOTE, text=True).returncode


if __name__ == "__main__":
    raise SystemExit(main())
