"""Physical-panel control protocol, capture fidelity and credential handling. No real device."""
import importlib.util
import io
import http.client
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import struct
import subprocess
import tempfile
import threading
import unittest
import urllib.error
from unittest.mock import patch

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('panel_remote', ROOT / 'tools/panel_remote.py')
remote = importlib.util.module_from_spec(spec)
spec.loader.exec_module(remote)
TOKEN = 'a' * 64


def signed_ok(headers, method, path, body):
    """The fake panel's HPv1 check, written independently of the client's sign()."""
    import hashlib, hmac, re
    match = re.fullmatch(r'HPv1 ts=([1-9][0-9]{0,14}),nonce=([0-9a-f]{16}),sig=([0-9a-f]{64})',
                         headers.get('Authorization') or '')
    if not match:
        return False
    ts, nonce, sig = match.groups()
    message = f"HPv1\n{method}\n{path}\n{ts}\n{nonce}\n{headers.get('X-Panel-Lease') or ''}\n".encode() + body
    return hmac.compare_digest(hmac.new(TOKEN.encode(), message, hashlib.sha256).hexdigest(), sig)


class JarvisHarnessInstallTest(unittest.TestCase):
    """tools/install_jarvis_harness.sh ships the client every scheduled Jarvis job imports."""

    def test_installer_files_exist_and_cover_the_scheduled_tools(self):
        import re
        text = (ROOT / 'tools/install_jarvis_harness.sh').read_text()
        files = re.search(r'FILES="([^"]+)"', text).group(1).split()
        for name in files:
            self.assertTrue((ROOT / name).is_file(), name)
        for needed in ('tools/panel_remote.py', 'tools/panel_lock.py', 'tools/panel_metrics.py',
                       'tools/panel_scheduled_smoke.py', 'tests/performance/policy.json'):
            self.assertIn(needed, files)
        # Every local module a shipped tool imports, also inside a function, must ship too:
        # a missing panel_recovery.py broke the scheduled smoke on Jarvis (2026-10-27).
        todo = [Path(name).stem for name in files if name.startswith('tools/') and name.endswith('.py')]
        seen = set()
        while todo:
            module = todo.pop()
            if module in seen:
                continue
            seen.add(module)
            source = (ROOT / 'tools' / f'{module}.py').read_text()
            for imported in re.findall(r'^\s*(?:from|import)\s+([A-Za-z_]\w*)', source, re.M):
                if (ROOT / 'tools' / f'{imported}.py').is_file():
                    todo.append(imported)
        for module in sorted(seen):
            self.assertIn(f'tools/{module}.py', files, f'{module} is imported but not shipped')
        self.assertEqual(subprocess.run(['sh', '-n', str(ROOT / 'tools/install_jarvis_harness.sh')]).returncode, 0)


