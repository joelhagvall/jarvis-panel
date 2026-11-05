#!/usr/bin/env python3
"""Home Assistant on Jarvis for every part of the server: one token, and one place that stops at a refusal.
Standard library only.

Home Assistant bans an address after five refused logins (ip_ban, login_attempts_threshold 5), 127.0.0.1 too,
and a request with a refused token counts as one. A ban would cut off more than the panel: everything else on
Jarvis that talks to Home Assistant locally, such as the alert pushes. So the first 401 or 403 stops every
call from this process, the home status, the radio, the notices and the voice alike, until the token file
holds another token. Callers get Refused, a PermissionError, so their `except OSError` still catches it.

  HA_URL                                   default http://127.0.0.1:8123
  HA_TOKEN_FILE or JARVIS_HOME_TOKEN_FILE  the long-lived token, default ~/.config/homeassistant/token
"""
import json
import os
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

URL = None                      # tests point this at a fake Home Assistant; otherwise HA_URL
_refused, _lock = [None], threading.Lock()   # the token Home Assistant refused


class Refused(PermissionError):
    """No token, or Home Assistant refused it once already."""


def base():
    return (URL or os.environ.get("HA_URL") or "http://127.0.0.1:8123").rstrip("/")


def token_file():
    return Path(os.environ.get("HA_TOKEN_FILE") or os.environ.get("JARVIS_HOME_TOKEN_FILE")
                or "~/.config/homeassistant/token").expanduser()


def token():
    """The token, or None when there is no token file. Read on every call, so a new token works at once."""
    try:
        return token_file().read_text().strip() or None
    except OSError:
        return None


def refused():
    """True when calls are stopped: no token, or the one in the file was refused."""
    t = token()
    return t is None or t == _refused[0]


def request(path, data=None, headers=None, method=None, timeout=10):
    """The body of a request to Home Assistant. data is bytes, or anything else, sent as JSON."""
    t = token()
    if t is None:
        raise Refused(f"no Home Assistant token in {token_file()}")
    if t == _refused[0]:
        raise Refused("Home Assistant refused this token; not asking again")
    h = {"Authorization": f"Bearer {t}", **(headers or {})}
    if data is not None and not isinstance(data, bytes):
        data, h = json.dumps(data).encode(), {"Content-Type": "application/json", **h}
    req = urllib.request.Request(base() + path, data=data, headers=h,
                                 method=method or ("POST" if data is not None else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            with _lock:
                if _refused[0] != t:
                    _refused[0] = t
                    print(f"hass: Home Assistant refused the token ({e.code}); no more calls until the token "
                          f"in {token_file()} changes", file=sys.stderr)
            raise Refused(f"Home Assistant refused the token ({e.code})") from e
        raise


def get_json(path, timeout=10):
    body = request(path, timeout=timeout)
    return json.loads(body) if body else None


def call_service(domain, service, data, timeout=10):
    """Home Assistant's POST /api/services/<domain>/<service>."""
    return request(f"/api/services/{domain}/{service}", data, timeout=timeout)


def reset():
    """Forget a refusal (tests)."""
    _refused[0] = None
