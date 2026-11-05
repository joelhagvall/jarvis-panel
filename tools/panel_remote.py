#!/usr/bin/env python3
"""Authenticated screenshots and real LVGL pointer input on the physical hallway panel."""
import argparse
from contextlib import contextmanager
import hashlib
import hmac
import secrets
from datetime import datetime
import http.client
import io
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RECEIVE_BUFFER_BYTES = 16384
try:
    from panel_lock import PanelLock
except ModuleNotFoundError:
    from tools.panel_lock import PanelLock


# Signed requests (HPv1, components/panel_debug/protocol.h): the token never crosses the network.
# Firmware from before 2026-10-27 only knows "Bearer <token>". A client falls back to it only while no panel at
# that address has ever accepted a signature; after the first signed success the address is pinned and the
# bearer token is never sent there again (HALLPANEL_LEGACY_AUTH=1 overrides, for a deliberate downgrade).
AUTH_PIN = Path(os.environ.get('HALLPANEL_AUTH_PIN', ROOT / '.panel-ci' / 'panel-auth.json'))


def sign(token, method, path, lease, body, ts=None, nonce=None):
    """The HPv1 Authorization header for one request; a fresh nonce every call."""
    ts = int(time.time() * 1000) if ts is None else ts
    nonce = secrets.token_hex(8) if nonce is None else nonce
    message = '\n'.join(('HPv1', method, path, str(ts), nonce, lease or '')).encode() + b'\n' + (body or b'')
    signature = hmac.new(token.encode(), message, hashlib.sha256).hexdigest()
    return f'HPv1 ts={ts},nonce={nonce},sig={signature}'


def auth_pinned(address):
    try:
        return json.loads(AUTH_PIN.read_text()).get(address) == 'hpv1'
    except (OSError, ValueError, AttributeError):
        return False


def pin_auth(address):
    try:
        pins = json.loads(AUTH_PIN.read_text())
    except (OSError, ValueError):
        pins = {}
    if not isinstance(pins, dict) or pins.get(address) == 'hpv1':
        return
    pins[address] = 'hpv1'
    AUTH_PIN.parent.mkdir(parents=True, exist_ok=True)
    partial = AUTH_PIN.with_name(f'.{AUTH_PIN.name}.{os.getpid()}.tmp')
    partial.write_text(json.dumps(pins))
    partial.chmod(0o600)
    os.replace(partial, AUTH_PIN)


def signs_requests(error):
    """True when a 401/503 came from firmware that knows HPv1 (its body says so)."""
    try:
        return json.loads(error.read() or b'{}').get('auth') == 'hpv1'
    except (ValueError, AttributeError, OSError):
        return False


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # A device must never redirect its credential to another host.


class ReceiveBufferMixin:
    receive_buffer_bytes = None

    def connect(self):
        super().connect()
        if self.receive_buffer_bytes is not None:
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, self.receive_buffer_bytes)


class PanelHTTPConnection(ReceiveBufferMixin, http.client.HTTPConnection):
    pass


class PanelHTTPSConnection(ReceiveBufferMixin, http.client.HTTPSConnection):
    pass


def panel_connection(kind, receive_buffer_bytes, host, **kwargs):
    connection = kind(host, **kwargs)
    connection.receive_buffer_bytes = receive_buffer_bytes
    return connection


class PanelHTTPHandler(urllib.request.HTTPHandler):
    def __init__(self, receive_buffer_bytes):
        super().__init__()
        self.receive_buffer_bytes = receive_buffer_bytes

    def http_open(self, request):
        return self.do_open(lambda host, **kw: panel_connection(
            PanelHTTPConnection, self.receive_buffer_bytes, host, **kw), request)


class PanelHTTPSHandler(urllib.request.HTTPSHandler):
    def __init__(self, receive_buffer_bytes):
        super().__init__()
        self.receive_buffer_bytes = receive_buffer_bytes

    def https_open(self, request):
        return self.do_open(lambda host, **kw: panel_connection(
            PanelHTTPSConnection, self.receive_buffer_bytes, host, **kw), request, context=self._context)


