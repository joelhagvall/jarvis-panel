#!/usr/bin/env python3
"""What Jarvis says without being asked. A thread in the server that looks every 20 seconds. Standard library only.

  reminders   the ones Joel asked for (server/reminders.py), when they are due              reminder
  smoke, leak the smoke alarm or the water sensor goes off; said again every 2 minutes       urgent
  the door    the front door has stood open 10 minutes, once per opening                     notice
  the air     PM2.5 over 35 for 5 minutes; again only after it has been under 25            notice
  laundry     30 minutes before the next slot (Home Assistant's sensor.tvatt_bokning_nasta);
              a slot that starts before 06:30 is said at 21:30 the evening before            notice
  calendar    15 minutes before each event with a time, read from the calendar's MCP server
              every 5 minutes (JARVIS_NOTICE_CALENDAR=0 turns the notice off; the events are
              still read for the panel's calendar card)                                       notice
  the net     when Jarvis has lost the internet for 2 checks in a row, and when it is back   notice

server/notify.py decides where each kind goes (the panel, the phone or nowhere, by the hour and whether Joel is
home). What has been said is kept in ~/.local/share/hallpanel/notices.json (JARVIS_NOTICES_FILE), so a restart
does not say it again. state() gives the server's GET /panel the latest home status, read here anyway.
"""
import json
import os
import re
import socket
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import home_status
import silent_topics
from home_status import ENTITIES, UNKNOWN, parse_time

TZ = ZoneInfo("Europe/Stockholm")
SEEN_FILE = Path(os.environ.get("JARVIS_NOTICES_FILE", "~/.local/share/hallpanel/notices.json")).expanduser()
TICK_S, CALENDAR_S, NET_S = 20, 300, 60
DOOR_MIN, URGENT_REPEAT_S = 10, 120
PM_HIGH, PM_OK, PM_MIN = 35, 25, 5
LAUNDRY_BEFORE, CALENDAR_BEFORE = timedelta(minutes=30), timedelta(minutes=15)
EVENT = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d)-\d\d:\d\d \| ([^|]+?) \| (.+?) \| id (\S+)")
CALENDAR_TEST_EVENT = re.compile(r'(?:Paneltest [0-9a-f]{16}(?: uppdaterad)?|[TU] [0-9a-f]{16})')
NET_HOSTS = (("1.1.1.1", 443), ("9.9.9.9", 443))


def laundry_start(text, now):
    """The start of "Torsdag 22 Okt 06:00–09:00: Gul + Röd" as a datetime in Stockholm, or None."""
    slot = home_status.laundry_slot(text, now)
    return slot[0] if slot else None


def laundry_notice_time(start):
    """When to say it: 30 minutes before, or 21:30 the evening before a slot that starts before 06:30
    (30 minutes before it would fall in the night, 22-06)."""
    if start.hour * 60 + start.minute < 6 * 60 + 30:
        return (start - timedelta(days=1)).replace(hour=21, minute=30)
    return start - LAUNDRY_BEFORE


def calendar_events(agenda):
    """[(start, calendar, title, id)] of the events with a time in the calendar MCP's agenda text."""
    out = []
    for line in (agenda or "").splitlines():
        if m := EVENT.match(line.strip()):
            start = datetime.fromisoformat(f"{m.group(1)}T{m.group(2)}").replace(tzinfo=TZ)
            out.append((start, m.group(3).strip(), m.group(4).strip(), m.group(5)))
    return out


def online():
    for host, port in NET_HOSTS:
        try:
            with socket.create_connection((host, port), timeout=3):
                return True
        except OSError:
            continue
    return False