class NativeRemoteProtocolTest(unittest.TestCase):
    def test_authentication_gesture_bounds_and_fuzzed_requests(self):
        compiler = shutil.which('clang++') or shutil.which('g++')
        self.assertIsNotNone(compiler, 'A C++ compiler is required for native safety checks')
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / 'remote.cpp'
            binary = Path(tmp) / 'remote'
            # Headers made by the real Python client; the C++ side must accept exactly these.
            ts, now = 1793000000000, 1793000005000
            good = remote.sign(TOKEN, 'POST', '/tap', 'f' * 64, b'x=1&y=2&duration_ms=60', ts, '0123456789abcdef')
            status = remote.sign(TOKEN, 'GET', '/status', None, b'', ts + 1, 'fedcba9876543210')
            source.write_text(r'''
#include <cassert>
#include <random>
#ifdef __APPLE__
#include <CommonCrypto/CommonHMAC.h>
static void hmac(const uint8_t *k, size_t ks, const uint8_t *m, size_t ms, uint8_t out[32]) {
  CCHmac(kCCHmacAlgSHA256, k, ks, m, ms, out);
}
#else
#include <openssl/hmac.h>
static void hmac(const uint8_t *k, size_t ks, const uint8_t *m, size_t ms, uint8_t out[32]) {
  unsigned n = 32; HMAC(EVP_sha256(), k, int(ks), m, ms, out, &n);
}
#endif
#include "protocol.h"
using namespace esphome::panel_debug;
int main() {
  std::string token(64, 'a');
  const std::string good = GOOD_HEADER, status = STATUS_HEADER, lease(64, 'f'), body = "x=1&y=2&duration_ms=60";
  const uint64_t ts = TS_VALUE, now = NOW_VALUE;
  {
    ReplayGuard guard;
    assert(authorized_signed(good, token, "POST", "/tap", lease, body, now, guard, hmac));
    assert(!authorized_signed(good, token, "POST", "/tap", lease, body, now, guard, hmac));  // replay
    assert(authorized_signed(status, token, "GET", "/status", "", "", now, guard, hmac));
  }
  {
    ReplayGuard guard;  // every signed field matters; a refusal never consumes the nonce
    auto wrong = token; wrong[3] = 'b';
    assert(!authorized_signed(good, wrong, "POST", "/tap", lease, body, now, guard, hmac));
    assert(!authorized_signed(good, token, "GET", "/tap", lease, body, now, guard, hmac));
    assert(!authorized_signed(good, token, "POST", "/swipe", lease, body, now, guard, hmac));
    assert(!authorized_signed(good, token, "POST", "/tap", std::string(64, 'e'), body, now, guard, hmac));
    assert(!authorized_signed(good, token, "POST", "/tap", lease, "x=9&y=2&duration_ms=60", now, guard, hmac));
    assert(!authorized_signed(good, token, "POST", "/tap", lease, body, ts + SIGNED_WINDOW_MS + 1, guard, hmac));
    assert(!authorized_signed(good, token, "POST", "/tap", lease, body, ts - SIGNED_WINDOW_MS - 1, guard, hmac));
    assert(!authorized_signed(good, std::string(31, 'a'), "POST", "/tap", lease, body, now, guard, hmac));
    assert(!authorized_signed("Bearer " + token, token, "POST", "/tap", lease, body, now, guard, hmac));
    assert(authorized_signed(good, token, "POST", "/tap", lease, body, now, guard, hmac));
  }
  {
    ReplayGuard guard;  // a full ring raises the floor, so an evicted nonce cannot come back
    SignedHeader h;
    for (uint64_t i = 0; i < ReplayGuard::SIZE + 4; ++i) {
      h.ts = ts + i; h.nonce = 1000 + i;
      assert(guard.fresh(h, now));
      guard.remember(h);
    }
    h.ts = ts; h.nonce = 1000;
    assert(!guard.fresh(h, now));
    h.ts = ts + 2; h.nonce = 1;
    assert(!guard.fresh(h, now));
    h.ts = ts + ReplayGuard::SIZE + 10; h.nonce = 2;
    assert(guard.fresh(h, now));
  }
  SignedHeader parsed;
  assert(parse_signed(good, parsed) && parsed.ts == ts && parsed.nonce == 0x0123456789abcdefULL);
  for (std::string bad : {good.substr(0, good.size() - 1), good + "0", "HPv1 ts=0" + good.substr(21),
       "HPv1 ts=" + std::string(16, '1') + good.substr(21),
       "HPv1 ts=1,nonce=0123456789ABCDEF,sig=" + std::string(64, '0'),
       "HPv1 ts=1,nonce=0123456789abcdef,sig=" + std::string(63, '0')})
    assert(!parse_signed(bad, parsed));
  Gesture g;
  assert(parse_gesture("x=0&y=599&duration_ms=40", false, g));
  assert(g.x1 == 0 && g.x2 == 0 && g.y1 == 599 && g.y2 == 599);
  assert(parse_gesture("x1=1023&y1=599&x2=0&y2=0&duration_ms=2000", true, g));
  for (std::string s : {"x=1024&y=0&duration_ms=60", "x=1&y=600&duration_ms=60",
      "x=-1&y=1&duration_ms=60", "x=1&y=1&duration_ms=0", "x=1&y=1&duration_ms=501",
      "x=1&x=2&y=2&duration_ms=60", "x=1&y=1&duration_ms=60&", "x=1&y=1",
      "x=1&y=1&duration_ms=60&extra=1", "x=1.5&y=1&duration_ms=60",
      "x=99999999999999999999999&y=1&duration_ms=60", "x=1%00&y=1&duration_ms=60"})
    assert(!parse_gesture(s, false, g));
  for (int elapsed = 0; elapsed <= 400; ++elapsed) {
    int increasing = interpolate(0, 1023, elapsed, 300), decreasing = interpolate(599, 0, elapsed, 300);
    assert(increasing >= 0 && increasing <= 1023 && decreasing >= 0 && decreasing <= 599);
  }
  assert(interpolate(100, 900, 300, 300) == 900);
  assert(quoted("\"\n\\") == "\"\\\"\\u000a\\\\\"");
  std::mt19937 random(42);
  for (size_t n = 0; n < 50000; ++n) {
    std::string input(random() % 240, '\0');
    for (auto &c : input) c = char(random() & 255);
    parse_gesture(input, bool(n % 2), g);
    parse_signed(input, parsed);
    ReplayGuard fuzz;
    authorized_signed(input, token, "GET", input, input, input, now, fuzz, hmac);
    quoted(input);
  }
}
'''.replace('GOOD_HEADER', json.dumps(good)).replace('STATUS_HEADER', json.dumps(status))
              .replace('TS_VALUE', str(ts)).replace('NOW_VALUE', str(now)))
            subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
                            '-I', str(ROOT / 'components/panel_debug'), str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True, timeout=20)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