def local_token():
    from ruamel.yaml import YAML
    try:
        values = YAML(typ="safe").load((ROOT / "styles/glass-v2/secrets.yaml").read_text())
    except Exception:
        # YAML diagnostics can include secret values. Report only the file that needs attention.
        raise ValueError("Cannot read styles/glass-v2/secrets.yaml") from None
    if not isinstance(values, dict):
        raise ValueError("Invalid styles/glass-v2/secrets.yaml")
    token = values.get("panel_debug_token", "")
    if not isinstance(token, str) or len(token) < 32:
        raise ValueError("Missing panel_debug_token in styles/glass-v2/secrets.yaml")
    return token


def form_body(form):
    return urllib.parse.urlencode(form, safe=':').encode('ascii')


# Reads that neither show nor touch the UI: no device lease or local device lock (a health check may run them
# beside a test). The panel enforces the same list.
UNLEASED = ('/status', '/lease', '/logs', '/crash', '/tasks')


class PanelClient:
    def __init__(self, url="http://hallpanel.local:8080", token=None, timeout=8, persistent=False,
                 receive_buffer_bytes=DEFAULT_RECEIVE_BUFFER_BYTES):
        if receive_buffer_bytes is not None and (type(receive_buffer_bytes) is not int or
                                                not 4096 <= receive_buffer_bytes <= 65536):
            raise ValueError('Receive buffer must be 4096–65536 bytes')
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or \
                parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Use a device base URL such as http://hallpanel.local:8080")
        self.url = url.rstrip("/")
        self.host_header = parsed.netloc
        if parsed.scheme == "http" and parsed.hostname.endswith(".local"):
            # macOS waits ~5 s for a missing mDNS AAAA record. This board uses IPv4.
            address = socket.getaddrinfo(parsed.hostname, parsed.port or 80, family=socket.AF_INET,
                                         type=socket.SOCK_STREAM)[0][4][0]
            self.url = f"http://{address}:{parsed.port or 80}"
        self.token = token if token is not None else local_token()
        self.timeout = timeout
        self.receive_buffer_bytes = receive_buffer_bytes
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirects(),
            PanelHTTPHandler(receive_buffer_bytes), PanelHTTPSHandler(receive_buffer_bytes))
        self.connection = None
        self._lock = None
        self._lease_id = None
        self._lease_owned = False
        self._lease_renew_at = 0
        self._protocol = None
        self._auth = None                  # 'hpv1' once a signature was accepted, 'bearer' for old firmware
        self.last_transport = {}
        if persistent:
            endpoint = urllib.parse.urlsplit(self.url)
            kind = PanelHTTPSConnection if endpoint.scheme == 'https' else PanelHTTPConnection
            self.connection = kind(endpoint.hostname, endpoint.port, timeout=timeout)
            self.connection.receive_buffer_bytes = receive_buffer_bytes

    def close(self):
        try:
            if self._lease_id and self._lease_owned:
                try:
                    self.request('/lease', {'action': 'release', 'id': self._lease_id, 'ttl_ms': 300000})
                except (OSError, http.client.HTTPException):
                    pass  # Device expiry is the fallback when the link vanished.
        finally:
            self._lease_id = None
            self._protocol = None
            if self._lock:
                self._lock.__exit__(None, None, None)
                self._lock = None
        if self.connection:
            self.connection.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _ensure_exclusive(self, require_device_lease=False):
        if self._lock is None:
            self._lock = PanelLock('device')
            try:
                self._lock.__enter__()
            except BaseException:
                self._lock = None
                raise
        if self._protocol is None:
            self._protocol = self.status()
        modern = self._protocol.get('protocol', 1) >= 2 and self._protocol.get('lease_required') is True
        if require_device_lease and not modern:
            raise RuntimeError('Firmware lacks a device lease; use the checked migration upload first')
        if not modern:
            return  # Legacy migration is protected across local agents only.
        if self._lease_id is None:
            inherited = os.environ.get('HALLPANEL_LEASE_ID')
            if inherited and os.environ.get('HALLPANEL_LEASE_URL') == self.url and \
                    os.environ.get('HALLPANEL_LEASE_BOOT') == str(self._protocol.get('boot_id')):
                if len(inherited) != 64 or any(c not in '0123456789abcdef' for c in inherited):
                    raise ValueError('Invalid inherited device lease')
                self._lease_id, self._lease_owned = inherited, False
            else:
                self._lease_id, self._lease_owned = secrets.token_hex(32), True
            try:
                self.request('/lease', {'action': 'acquire', 'id': self._lease_id, 'ttl_ms': 300000})
            except BaseException:
                # Not acquired: forget the id so the next call acquires again. Keeping it made every later call
                # "renew" a lease it never had, so one 409 at the start (another client's lease still running)
                # kept a persistent client refused for good and wait_ready timed out (2026-11-03).
                self._lease_id, self._lease_owned = None, False
                raise
            self._lease_renew_at = time.monotonic() + 90
        elif time.monotonic() >= self._lease_renew_at:
            self.request('/lease', {'action': 'renew', 'id': self._lease_id, 'ttl_ms': 300000})
            self._lease_renew_at = time.monotonic() + 90

    @contextmanager
    def exclusive(self, require_device_lease=True):
        try:
            self._ensure_exclusive(require_device_lease)
            yield self
        finally:
            self.close()

    def subprocess_options(self, env=None):
        self._ensure_exclusive(require_device_lease=True)
        options = self._lock.subprocess_options(env)
        options['env'].update(HALLPANEL_LEASE_ID=self._lease_id, HALLPANEL_LEASE_URL=self.url,
                              HALLPANEL_LEASE_BOOT=str(self._protocol.get('boot_id')))
        return options

    def _persistent_request(self, path, data, headers):
        try:
            started = time.monotonic()
            self.connection.request('GET' if data is None else 'POST', path, body=data, headers=headers)
            sent = time.monotonic()
            response = self.connection.getresponse()
            received = time.monotonic()
            body, result_headers, status = response.read(), dict(response.headers), response.status
            finished = time.monotonic()
            self.last_transport = {'request_ms': round((sent-started)*1000, 2),
                                   'headers_ms': round((received-sent)*1000, 2),
                                   'body_ms': round((finished-received)*1000, 2)}
            if not 200 <= status < 300:
                raise urllib.error.HTTPError(self.url + path, status, response.reason, response.headers, io.BytesIO(body))
            return body, result_headers
        except (OSError, http.client.HTTPException):
            # Never resend an input after an ambiguous disconnect. The next explicit command can reconnect.
            self.connection.close()
            raise

    def request(self, path, form=None, raw=None, timeout=None):
        """raw: an exact body (a replay script); timeout: seconds for one long diagnostic, then back."""
        if path not in UNLEASED:
            self._ensure_exclusive()
        if timeout is not None and timeout != self.timeout:
            previous = self.timeout
            self._set_timeout(timeout)
            try:
                return self.request(path, form, raw)
            finally:
                self._set_timeout(previous)
        # ':' stays literal: the panel's form parser does not percent-decode, and night times are "HH:MM".
        data = raw if raw is not None else None if form is None else form_body(form)
        start = time.monotonic()
        self.last_transport = {}
        address = urllib.parse.urlsplit(self.url).netloc
        legacy, switched = self._auth == 'bearer', False
        # A capture is safe to retry after the device's rate limit. Input is never retried; a request the
        # panel refused as unauthorized did not run, so it may be sent once more with legacy auth.
        for attempt in range(3):
            headers = {
                "Authorization": ("Bearer " + self.token) if legacy else
                sign(self.token, "GET" if data is None else "POST", path, self._lease_id, data),
                "Content-Type": "text/plain" if raw is not None else "application/x-www-form-urlencoded",
                "Host": self.host_header,
            }
            if self._lease_id:
                headers['X-Panel-Lease'] = self._lease_id
            request = urllib.request.Request(self.url + path, data=data, headers=headers)
            try:
                if self.connection:
                    body, result_headers = self._persistent_request(path, data, headers)
                else:
                    with self.opener.open(request, timeout=self.timeout) as response:
                        body, result_headers = response.read(), dict(response.headers)
                if not legacy and self._auth != 'hpv1':
                    self._auth = 'hpv1'
                    pin_auth(address)
                return body, result_headers, (time.monotonic() - start) * 1000
            except urllib.error.HTTPError as error:
                if error.code == 401 and not switched:
                    signed_panel = signs_requests(error)
                    if legacy and signed_panel:
                        # The panel was updated under this client (a flash): sign from now on.
                        error.close()
                        switched, legacy, self._auth = True, False, None
                        continue
                    if not legacy and not signed_panel and (not auth_pinned(address) or
                                                            os.environ.get('HALLPANEL_LEGACY_AUTH') == '1'):
                        error.close()
                        switched, legacy, self._auth = True, True, 'bearer'
                        continue
                if form is None and path in ("/screenshot", "/frame.rgb565") and error.code == 429 and attempt < 2:
                    error.close()
                    time.sleep(0.55)
                    continue
                raise
            except (OSError, http.client.HTTPException):
                if self.connection and data is None and attempt == 0:
                    continue  # A stale keep-alive socket is safe to replace for a read only.
                raise

    def _set_timeout(self, value):
        self.timeout = value
        if self.connection:
            self.connection.timeout = value
            if self.connection.sock:
                self.connection.sock.settimeout(value)

    # Diagnostics (docs/remote-panel.md). Logs, crash and tasks need no lease; the rest do.
    def logs(self):
        return json.loads(self.request('/logs', timeout=20)[0])

    def crash(self):
        return json.loads(self.request('/crash', timeout=20)[0])

    def tasks(self, seconds=2.0):
        window = int(seconds * 1000)
        return json.loads(self.request('/tasks', {'window_ms': window}, timeout=window / 1000 + 15)[0])

    def redraw(self, seconds=3.0):
        window = int(seconds * 1000)
        return json.loads(self.request('/redraw', {'duration_ms': window}, timeout=window / 1000 + 15)[0])

    def clip(self, seconds=2.0, region=(0, 0, 1024, 600), scale=2, quality=75):
        x, y, w, h = region
        form = {'duration_ms': int(seconds * 1000), 'x': x, 'y': y, 'w': w, 'h': h, 'scale': scale, 'quality': quality}
        return self.request('/clip', form, timeout=seconds + 60)[0]

    def touches(self):
        return json.loads(self.request('/touches', timeout=20)[0])

    def replay(self, script):
        last = int(script.rsplit(';', 1)[-1].split(',', 1)[0])
        return json.loads(self.request('/replay', raw=script.encode('ascii'), timeout=last / 1000 + 15)[0])

    def restart(self):
        """Clean software restart; returns once the panel answers again with a new boot id."""
        before = self.status()['boot_id']
        self.request('/restart', {'reason': 'diagnostics'})
        self.close()
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            time.sleep(2)
            try:
                status = self.status()
            except (OSError, http.client.HTTPException, urllib.error.URLError, ValueError):
                continue
            if status['boot_id'] != before:
                return {'boot_id': status['boot_id'], 'reset_reason': status['reset_reason'], 'uptime_ms': status['uptime_ms']}
        raise RuntimeError('Panel did not come back within 120 s')

    def settings(self, key=None, value=None):
        if key is None:
            return json.loads(self.request('/settings')[0])
        return json.loads(self.request('/settings', {'key': key, 'value': value})[0])

    def status(self):
        status = json.loads(self.request("/status")[0])
        if self._protocol is None:
            self._protocol = status
        return status

    def ui(self):
        return json.loads(self.request("/ui")[0])

    def tap(self, x, y, duration=60, settle=0.12):
        self._point(x, y)
        if type(duration) is not int or not 40 <= duration <= 500:
            raise ValueError("Tap duration must be 40–500 ms")
        answer = json.loads(self.request("/tap", {"x": x, "y": y, "duration_ms": duration})[0])
        time.sleep(settle)
        return answer

    def swipe(self, x1, y1, x2, y2, duration=300, settle=0.12):
        self._point(x1, y1)
        self._point(x2, y2)
        if type(duration) is not int or not 100 <= duration <= 2000:
            raise ValueError("Swipe duration must be 100–2000 ms")
        answer = json.loads(self.request("/swipe", {
            "x1": x1, "y1": y1, "x2": x2, "y2": y2, "duration_ms": duration})[0])
        time.sleep(settle)
        return answer

    @staticmethod
    def _point(x, y):
        if type(x) is not int or type(y) is not int or not 0 <= x < 1024 or not 0 <= y < 600:
            raise ValueError("Coordinates must be integers within 1024 × 600")

    def tap_text(self, text):
        widgets = self.ui()["widgets"]
        matches = [w for w in widgets if w.get("text") == text]
        if len(matches) != 1:
            raise ValueError(f"Expected one visible label {text!r}, found {len(matches)}")
        label = matches[0]
        x, y = label["x"] + label["width"] // 2, label["y"] + label["height"] // 2
        parents = [w for w in widgets if w.get("clickable") and
                   (label["path"] == w["path"] or label["path"].startswith(w["path"] + "/")) and
                   w["x"] <= x < w["x"] + w["width"] and w["y"] <= y < w["y"] + w["height"]]
        if not parents:
            raise ValueError(f"Label {text!r} has no visible clickable ancestor")
        return self.tap(x, y)

    def element(self, identifier):
        ui = self.ui()
        if ui.get('truncated'):
            raise ValueError('Truncated UI tree; cannot choose an element safely')
        matches = [w for w in ui['widgets'] if identifier in (w.get('id'), w.get('path'))]
        if len(matches) != 1:
            raise ValueError('Expected exactly one visible element ID/path')
        return matches[0]

    def tap_id(self, identifier):
        widget = self.element(identifier)
        if not widget.get('clickable') or widget.get('disabled'):
            raise ValueError('Element is not clickable')
        return self.tap(widget['x'] + widget['width'] // 2, widget['y'] + widget['height'] // 2)

    def drag(self, x1, y1, x2, y2, duration=300, settle=0.12):
        return self.swipe(x1, y1, x2, y2, duration, settle)

    def slider(self, identifier, value, duration=300):
        widget = self.element(identifier)
        if widget.get('type') != 'slider' or not widget.get('clickable') or type(value) is not int or \
                not widget['min'] <= value <= widget['max'] or widget['width'] <= widget['height']:
            raise ValueError('Expected a horizontal slider and an integer within its range')
        minimum, maximum = widget['min'], widget['max']
        if minimum == maximum:
            raise ValueError('Slider has an empty range')
        # LVGL bar/slider endpoints are the content center's left and right edge.
        left, right = widget['x'], widget['x'] + widget['width'] - 1
        x = lambda v: round(left + (right - left) * (v - minimum) / (maximum - minimum))
        y = widget['y'] + widget['height'] // 2
        return self.drag(x(widget['value']), y, x(value), y, duration)

    def test_mode(self, mode, duration_ms=10000):
        allowed = ('restore', 'animation', 'animation_live', 'animation_60', 'animation_60_linear', 'animation_60_staged', 'listening', 'thinking', 'speaking', 'speaking_live', 'idle', 'night', 'ota', 'chat', 'chat_scroll',
                   'wifi_disconnected', 'ha_disconnected', 'api_timeout', 'missing_image', 'turntable', 'music_load',
                   'detail_weather','detail_calendar','detail_ai')
        if mode not in allowed or type(duration_ms) is not int or not 100 <= duration_ms <= 30000:
            raise ValueError('Unsupported synthetic test mode or duration (100–30000 ms)')
        self._ensure_exclusive(require_device_lease=True)
        return json.loads(self.request('/test', {'mode': mode, 'duration_ms': duration_ms})[0])

    def screenshot(self, destination, lossless=False):
        from PIL import Image
        try:
            data, headers, elapsed = self.request("/frame.rgb565" if lossless else "/screenshot")
        except urllib.error.HTTPError as error:
            if error.code != 503:
                raise
            detail = error.read(2048)
            error.close()
            if not lossless and b'JPEG encoder unavailable' in detail:
                # The encoder can fail while the actual framebuffer is healthy. A clip uses that same
                # encoder; use the existing exact RGB565 endpoint and report the real PNG path instead.
                result = self.screenshot(Path(destination).with_suffix('.png'), lossless=True)
                result['source'] = 'rgb565'
                return result
            # Spelas nu's turning record keeps a flip pending, so the still capture answers 503
            # (docs/now-playing.md). A one-frame clip grabs a presented frame instead; it is JPEG, never lossless.
            return self._screenshot_from_clip(destination)
        headers = {k.lower(): v for k, v in headers.items()}
        width, height = int(headers["x-width"]), int(headers["x-height"])
        if (width, height) != (1024, 600):
            raise ValueError("Unexpected panel dimensions")
        output = Path(destination).expanduser().resolve()
        if lossless:
            image = decode_rgb565(data, width, height)
        else:
            image = Image.open(io.BytesIO(data))
            image.load()
            if image.size != (width, height):
                raise ValueError("JPEG dimensions do not match capture metadata")
        if lossless and output.suffix.lower() != ".png":
            raise ValueError("A lossless RGB565 capture must be saved as .png")
        # Screens can contain private calendar/reply text. Files remain private to this user.
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as file:
            if not lossless and output.suffix.lower() in (".jpg", ".jpeg"):
                file.write(data)
            else:
                image.save(file, format="PNG")
        return {"path": str(output), "width": width, "height": height, "transfer_bytes": len(data),
                "elapsed_ms": round(elapsed, 1), "frame": int(headers["x-frame-sequence"]),
                "main_copy_us": int(headers["x-capture-copy-us"]), "lossless": lossless,
                "transport": dict(self.last_transport), "receive_buffer_bytes": self.receive_buffer_bytes}

    def _screenshot_from_clip(self, destination):
        from PIL import Image
        import panel_diag
        start = time.monotonic()
        header, frames = panel_diag.parse_clip(self.clip(seconds=0.2, scale=1, quality=95))
        if not frames:
            raise RuntimeError('Panel busy: the still capture answered 503 and the clip held no frame')
        frame = frames[-1]
        image = Image.open(io.BytesIO(frame['jpeg']))
        image.load()
        # The JPEG is padded to whole 16-pixel blocks (1024 × 608); the screen is the top 600 rows.
        if image.size[0] != 1024 or image.size[1] < 600:
            raise ValueError('Unexpected clip frame dimensions')
        image = image.crop((0, 0, 1024, 600))
        output = Path(destination).expanduser().resolve()
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as file:
            image.save(file, format="JPEG" if output.suffix.lower() in (".jpg", ".jpeg") else "PNG", quality=95)
        return {"path": str(output), "width": 1024, "height": 600, "transfer_bytes": len(frame['jpeg']),
                "elapsed_ms": round((time.monotonic() - start) * 1000, 1), "frame": frame['sequence'],
                "main_copy_us": frame['grab_cost_us'], "lossless": False, "source": "clip",
                "transport": dict(self.last_transport), "receive_buffer_bytes": self.receive_buffer_bytes}

    def inspect(self, folder=None, lossless=False):
        """One reviewable snapshot for an agent: pixels, visible controls and page/memory state."""
        folder = Path(tempfile.mkdtemp(prefix='hallpanel-inspect-')) if folder is None else Path(folder).expanduser().resolve()
        if not folder.exists():
            folder.mkdir(parents=True, mode=0o700)
        elif any(folder.iterdir()):
            raise ValueError('Inspection output directory must be empty')
        folder.chmod(0o700)
        before = self.status()
        ui = self.ui()
        image = self.screenshot(folder / ('screen.png' if lossless else 'screen.jpg'), lossless)
        after = self.status()
        result = {'captured_at': datetime.now().astimezone().isoformat(), 'page': after['page'],
                  'consistent': before['page'] == ui['page'] == after['page'], 'screenshot': image,
                  'ui_path': str(folder / 'ui.json'), 'status_path': str(folder / 'status.json'),
                  'widgets': len(ui['widgets']), 'truncated': ui.get('truncated', False)}
        private_json(folder / 'ui.json', ui)
        private_json(folder / 'status.json', {'before': before, 'after': after})
        private_json(folder / 'snapshot.json', result)
        if not result['consistent']:
            raise ValueError('Page changed during inspection; inspect again before input')
        return result


def private_json(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as output:
        json.dump(value, output, ensure_ascii=False, indent=2)


def session_command(client, command):
    if not isinstance(command, dict):
        raise ValueError('A session command must be a JSON object')
    op = command.get('op')
    schemas = {'status': set(), 'ui': set(), 'inspect': {'out', 'lossless'},
               'screenshot': {'out', 'lossless'}, 'tap': {'x', 'y', 'duration'},
               'tap-text': {'text'}, 'tap-id': {'element'}, 'slider': {'element', 'value', 'duration'},
               'test-mode': {'mode', 'duration_ms'}, 'drag': {'x1', 'y1', 'x2', 'y2', 'duration'}, 'swipe': {'x1', 'y1', 'x2', 'y2', 'duration'}}
    if op not in schemas or set(command) - schemas[op] - {'id', 'op', 'expect_page'}:
        raise ValueError('Unknown operation or fields; see tools/panel session --help')
    identifier = command.get('id')
    if identifier is not None and type(identifier) not in (int, str):
        raise ValueError('Command id must be a string or integer')
    if 'lossless' in command and type(command['lossless']) is not bool:
        raise ValueError('lossless must be a JSON boolean')
    if 'expect_page' in command and client.status()['page'] != command['expect_page']:
        raise ValueError('Expected page is no longer active; inspect before more input')
    if op == 'status': return client.status()
    if op == 'ui': return client.ui()
    if op == 'inspect': return client.inspect(command.get('out'), command.get('lossless', False))
    if op == 'screenshot': return client.screenshot(command['out'], command.get('lossless', False))
    if op == 'tap': answer = client.tap(command['x'], command['y'], command.get('duration', 60))
    elif op == 'tap-text': answer = client.tap_text(command['text'])
    elif op == 'tap-id': answer = client.tap_id(command['element'])
    elif op == 'slider': answer = client.slider(command['element'], command['value'], command.get('duration', 300))
    elif op == 'test-mode': answer = client.test_mode(command['mode'], command.get('duration_ms', 10000))
    else: answer = client.swipe(*(command[k] for k in ('x1', 'y1', 'x2', 'y2')), duration=command.get('duration', 300))
    return {'input': answer, 'after': client.status()}


def run_session(client, source, output):
    """JSON lines in/out. Stop at the first error; never run later queued actions blindly."""
    while True:
        line = source.readline(8193)
        if not line: break
        if not line.strip(): continue
        identifier, op = None, None
        try:
            if len(line) > 8192: raise ValueError('Session command exceeds 8192 characters')
            command = json.loads(line)
            if isinstance(command, dict): identifier, op = command.get('id'), command.get('op')
            result = {'id': identifier, 'ok': True, 'result': session_command(client, command)}
        except (OSError, http.client.HTTPException, ValueError, RuntimeError, TypeError, KeyError) as error:
            result = {'id': identifier if type(identifier) in (int, str) else None, 'ok': False,
                      'error': 'Command failed; inspect the panel before resuming. Later commands were not executed.',
                      'error_type': type(error).__name__,
                      'http_status': error.code if isinstance(error, urllib.error.HTTPError) else None,
                      'input_may_have_run': op in ('tap', 'tap-text', 'tap-id', 'slider', 'test-mode', 'drag', 'swipe')}
            output.write(json.dumps(result, ensure_ascii=False) + '\n')
            output.flush()
            return 1
        output.write(json.dumps(result, ensure_ascii=False) + '\n')
        output.flush()
    return 0


def decode_rgb565(data, width, height):
    from PIL import Image
    if len(data) != width * height * 2:
        raise ValueError("Truncated RGB565 framebuffer")
    return Image.frombytes("RGB", (width, height), data, "raw", "BGR;16")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("HALLPANEL_DEBUG_URL", "http://hallpanel.local:8080"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("status")
    commands.add_parser("ui")
    inspect = commands.add_parser('inspect', help='Save screenshot, widget tree and status in one private folder')
    inspect.add_argument('--out', type=Path)
    inspect.add_argument('--lossless', action='store_true')
    commands.add_parser('session', help='JSONL stdin/stdout; reuse HTTP connection; stop on first error',
                        description='Read one JSON object per line: {"id":1,"op":"inspect"}. '
                        'Operations: inspect, status, ui, screenshot, tap, tap-text, swipe. '
                        'Optional expect_page guards an action. Output is JSONL; first error stops the session. '
                        'See docs/remote-panel.md for fields and examples.')
    shot = commands.add_parser("screenshot")
    shot.add_argument("-o", "--output", required=True)
    shot.add_argument("--lossless", action="store_true", help="Exact RGB565 framebuffer as PNG; larger transfer")
    tap = commands.add_parser("tap")
    tap.add_argument("x", type=int)
    tap.add_argument("y", type=int)
    tap.add_argument("--duration", type=int, default=60)
    label = commands.add_parser("tap-text")
    label.add_argument("text")
    element = commands.add_parser('tap-id', help='Tap one visible clickable widget path/ID')
    element.add_argument('element')
    slider = commands.add_parser('slider', help='Drag a horizontal slider to a value')
    slider.add_argument('element')
    slider.add_argument('value', type=int)
    slider.add_argument('--duration', type=int, default=300)
    test = commands.add_parser('test-mode', help='Bounded synthetic UI state; no real network/audio fault')
    test.add_argument('mode')
    test.add_argument('--duration-ms', type=int, default=10000)
    swipe = commands.add_parser("swipe", aliases=['drag'])
    for name in ("x1", "y1", "x2", "y2"):
        swipe.add_argument(name, type=int)
    swipe.add_argument("--duration", type=int, default=300)
    import panel_diag
    panel_diag.add_parsers(commands)
    args = parser.parse_args()
    client = None
    try:
        client = PanelClient(args.url, persistent=True)
        if args.command == 'session':
            try: return run_session(client, sys.stdin, sys.stdout)
            finally: client.close()
        if args.command in panel_diag.COMMANDS:
            result = panel_diag.run(client, args)
            print(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False, indent=2))
            if args.command == 'health' and not getattr(args, 'json', False):
                return 0 if result.startswith('Panel health') and ': OK' in result.split('\n', 1)[0] else 1
            return 0
        if args.command == 'inspect': result = client.inspect(args.out, args.lossless)
        elif args.command == "screenshot": result = client.screenshot(args.output, args.lossless)
        elif args.command == "tap": result = client.tap(args.x, args.y, args.duration)
        elif args.command == "tap-text": result = client.tap_text(args.text)
        elif args.command == "tap-id": result = client.tap_id(args.element)
        elif args.command == "slider": result = client.slider(args.element, args.value, args.duration)
        elif args.command == "test-mode": result = client.test_mode(args.mode, args.duration_ms)
        elif args.command in ("swipe", "drag"): result = client.swipe(args.x1, args.y1, args.x2, args.y2, args.duration)
        elif args.command == "ui": result = client.ui()
        else: result = client.status()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except urllib.error.HTTPError as error:
        print(f"Panel returned HTTP {error.code}; input is never retried automatically.", file=sys.stderr)
    except (OSError, http.client.HTTPException, ValueError, RuntimeError, KeyError) as error:
        print(f"Panel request failed: {error}", file=sys.stderr)
        if hint := local_network_hint(error):
            print(hint, file=sys.stderr)
    finally:
        if client: client.close()
    return 1



def local_network_hint(error, platform=sys.platform):
    """macOS answers EHOSTUNREACH ("No route to host") to every Python on the LAN when the app that runs the
    agent (Cursor, Terminal, Claude) lacks Local Network permission, while curl, ping and ssh still reach the
    panel (2026-11-01, Cursor). Say so instead of letting it look like the panel is down."""
    if platform == "darwin" and isinstance(error, OSError) and error.errno == 65:
        return ("If `curl -m 5 http://hallpanel.local:8080/status` answers (401 is fine), macOS blocks Python here: "
                "System Settings > Privacy & Security > Local Network, turn on the app that runs this shell.")
    return None


if __name__ == "__main__":
    raise SystemExit(main())