class Watch:
    def __init__(self, notifier, reminders, home=None, calendar=None, seen_file=SEEN_FILE, on_net_down=None):
        self.notifier, self.reminders, self.home, self.calendar = notifier, reminders, home, calendar
        self.on_net_down = on_net_down  # the server loads Piper then, so the first sentence without the net is quick
        self.seen_file = Path(seen_file)
        try:
            self.seen = json.loads(self.seen_file.read_text())
        except (OSError, ValueError):
            self.seen = {}
        self.states = None              # (time.monotonic(), Home Assistant's states) from the last look
        self.events, self.events_at, self.calendar_started = [], 0.0, False
        self.net_ok, self.net_fails, self.net_down_at, self.net_at = True, 0, None, 0.0
        self.pm_high_since = None
        self.urgent_at = {}             # "smoke"/"water" -> time.monotonic() when it was last said
        self.lock = threading.Lock()
        self.calendar_feed = None

    # -- bookkeeping --------------------------------------------------------------------------------------
    def once(self, key):
        """True the first time a key is seen; the keys are kept 3 days."""
        if key in self.seen:
            return False
        now = time.time()
        self.seen = {k: t for k, t in self.seen.items() if now - t < 3 * 86400}
        self.seen[key] = now
        try:
            self.seen_file.parent.mkdir(parents=True, exist_ok=True)
            self.seen_file.write_text(json.dumps(self.seen))
        except OSError as e:
            print(f"watch: cannot keep {self.seen_file}: {e}", file=sys.stderr)
        return True

    def forget(self, prefix):
        self.seen = {k: t for k, t in self.seen.items() if not k.startswith(prefix)}

    # -- one look -----------------------------------------------------------------------------------------
    def tick(self, now=None):
        """Look at everything once; now (Stockholm) only for tests."""
        now = now or datetime.now(TZ)
        for n, due, text in self.reminders.due(now.astimezone(timezone.utc)):
            if now - due >= timedelta(hours=6):  # preserve the existing expiry policy
                self.reminders.mark_done(n)
                continue
            if not self.reminders.claim(n):
                continue
            try:
                where = self.notifier.say(text, "reminder", now)
                if where in ("panel", "phone"):
                    self.reminders.mark_done(n)
                else:
                    self.reminders.release(n)  # definitely not accepted; the next tick can retry
            except Exception as error:
                # The remote side may already have acted. Keep the durable claim visible, never replay it.
                print(f"watch: reminder {n} delivery unconfirmed ({type(error).__name__})", file=sys.stderr)
        if self.home is not None:
            try:
                states = self.home.states()
                with self.lock:
                    self.states = (time.monotonic(), states)
                self.check_home({s["entity_id"]: s for s in states}, now)
            except Exception as e:      # Home Assistant restarting must not stop the reminders
                print(f"watch: home failed: {e}", file=sys.stderr)
        if self.calendar is not None:
            self.check_calendar(now)
        if time.monotonic() - self.net_at >= NET_S:
            self.net_at = time.monotonic()
            self.check_net(online(), now)

    def check_home(self, by_id, now):
        def state(key):
            return (by_id.get(ENTITIES[key]) or {}).get("state")

        for key, text in (("smoke", "Brandvarnaren larmar! Kolla om det brinner."),
                          ("water", "Vattenlarmet har gått. Det läcker någonstans.")):
            if state(key) == "on":
                last = self.urgent_at.get(key)
                if last is None or time.monotonic() - last >= URGENT_REPEAT_S:
                    self.urgent_at[key] = time.monotonic()
                    self.notifier.say(text, "urgent", now)
            else:
                self.urgent_at.pop(key, None)
        door = by_id.get(ENTITIES["door"])
        if door and door.get("state") == "on":
            since = parse_time(door["last_changed"])
            if now - since >= timedelta(minutes=DOOR_MIN) and self.once(f"door:{door['last_changed']}"):
                self.notifier.say(f"Ytterdörren har stått öppen i {int((now - since).total_seconds() // 60)} minuter.",
                                  "notice", now)
        pm = state("pm25")
        if pm not in UNKNOWN:
            pm = float(pm)
            if pm > PM_HIGH:
                self.pm_high_since = self.pm_high_since or now
                if now - self.pm_high_since >= timedelta(minutes=PM_MIN) and self.once("air:high"):
                    self.notifier.say(f"Luften är dålig, PM2,5 är {home_status.num(pm, 0)}. Vädra gärna.", "notice", now)
            else:
                self.pm_high_since = None
                if pm < PM_OK:
                    self.forget("air:")
        if (laundry := state("laundry")) not in UNKNOWN and (start := laundry_start(laundry, now)):
            at = laundry_notice_time(start)
            if at <= now < start and now - at < timedelta(minutes=30) and self.once(f"laundry:{start.isoformat()}"):
                m = re.search(r"\d\d:\d\d\s*[–-]\s*\d\d:\d\d:?\s*(.*)$", laundry)
                slot = (m.group(1) if m else "").strip().replace(" + ", " och ")      # the rooms, "Gul + Röd"
                when = f"i morgon klockan {start:%H:%M}" if start.date() > now.date() else f"klockan {start:%H:%M}"
                self.notifier.say(f"Du har tvättid {when}" + (f", {slot}." if slot else "."), "notice", now)

    def check_calendar(self, now):
        if self.calendar_feed is None and (time.monotonic() - self.events_at >= CALENDAR_S or not self.events_at):
            self.events_at = time.monotonic()
            try:
                if not self.calendar_started:       # its own MCP session, apart from the agent's
                    self.calendar.start()
                    self.calendar_started = True
                is_error, text = self.calendar.run("agenda", {"days": 2}, timeout=10)
                if not is_error:
                    self.events = calendar_events(text)
            except Exception as e:
                self.calendar_started = False
                print(f"watch: calendar failed: {e}", file=sys.stderr)
        if os.environ.get("JARVIS_NOTICE_CALENDAR", "1") == "0" or not self.calendar_fresh(now):
            return
        for start, _calendar, title, eid in self.events:
            # Joel never wants these mentioned. Keep the events for availability and the calendar card.
            if silent_topics.silent(title) or CALENDAR_TEST_EVENT.fullmatch(title):
                continue
            if start - CALENDAR_BEFORE <= now < start and self.once(f"cal:{eid}:{start.isoformat()}"):
                minutes = max(1, round((start - now).total_seconds() / 60))
                self.notifier.say(f"Om {minutes} minuter: {title}.", "notice", now)

    def check_net(self, ok, now):
        if ok:
            if not self.net_ok and self.net_down_at and now - self.net_down_at >= timedelta(minutes=2):
                self.notifier.say("Nätet är tillbaka.", "notice", now)
            self.net_ok, self.net_fails, self.net_down_at = True, 0, None
            return
        self.net_fails += 1
        if self.net_fails == 2:
            self.net_ok, self.net_down_at = False, now
            if self.on_net_down:
                self.on_net_down()
            self.notifier.say("Nätet är nere. Jag kan inte svara på frågor just nu, men timers, påminnelser och "
                              "larm fungerar.", "notice", now)

    # -- for the server --------------------------------------------------------------------------------------
    def latest_states(self, max_age=60):
        with self.lock:
            if self.states and time.monotonic() - self.states[0] <= max_age:
                return self.states[1]
        return None

    def calendar_fresh(self, now):
        """Bound source age; unchanged successful checks also refresh the feed's success timestamp."""
        if self.calendar_feed is None:
            return bool(self.events_at) and 0 <= time.monotonic() - self.events_at <= 2 * CALENDAR_S
        stamp = self.calendar_feed.status().get("last_success")
        try:
            last = datetime.fromisoformat(stamp)
            if last.tzinfo is None:
                return False
            age = now.timestamp() - last.timestamp()
            return 0 <= age <= 2 * CALENDAR_S
        except (ValueError, TypeError):
            return False

    def run(self):
        while True:
            try:
                self.tick()
            except Exception as e:      # never let one bad look end the thread
                print(f"watch: {e}", file=sys.stderr)
            time.sleep(TICK_S)

    def start(self):
        if self.calendar is not None:
            from calendar_sync import CalendarFeed
            def publish(events):
                with self.lock:
                    self.events = events
                    self.events_at = time.monotonic()
                # This only refreshes the aggregate sensor; no actuator, speech or alarm service.
                import hass
                hass.call_service('homeassistant','update_entity',{'entity_id':'sensor.jarvis_hemmet'},timeout=3)
            self.calendar_feed = CalendarFeed(self.calendar, calendar_events, publish)
            self.calendar_feed.start()
        threading.Thread(target=self.run, daemon=True, name="watch").start()
