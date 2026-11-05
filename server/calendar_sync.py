"""Near-real-time read-only calendar sync on Jarvis, independent of the alarm watcher.

One bounded CalDAV metadata request every 10s; fetch changed calendars through
the existing MCP only. Full two-day refresh every 5min and at day rollover.
No events, credentials, URLs or opaque sync tokens enter status/log output.
"""
from datetime import datetime
import hashlib
from pathlib import Path
import re
import threading
import time
from urllib.error import HTTPError
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

import calendar_palette as palette

POLL_S, FULL_S = 10, 300
TZ = ZoneInfo('Europe/Stockholm')
CTAG = '{http://calendarserver.org/ns/}getctag'


class Metadata:
    def __init__(self, credentials=palette.CREDENTIALS, request=palette.propfind):
        self.credentials, self.request = Path(credentials), request
        self.home, self.auth, self.fingerprint, self.denied = None, None, None, None
        self.expires = 0
        self.refused = False
        self.denied_until, self.denials = 0, 0

    def poll(self):
        fingerprint = hashlib.sha256(self.credentials.read_bytes()).digest()
        if fingerprint == self.denied and time.monotonic() < self.denied_until:
            self.refused = True
            raise PermissionError('Calendar sign-in was refused; waiting before retry')
        if fingerprint != self.denied:
            self.denials = 0
        try:
            if fingerprint != self.fingerprint or time.monotonic() >= self.expires:
                self.home, self.auth = palette.discover(self.credentials, self.request)
                self.fingerprint, self.expires = fingerprint, time.monotonic()+3600
            root = self.request(self.home, ['{DAV:}displayname', '{DAV:}resourcetype',
                '{DAV:}sync-token', CTAG, f'{{{palette.APPLE}}}calendar-color'], self.auth, depth=1)
        except HTTPError as error:
            if error.code in (401, 403):
                self.denied = fingerprint
                self.refused = True
                self.denied_until = time.monotonic() + min(3600, 300 * 2**min(self.denials, 4))
                self.denials += 1
            self.expires = 0
            raise
        result, names = {}, set()
        self.refused = False
        self.denied, self.denials, self.denied_until = None, 0, 0
        for href, props in palette.properties(root, with_hrefs=True):
            resource = props.get('{DAV:}resourcetype')
            if resource is None or resource.find(f'{{{palette.CAL}}}calendar') is None:
                continue
            name = props.get('{DAV:}displayname')
            if not href or name is None or not name.text:
                raise ValueError('Incomplete calendar metadata')
            url = urljoin(self.home, href)
            if not palette.trusted(url) or name.text in names:
                raise ValueError('Ambiguous or untrusted calendar metadata')
            names.add(name.text)
            token = props.get('{DAV:}sync-token')
            if token is None or not token.text:
                token = props.get(CTAG)
            colour = props.get(f'{{{palette.APPLE}}}calendar-color')
            result[url] = {'name': name.text, 'token': token.text if token is not None else None,
                          'colour': palette.colour(colour.text) if colour is not None else None}
        if not result and not any('{DAV:}resourcetype' in p for p in palette.properties(root)):
            raise ValueError('No calendar collections in metadata')
        return result


