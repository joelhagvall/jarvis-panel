"""Read iCloud calendar colours with CalDAV; never change calendars or events.

Uses the existing calendar skill's credentials. Requests run in a separate
thread, so an unavailable iCloud cannot delay panel requests or alarm checks.
Only names/colours are cached locally. No credentials or event text are logged.
"""
import base64
import json
import os
from pathlib import Path
import re
import threading
import time
from urllib.parse import urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
import xml.etree.ElementTree as ET

DAV = 'DAV:'
CAL = 'urn:ietf:params:xml:ns:caldav'
APPLE = 'http://apple.com/ns/ical/'
CREDENTIALS = Path(os.environ.get('CALDAV_CREDENTIALS', '~/.config/icloud-caldav/credentials.json')).expanduser()
CACHE = Path(os.environ.get('JARVIS_CALENDAR_PALETTE', '~/.local/share/hallpanel/calendar-palette.json')).expanduser()


def colour(value):
    """Apple's #RRGGBBAA property -> #RRGGBB; reject malformed metadata."""
    value = str(value or '').strip()
    return value[:7].upper() if re.fullmatch(r'#[0-9a-fA-F]{6}(?:[0-9a-fA-F]{2})?', value) else None


def trusted(url):
    parts = urlsplit(url)
    return (parts.scheme == 'https' and parts.hostname is not None
            and (parts.hostname == 'icloud.com' or parts.hostname.endswith('.icloud.com'))
            and parts.port in (None, 443) and not parts.username and not parts.password)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # never forward account credentials through a redirect


def propfind(url, properties, authorization, depth=0):
    if not trusted(url):
        raise ValueError('untrusted CalDAV host')
    root = ET.Element('{DAV:}propfind')
    prop = ET.SubElement(root, '{DAV:}prop')
    for name in properties:
        ET.SubElement(prop, name)
    req = Request(url, data=ET.tostring(root), method='PROPFIND', headers={
        'Authorization': authorization, 'Depth': str(depth), 'Content-Type': 'application/xml; charset=utf-8'})
    with build_opener(NoRedirect()).open(req, timeout=8) as response:
        data = response.read(1024 * 1024 + 1)
    if len(data) > 1024 * 1024:
        raise ValueError('CalDAV metadata too large')
    return ET.fromstring(data)


def properties(root, with_hrefs=False):
    """Only successful propstat entries; a failed colour property is not a colour."""
    for response in root.findall('{DAV:}response'):
        out = {}
        for stat in response.findall('{DAV:}propstat'):
            if ' 200 ' in (stat.findtext('{DAV:}status') or ''):
                prop = stat.find('{DAV:}prop')
                if prop is not None:
                    out.update({child.tag: child for child in prop})
        yield (response.findtext('{DAV:}href'), out) if with_hrefs else out


def discover(credentials=CREDENTIALS, request=propfind):
    cfg = json.loads(Path(credentials).read_text())
    url = cfg.get('url') or 'https://caldav.icloud.com/'
    if not trusted(url):
        raise ValueError('untrusted CalDAV host')
    auth = 'Basic ' + base64.b64encode(f"{cfg['user']}:{cfg['password']}".encode()).decode()
    def href(base, tag):
        for props in properties(request(base, [tag], auth)):
            node = props.get(tag)
            if node is not None and (value := node.findtext('{DAV:}href')):
                target = urljoin(base, value)
                if trusted(target):
                    return target
        raise ValueError('CalDAV discovery failed')
    principal = href(url, '{DAV:}current-user-principal')
    return href(principal, f'{{{CAL}}}calendar-home-set'), auth


def fetch(credentials=CREDENTIALS, request=propfind):
    home, auth = discover(credentials, request)
    result = {}
    for props in properties(request(home, ['{DAV:}displayname', f'{{{APPLE}}}calendar-color'], auth, depth=1)):
        name, value = props.get('{DAV:}displayname'), props.get(f'{{{APPLE}}}calendar-color')
        if name is not None and name.text and value is not None and (hexvalue := colour(value.text)):
            if name.text in result and result[name.text] != hexvalue:
                raise ValueError('ambiguous calendar names')
            result[name.text] = hexvalue
    if not result:
        raise ValueError('no calendar colours')
    return result


class Palette:
    def __init__(self, credentials=CREDENTIALS, cache=CACHE):
        self.credentials, self.cache = Path(credentials), Path(cache)
        try:
            self.values = {k: v for k, v in json.loads(self.cache.read_text()).items() if colour(v) == v}
        except (OSError, ValueError, AttributeError):
            self.values = {}
        self.next_at, self.busy, self.lock = 0, False, threading.Lock()

    def get(self):
        with self.lock:
            if not self.busy and time.monotonic() >= self.next_at:
                self.busy = True
                threading.Thread(target=self.refresh, name='calendar-colours', daemon=True).start()
            return dict(self.values)

    def refresh(self):
        delay = 300
        try:
            values = fetch(self.credentials)
            self.update(values)
            delay = 3600
        except (OSError, ValueError, KeyError, ET.ParseError):
            pass  # retain last successful palette; no event, URL or secret in logs
        finally:
            with self.lock:
                self.next_at, self.busy = time.monotonic() + delay, False

    def update(self, values):
        """Accept metadata from the shared fast calendar check, without repeated disk writes."""
        values = {str(k): v for k, v in values.items() if colour(v) == v}
        with self.lock:
            if values == self.values:
                return False
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.cache.with_suffix('.tmp')
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, 'w') as out:
                json.dump(values, out, ensure_ascii=False)
            os.replace(tmp, self.cache)
            self.values = values
            return True


PALETTE = Palette()