class PanelRemoteClientTest(unittest.TestCase):
    def setUp(self):
        # A loopback HTTP fixture is not the physical panel. Its locks must not
        # contend with a real device test running alongside local checks.
        self.lock_folder = tempfile.TemporaryDirectory(prefix='panel-mock-locks-')
        self.addCleanup(self.lock_folder.cleanup)
        real_lock = remote.PanelLock
        isolated_lock = patch.object(remote, 'PanelLock',
            lambda name, **kwargs: real_lock(name, root=Path(self.lock_folder.name), **kwargs))
        isolated_lock.start()
        self.addCleanup(isolated_lock.stop)
        self.calls = []
        self.posts = []
        self.auth = 'hpv1'                 # 'hpv1' new firmware, 'bearer' firmware from before 2026-10-27
        pin = tempfile.TemporaryDirectory(prefix='panel-auth-pin-')
        self.addCleanup(pin.cleanup)
        pinned = patch.object(remote, 'AUTH_PIN', Path(pin.name) / 'panel-auth.json')
        pinned.start()
        self.addCleanup(pinned.stop)
        self.connections = []
        self.mode = 'normal'
        parent = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = 'HTTP/1.1'
            def log_message(self, *args): pass

            def do_GET(self):
                parent.calls.append((self.path, self.headers.get('Authorization')))
                parent.connections.append(self.client_address)
                if not parent.authorized(self.headers, 'GET', self.path, b''):
                    parent.refuse(self)
                    return
                if parent.mode == 'drop_once':
                    parent.mode = 'normal'
                    self.connection.shutdown(remote.socket.SHUT_RDWR)
                    self.connection.close()
                    self.close_connection = True
                    return
                if parent.mode == 'redirect':
                    self.send_response(302)
                    self.send_header('Content-Length', '0')
                    self.send_header('Location', f'http://127.0.0.1:{self.server.server_port}/redirected')
                    self.end_headers()
                    return
                if self.path == '/screenshot':
                    out = io.BytesIO()
                    Image.new('RGB', (1024, 600), (25, 80, 160)).save(out, format='JPEG')
                    body = out.getvalue()
                else:
                    body = json.dumps({'page': 'page_hemmet', 'widgets': parent.widgets}).encode()
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                for key, value in {'X-Width': '1024', 'X-Height': '600', 'X-Frame-Sequence': '42',
                                   'X-Capture-Copy-Us': '1234'}.items(): self.send_header(key, value)
                self.end_headers()
                self.wfile.write(body)

            def do_POST(self):
                body = self.rfile.read(int(self.headers['Content-Length']))
                parent.calls.append((self.path, self.headers.get('Authorization')))
                if not parent.authorized(self.headers, 'POST', self.path, body):
                    parent.refuse(self)
                    return
                parent.posts.append((self.path, body))
                if parent.mode == 'drop':
                    self.connection.shutdown(remote.socket.SHUT_RDWR)
                    self.connection.close()
                    self.close_connection = True
                    return
                self.send_response(503 if parent.mode == 'busy' else 200)
                self.send_header('Content-Length', '11')
                self.end_headers()
                self.wfile.write(b'{"ok":true}')

        self.widgets = [
            {'path': 'top/0', 'type': 'button', 'clickable': True, 'x': 100, 'y': 500, 'width': 200, 'height': 60},
            {'path': 'top/0/0', 'type': 'label', 'text': 'Hemmet', 'x': 150, 'y': 510, 'width': 100, 'height': 40}]
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.client = remote.PanelClient(f'http://127.0.0.1:{self.server.server_port}', token=TOKEN)

    def authorized(self, headers, method, path, body):
        if self.auth == 'bearer':
            return headers.get('Authorization') == 'Bearer ' + TOKEN
        return signed_ok(headers, method, path, body)

    def refuse(self, handler):
        body = b'{"error":"unauthorized","auth":"hpv1"}' if self.auth == 'hpv1' else b'{"error":"unauthorized"}'
        handler.send_response(401)
        handler.send_header('Content-Length', str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def tearDown(self):
        self.client.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join()

    def test_refused_lease_acquire_is_retried_not_renewed(self):
        # 2026-11-03: one 409 at the start (another client's 5-minute lease) left the id set, so every later call
        # "renewed" a lease never held and `visual capture --wait-ready` timed out as "not ready".
        sent, refusals = [], [True]
        def request(path, fields=None, *args, **kwargs):
            sent.append(fields['action'])
            if refusals and refusals.pop():
                raise remote.urllib.error.HTTPError(path, 409, 'Conflict', {}, None)
            return b'{"ok":true}', {}
        client = remote.PanelClient(self.client.url, token=TOKEN, persistent=True)
        client._protocol = {'protocol': 2, 'lease_required': True, 'boot_id': 1}
        with patch.object(client, 'request', request):
            with self.assertRaises(OSError):
                client._ensure_exclusive()
            client._ensure_exclusive()
            self.assertEqual(sent, ['acquire', 'acquire'])
            self.assertTrue(client._lease_owned)
            client._lock.__exit__(None, None, None)
            client._lock = None

    def test_headers_and_status_without_exposing_the_key(self):
        self.assertEqual(self.client.status()['page'], 'page_hemmet')
        self.assertEqual(len(self.calls), 1)
        self.assertTrue(self.calls[0][1].startswith('HPv1 ts='))
        self.assertNotIn(TOKEN, self.calls[0][1])
        self.client.token = 'wrong'
        with self.assertRaises(urllib.error.HTTPError) as raised: self.client.status()
        self.assertEqual(raised.exception.code, 401)

    def test_every_request_and_retry_gets_a_fresh_nonce(self):
        for _ in range(3):
            self.client.status()
        self.client.tap(10, 10)
        nonces = [header.split('nonce=')[1][:16] for _, header in self.calls]
        self.assertEqual(len(set(nonces)), len(nonces))

    def test_old_firmware_gets_the_bearer_token_until_a_signature_was_ever_accepted(self):
        self.auth = 'bearer'
        self.assertEqual(self.client.status()['page'], 'page_hemmet')
        self.assertEqual(self.calls[-1][1], 'Bearer ' + TOKEN)
        self.assertFalse(remote.AUTH_PIN.exists())

    def test_after_one_signed_success_the_token_never_crosses_the_network_again(self):
        self.client.status()
        self.assertTrue(remote.auth_pinned(urllib.parse.urlsplit(self.client.url).netloc))
        self.auth = 'bearer'                     # e.g. someone impersonating the panel
        fresh = remote.PanelClient(self.client.url, token=TOKEN)
        with self.assertRaises(urllib.error.HTTPError) as raised:
            fresh.status()
        self.assertEqual(raised.exception.code, 401)
        self.assertFalse(any(header == 'Bearer ' + TOKEN for _, header in self.calls))

    def test_a_client_switches_to_signing_when_the_panel_is_flashed_under_it(self):
        self.auth = 'bearer'
        with remote.PanelClient(self.client.url, token=TOKEN, persistent=True) as client:
            client.status()
            self.auth = 'hpv1'
            self.assertEqual(client.status()['page'], 'page_hemmet')
        self.assertTrue(self.calls[-1][1].startswith('HPv1 '))

    def test_signed_input_is_not_resent_after_a_refusal_from_new_firmware(self):
        self.client.token = 'b' * 64
        with self.assertRaises(urllib.error.HTTPError):
            self.client.tap(10, 10)
        self.assertEqual(self.posts, [])
        paths = [path for path, _ in self.calls]
        self.assertEqual(len(paths), len(set(paths)), paths)      # nothing sent twice

    def test_local_panel_uses_ipv4_without_waiting_for_a_missing_aaaa_record(self):
        with patch.object(remote.socket, 'getaddrinfo', return_value=[
                (remote.socket.AF_INET, remote.socket.SOCK_STREAM, 6, '', ('127.0.0.1', self.server.server_port))]) as lookup:
            client = remote.PanelClient(f'http://hallpanel.local:{self.server.server_port}', token=TOKEN)
        self.assertEqual(lookup.call_args.kwargs['family'], remote.socket.AF_INET)
        self.assertEqual(client.status()['page'], 'page_hemmet')

    def test_redirects_are_not_followed_with_the_credential(self):
        self.mode = 'redirect'
        with self.assertRaises(urllib.error.HTTPError): self.client.status()
        self.assertEqual(len(self.calls), 1)

    def test_taps_are_never_retried_and_out_of_bounds_input_is_rejected_locally(self):
        for point in ((-1, 5), (1024, 5), (0, 600), (1.5, 10)):
            with self.assertRaises(ValueError): self.client.tap(*point, settle=0)
        self.assertEqual(self.posts, [])
        self.mode = 'busy'
        with self.assertRaises(urllib.error.HTTPError): self.client.tap(200, 530, settle=0)
        self.assertEqual(len(self.posts), 1)

    def test_tap_text_hits_the_label_inside_its_button(self):
        self.client.tap_text('Hemmet')
        self.assertEqual(self.posts, [('/tap', b'x=200&y=530&duration_ms=60')])
        self.widgets.append(dict(self.widgets[-1]))
        with self.assertRaises(ValueError): self.client.tap_text('Hemmet')
        self.assertEqual(len(self.posts), 1)

    def test_screenshot_is_verified_and_saved_privately(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'panel.png'
            result = self.client.screenshot(path)
            self.assertEqual((result['frame'], result['main_copy_us']), (42, 1234))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with Image.open(path) as image: self.assertEqual(image.size, (1024, 600))

    def test_a_busy_still_capture_falls_back_to_one_clip_frame(self):
        # Spelas nu's turning record answers 503 to /frame.rgb565 and /screenshot (docs/now-playing.md).
        jpeg = io.BytesIO()
        Image.new('RGB', (1024, 608), (10, 20, 30)).save(jpeg, format='JPEG')
        jpeg = jpeg.getvalue()
        clip = b'{"format":"hpclip1","frames":1}\n' + struct.pack('<5I', len(jpeg), 7, 0, 0, 900) + jpeg

        def busy(path, *args, **kwargs):
            raise urllib.error.HTTPError(path, 503, 'busy', {}, io.BytesIO(b''))
        with patch('sys.path', [str(ROOT / 'tools')] + __import__('sys').path), \
             patch.object(self.client, 'request', busy), \
             patch.object(self.client, 'clip', lambda **kwargs: clip), tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'panel.png'
            result = self.client.screenshot(path, lossless=True)
            self.assertEqual((result['source'], result['lossless'], result['frame']), ('clip', False, 7))
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with Image.open(path) as image: self.assertEqual(image.size, (1024, 600))

    def test_raw_pixel_order_and_truncated_capture(self):
        pixels = struct.pack('<HHHH', 0xf800, 0x07e0, 0x001f, 0xffff)
        self.assertEqual(list(remote.decode_rgb565(pixels, 4, 1).get_flattened_data()),
                         [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 255)])
        with self.assertRaises(ValueError): remote.decode_rgb565(pixels[:-1], 4, 1)

    def test_failed_jpeg_encoder_uses_exact_framebuffer_and_reports_png(self):
        calls=[]
        def request(path,*args,**kwargs):
            calls.append(path)
            if path=='/screenshot':
                raise urllib.error.HTTPError(path,503,'unavailable',{},io.BytesIO(b'{"error":"JPEG encoder unavailable"}'))
            self.assertEqual(path,'/frame.rgb565')
            return b'\x00\xf8'*(1024*600),{'X-Width':'1024','X-Height':'600','X-Frame-Sequence':'12','X-Capture-Copy-Us':'23000'},40
        with patch.object(self.client,'request',request),patch.object(self.client,'clip',side_effect=AssertionError('same failed encoder')),tempfile.TemporaryDirectory() as tmp:
            result=self.client.screenshot(Path(tmp)/'screen.jpg')
            self.assertEqual((result['source'],result['lossless'],result['frame']),('rgb565',True,12))
            self.assertEqual(Path(result['path']).suffix,'.png')
            with Image.open(result['path']) as image:self.assertEqual(image.getpixel((0,0)),(255,0,0))
        self.assertEqual(calls,['/screenshot','/frame.rgb565'])

    def test_session_reuses_one_http_connection(self):
        with remote.PanelClient(self.client.url, token=TOKEN, persistent=True) as client:
            client.status()
            client.ui()
        self.assertEqual(len(self.connections), 2)
        self.assertEqual(self.connections[0], self.connections[1])

    def test_capture_exposes_transport_phases_without_credentials_or_headers(self):
        with remote.PanelClient(self.client.url, token=TOKEN, persistent=True) as client:
            with tempfile.TemporaryDirectory() as folder:
                captured = client.screenshot(Path(folder)/'capture.jpg')
        phases = captured['transport']
        self.assertEqual(set(phases), {'request_ms', 'headers_ms', 'body_ms'})
        self.assertTrue(all(isinstance(value, (int, float)) and value >= 0 for value in phases.values()))
        self.assertNotIn(TOKEN, json.dumps(captured))

    def test_receive_buffer_is_applied_on_connect_and_reconnect(self):
        for size in (4096, 16384):
            with remote.PanelClient(self.client.url, token=TOKEN, persistent=True,
                                    receive_buffer_bytes=size) as client:
                client.status()
                self.assertGreaterEqual(client.connection.sock.getsockopt(remote.socket.SOL_SOCKET, remote.socket.SO_RCVBUF), size)
                client.connection.close()
                client.status()
                self.assertGreaterEqual(client.connection.sock.getsockopt(remote.socket.SOL_SOCKET, remote.socket.SO_RCVBUF), size)
        for invalid in (0, 4095, 65537, True):
            with self.assertRaises(ValueError):
                remote.PanelClient(self.client.url, token=TOKEN, persistent=True, receive_buffer_bytes=invalid)

    def test_default_receive_window_applies_to_both_transport_modes(self):
        connected = []
        original = remote.PanelHTTPConnection.connect
        def connect(connection):
            original(connection)
            connected.append((connection.receive_buffer_bytes,
                connection.sock.getsockopt(remote.socket.SOL_SOCKET, remote.socket.SO_RCVBUF)))
        with patch.object(remote.PanelHTTPConnection, 'connect', connect):
            for persistent in (False, True):
                with remote.PanelClient(self.client.url, token=TOKEN, persistent=persistent) as client:
                    client.status()
        self.assertEqual(len(connected), 2)
        self.assertTrue(all(requested == 16384 and effective >= 16384 for requested, effective in connected))

    def test_persistent_transport_does_not_follow_redirects(self):
        self.mode = 'redirect'
        with remote.PanelClient(self.client.url, token=TOKEN, persistent=True) as client:
            with self.assertRaises(urllib.error.HTTPError): client.status()
        self.assertEqual(len(self.calls), 1)

    def test_disconnected_input_is_not_resent(self):
        self.mode = 'drop'
        with remote.PanelClient(self.client.url, token=TOKEN, persistent=True) as client:
            with self.assertRaises((OSError, http.client.HTTPException)): client.tap(200, 530, settle=0)
        self.assertEqual(len(self.posts), 1)

    def test_disconnected_read_reconnects_once(self):
        self.mode = 'drop_once'
        with remote.PanelClient(self.client.url, token=TOKEN, persistent=True) as client:
            self.assertEqual(client.status()['page'], 'page_hemmet')
        self.assertEqual(len(self.calls), 2)

    def test_session_stops_before_later_inputs_when_page_guard_fails(self):
        source = io.StringIO('\n'.join(json.dumps(c) for c in (
            {'id': 1, 'op': 'status'},
            {'id': 2, 'op': 'tap', 'x': 200, 'y': 530, 'expect_page': 'page_tavla'},
            {'id': 3, 'op': 'tap', 'x': 200, 'y': 530})))
        output = io.StringIO()
        self.assertEqual(remote.run_session(self.client, source, output), 1)
        results = [json.loads(line) for line in output.getvalue().splitlines()]
        self.assertEqual([r['id'] for r in results], [1, 2])
        self.assertFalse(results[1]['ok'])
        self.assertEqual(self.posts, [])
        self.assertNotIn(TOKEN, output.getvalue())

    def test_inspect_produces_private_images_and_control_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'inspection'
            with remote.PanelClient(self.client.url, token=TOKEN, persistent=True) as client:
                result = client.inspect(folder)
            self.assertTrue(result['consistent'])
            self.assertEqual(result['page'], 'page_hemmet')
            self.assertEqual(json.loads(Path(result['ui_path']).read_text())['widgets'], self.widgets)
            self.assertTrue(Path(result['screenshot']['path']).is_file())
            self.assertEqual(folder.stat().st_mode & 0o777, 0o700)
            for path in folder.iterdir(): self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError): self.client.inspect(folder)

    def test_oversized_session_command_stops_before_any_request(self):
        output = io.StringIO()
        # A large nonblank object is bounded and fails without issuing a network request.
        self.assertEqual(remote.run_session(self.client, io.StringIO('{"op":"' + 'a' * 9000), output), 1)
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