class CalendarFeed:
    def __init__(self, reader, parse, publish, metadata=None, palette_store=palette.PALETTE):
        self.reader, self.parse, self.publish = reader, parse, publish
        self.metadata = metadata if metadata is not None else Metadata()
        self.palette = palette_store
        self.events, self.previous = [], None
        self.started, self.loaded, self.pending = False, False, False
        self.full_at, self.opaque_at, self.meta_at = -FULL_S, -30, 0
        self.day, self.failures, self.meta_failures = None, 0, 0
        self.lock, self.stop_event = threading.Lock(), threading.Event()
        self.health = {'revision':0, 'metadata_checks':0, 'full_reads':0, 'scoped_reads':0,
                       'pushes':0, 'last_error':None, 'last_success':None, 'calendar_count':0,
                       'token_changes':0}
        self._status = dict(self.health)

    def _read(self, name=None):
        if not self.started:
            self.reader.start()
            self.started = True
        args = {'days':2}
        if name is not None:
            args['calendar'] = name
        error, text = self.reader.run('agenda', args, timeout=10)
        if error or text.startswith(('FAILED:', 'REFUSED:', 'STILL WORKING')) or '\n... and ' in text:
            raise RuntimeError('Calendar agenda read failed')
        events = self.parse(text)
        if not events and text.strip() != 'Nothing in that period.' and not re.search(r'^\d{4}-\d\d-\d\d(?: heldag)? \| [^|]+ \| .+ \| id \S+',text,re.M):
            raise ValueError('Unexpected calendar agenda format')
        if name is not None and any(e[1] != name for e in events):
            raise ValueError('Calendar agenda scope mismatch')
        return events

    def status(self):
        with self.lock:
            return {**self._status, 'poll_seconds':POLL_S, 'full_refresh_seconds':FULL_S}

    def step(self, monotonic=None, now=None):
        try:
            self._step(monotonic, now)
        finally:
            with self.lock:
                self._status = dict(self.health)

    def _step(self, monotonic=None, now=None):
        at = time.monotonic() if monotonic is None else monotonic
        now = now or datetime.now(TZ)
        new_meta, changed, removed = None, set(), set()
        if at >= self.meta_at:
            try:
                new_meta = self.metadata.poll()
                self.meta_failures = 0
                self.meta_at = at+POLL_S
                self.health['metadata_checks'] += 1
                old = self.previous or {}
                changed = {item['name'] for url,item in new_meta.items() if old.get(url) != item}
                removed = {item['name'] for url,item in old.items()
                           if url not in new_meta or new_meta[url]['name'] != item['name']}
            except Exception as error:
                self.meta_failures += 1
                self.meta_at = at+min(60,POLL_S*2**min(self.meta_failures-1,3))
                self.health['last_error'] = type(error).__name__
        # A changed token with the same agenda is an edit to alarms or notes: the calendar page re-reads them.
        meta_changed = bool(changed or removed)
        full = not self.loaded or at-self.full_at >= FULL_S or self.day != now.date()
        if getattr(self.metadata, 'refused', False):
            return
        if self.failures and at < self.retry_at:
            return
        try:
            if full:
                events = self._read()
                self.health['full_reads'] += 1
            else:
                # Missing change tokens use a bounded 30s fallback for those calendars only.
                if new_meta is not None and at-self.opaque_at >= 30:
                    changed |= {item['name'] for item in new_meta.values() if not item['token']}
                    self.opaque_at = at
                if not changed and not removed:
                    events = self.events
                else:
                    events = [e for e in self.events if e[1] not in changed | removed]
                    for name in sorted(changed):
                        events.extend(self._read(name))
                        self.health['scoped_reads'] += 1
            events = sorted(events, key=lambda e:(e[0],e[1],e[3]))
            colours_changed = False
            if new_meta is not None:
                colours = {v['name']:v['colour'] for v in new_meta.values() if v['colour']}
                colours_changed = self.palette.update(colours)
            if not self.loaded or events != self.events or colours_changed:
                self.events, self.pending = events, True
                self.health['revision'] += 1
            if new_meta is not None:
                self.previous = new_meta  # advance tokens only after all reads succeeded
                if meta_changed:
                    self.health['token_changes'] += 1
                self.health['calendar_count'] = len(new_meta)
            self.loaded, self.day = True, now.date()
            if full:
                self.full_at = at
                self.opaque_at = at
            if new_meta is not None or full or changed or removed:
                self.health['last_success'] = now.isoformat()
            if not self.meta_failures:
                self.health['last_error'] = None
            if self.pending:
                # publish updates the event snapshot before HA calls back into GET /panel.
                self.publish(list(self.events))
                self.pending = False
                self.health['pushes'] += 1
            self.failures = 0
        except Exception as error:
            self.started = False
            self.failures += 1
            self.retry_at = at+min(60,POLL_S*2**min(self.failures-1,3))
            self.health['last_error'] = type(error).__name__

    def run(self):
        while not self.stop_event.is_set():
            self.step()
            self.stop_event.wait(POLL_S)

    def start(self):
        threading.Thread(target=self.run, daemon=True, name='calendar-sync').start()
