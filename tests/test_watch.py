#!/usr/bin/env python3
"""What Jarvis says unasked: the watch (server/watch.py) with a fake home, calendar and notifier, and where
server/notify.py sends each kind (panel, phone, nowhere) with a fake Home Assistant. No network.
Run: python3 -m unittest tests/test_watch.py"""
import json
import os
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import URLError

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
sys.path.insert(0, str(ROOT / "server" / "agents"))
import hass  # noqa: E402
import notify  # noqa: E402
import reminders as rem  # noqa: E402
import watch  # noqa: E402
from home_status import ENTITIES  # noqa: E402

TZ = watch.TZ
NOW = datetime(2026, 10, 22, 16, 0, tzinfo=TZ)


def st(key, value, changed="2026-10-22T13:00:00+00:00"):
    return {"entity_id": ENTITIES[key], "state": value, "attributes": {}, "last_changed": changed}


class FakeNotifier:
    def __init__(self):
        self.said = []

    def say(self, text, kind="notice", now=None):
        self.said.append((kind, text))
        return "panel"


class FakeHome:
    def __init__(self, states):
        self.states_now = states

    def states(self):
        return self.states_now


class FakeCalendar:
    def __init__(self, text):
        self.text, self.calls = text, 0

    def start(self):
        return []

    def run(self, name, args, timeout=60):
        self.calls += 1
        return False, self.text


class WatchTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="hallpanel-watch-"))
        self.n = FakeNotifier()
        self.r = rem.Reminders(self.tmp / "h.db")
        self.addCleanup(self.r.db.close)
        self.home = FakeHome([])

    def watcher(self, calendar=None):
        w = watch.Watch(self.n, self.r, home=self.home, calendar=calendar, seen_file=self.tmp / "seen.json")
        w.net_at = time.monotonic() + 3600      # no real internet check in the tests
        return w

    def test_reminder_said_once_when_due(self):
        self.r.add(NOW + timedelta(minutes=5), "Dags att ta ut tvätten.")
        w = self.watcher()
        w.tick(NOW)
        self.assertEqual(self.n.said, [])
        w.tick(NOW + timedelta(minutes=5))
        w.tick(NOW + timedelta(minutes=6))
        self.assertEqual(self.n.said, [("reminder", "Dags att ta ut tvätten.")])

    def test_old_reminder_is_dropped(self):
        self.r.add(NOW - timedelta(hours=7), "För länge sedan.")
        self.watcher().tick(NOW)
        self.assertEqual(self.n.said, [])
        self.assertEqual(self.r.pending(), [])

    def test_undelivered_reminder_survives_restart_and_retries_after_recovery(self):
        n = self.r.add(NOW, "Dags nu.")
        self.n.say = Mock(return_value=None)  # no route accepted the delivery
        self.watcher().tick(NOW)
        self.assertEqual([r[0] for r in self.r.pending()], [n])
        again = rem.Reminders(self.tmp / "h.db")
        self.addCleanup(again.db.close)
        w = self.watcher(); w.reminders = again
        self.n.say = Mock(return_value="panel")
        w.tick(NOW + timedelta(seconds=20))
        self.n.say.assert_called_once()
        self.assertEqual(again.pending(), [])

    def test_ambiguous_reminder_stays_visible_without_replaying_after_restart(self):
        n = self.r.add(NOW, "Dags nu.")
        self.n.say = Mock(side_effect=TimeoutError("response lost after send"))
        self.watcher().tick(NOW)
        self.assertEqual([r[0] for r in self.r.pending()], [n])
        self.assertIn("Leverans ej bekräftad", self.r.lines(NOW)[0])
        self.watcher().tick(NOW + timedelta(seconds=20))
        self.n.say.assert_called_once()

    def test_failure_in_one_reminder_does_not_block_the_next(self):
        self.r.add(NOW, "Första."); self.r.add(NOW, "Andra.")
        self.n.say = Mock(side_effect=[TimeoutError("unknown delivery"), "panel"])
        self.watcher().tick(NOW)
        self.assertEqual(self.n.say.call_count, 2)
        self.assertEqual([r[2] for r in self.r.pending()], ["Första."])

    def test_stale_calendar_does_not_announce_removed_or_unverified_events(self):
        w=self.watcher(FakeCalendar(''))
        w.events=watch.calendar_events('2026-10-22 16:10-16:15 | Jobb | Möte | id a')
        w.calendar_feed=SimpleNamespace(status=lambda:{'last_success':(NOW-timedelta(hours=1)).isoformat()})
        w.check_calendar(NOW)
        self.assertEqual(self.n.said,[])
        w.calendar_feed=SimpleNamespace(status=lambda:{'last_success':NOW.isoformat()})
        w.check_calendar(NOW)
        self.assertEqual(len(self.n.said),1)

    def test_door_once_per_opening(self):
        self.home.states_now = [st("door", "on", "2026-10-22T13:55:00+00:00")]
        w = self.watcher()
        w.tick(NOW)                                  # open 5 minutes: nothing yet
        w.tick(NOW + timedelta(minutes=6))
        w.tick(NOW + timedelta(minutes=7))
        self.assertEqual(self.n.said, [("notice", "Ytterdörren har stått öppen i 11 minuter.")])
        self.home.states_now = [st("door", "on", "2026-10-22T14:30:00+00:00")]    # closed and opened again
        w.tick(NOW + timedelta(minutes=41))
        self.assertEqual(len(self.n.said), 2)
        # a restart does not say the same opening again
        self.watcher().tick(NOW + timedelta(minutes=42))
        self.assertEqual(len(self.n.said), 2)

    def test_smoke_is_repeated_every_two_minutes(self):
        self.home.states_now = [st("smoke", "on")]
        w = self.watcher()
        w.tick(NOW)
        w.tick(NOW)
        self.assertEqual(self.n.said, [("urgent", "Brandvarnaren larmar! Kolla om det brinner.")])
        w.urgent_at["smoke"] -= watch.URGENT_REPEAT_S
        w.tick(NOW)
        self.assertEqual(len(self.n.said), 2)
        self.home.states_now = [st("smoke", "off")]
        w.tick(NOW)
        self.home.states_now = [st("smoke", "on"), st("water", "on")]
        w.tick(NOW)
        self.assertEqual([k for k, _ in self.n.said], ["urgent"] * 4)

    def test_bad_air_after_five_minutes_and_again_only_after_clean(self):
        w = self.watcher()
        for minutes, pm in ((0, "40"), (4, "41"), (5, "42"), (10, "45"), (11, "30"), (12, "40"), (20, "20"),
                            (21, "40"), (26, "40")):
            self.home.states_now = [st("pm25", pm)]
            w.tick(NOW + timedelta(minutes=minutes))
        self.assertEqual(self.n.said, [("notice", "Luften är dålig, PM2,5 är 42. Vädra gärna."),
                                       ("notice", "Luften är dålig, PM2,5 är 40. Vädra gärna.")])

    def test_laundry_half_an_hour_before_or_the_evening_before(self):
        start = watch.laundry_start("Torsdag 22 Okt 17:00–20:00: Gul + Röd", NOW)
        self.assertEqual(start, datetime(2026, 10, 22, 17, 0, tzinfo=TZ))
        self.assertEqual(watch.laundry_notice_time(start), datetime(2026, 10, 22, 16, 30, tzinfo=TZ))
        early = watch.laundry_start("Fredag 23 Okt 06:00–09:00: Gul", NOW)
        self.assertEqual(watch.laundry_notice_time(early), datetime(2026, 10, 22, 21, 30, tzinfo=TZ))
        self.assertEqual(watch.laundry_start("Januari 3 Jan 07:00–10:00", datetime(2026, 12, 30, tzinfo=TZ)).year, 2027)
        self.assertIsNone(watch.laundry_start("Ingen bokning", NOW))
        self.home.states_now = [st("laundry", "Torsdag 22 Okt 17:00–20:00: Gul + Röd")]
        w = self.watcher()
        w.tick(NOW)
        w.tick(NOW + timedelta(minutes=31))
        w.tick(NOW + timedelta(minutes=35))
        self.assertEqual(self.n.said, [("notice", "Du har tvättid klockan 17:00, Gul och Röd.")])
        self.home.states_now = [st("laundry", "Fredag 23 Okt 06:00–09:00: Gul")]
        w.tick(datetime(2026, 10, 22, 21, 31, tzinfo=TZ))
        self.assertEqual(self.n.said[-1], ("notice", "Du har tvättid i morgon klockan 06:00, Gul."))

    def test_calendar_quarter_of_an_hour_before(self):
        agenda = ("2026-10-22 16:20-16:35 | Att göra | Handla pb stor | id ABC\n"
                  "2026-10-22 heldag | Annat | Namnsdag | id DAY\n"
                  "2026-10-22 19:00-20:00 | Jobb | lönerapportera | id XYZ | repeats, occurrence 2026-10-22")
        self.assertEqual([e[2] for e in watch.calendar_events(agenda)], ["Handla pb stor", "lönerapportera"])
        cal = FakeCalendar(agenda)
        w = self.watcher(cal)
        w.tick(NOW)                                 # 20 minutes before: nothing
        w.tick(NOW + timedelta(minutes=6))
        w.tick(NOW + timedelta(minutes=7))
        self.assertEqual(self.n.said, [("notice", "Om 14 minuter: Handla pb stor.")])
        self.assertEqual(cal.calls, 1)              # read every 5 minutes, not every look

    def test_silent_topic_events_stay_in_calendar_but_are_not_announced(self):
        agenda = ("2026-10-22 16:10-16:15 | Att göra | Dammsug-påminnelse | id A\n"
                  "2026-10-22 16:10-16:15 | Att göra | Dra fram DAMMSUGAREN | id B\n"
                  "2026-10-22 16:10-16:15 | Att göra | Vacuum the hall | id C\n"
                  "2026-10-22 16:10-16:15 | Jobb | Lönerapportering | id D")
        w = self.watcher(FakeCalendar(agenda))
        with patch.dict(os.environ, {"JARVIS_SILENT_TOPICS": r"dammsug\w*|vacuum\w*"}):
            w.tick(NOW)
            w.tick(NOW + timedelta(minutes=1))
        self.assertEqual(len(w.events), 4)
        self.assertEqual(self.n.said, [("notice", "Om 10 minuter: Lönerapportering.")])
        self.assertEqual(len(w.seen), 1)

    def test_live_probe_namespace_is_visible_but_never_announced(self):
        for title in ('Paneltest 0123456789abcdef','Paneltest 0123456789abcdef uppdaterad',
                      'T 0123456789abcdef','U 0123456789abcdef'):
            with self.subTest(title=title):
                w=self.watcher(FakeCalendar('2026-10-22 16:05-16:10 | Att göra | '+title+' | id probe'))
                w.tick(NOW)
                self.assertEqual(len(w.events),1)
                self.assertEqual(self.n.said,[])

    def test_threaded_calendar_does_not_block_alarm_tick(self):
        cal=FakeCalendar('Nothing in that period.')
        w=self.watcher(cal);w.calendar_feed=SimpleNamespace(status=lambda:{'last_success':NOW.isoformat()})
        self.home.states_now=[st('smoke','on')]
        w.tick(NOW)
        self.assertEqual(cal.calls,0)
        self.assertEqual(self.n.said,[('urgent','Brandvarnaren larmar! Kolla om det brinner.')])

    def test_net_down_after_two_checks_and_back(self):
        downs = []
        w = watch.Watch(self.n, self.r, seen_file=self.tmp / "seen.json", on_net_down=lambda: downs.append(1))
        w.check_net(False, NOW)
        self.assertEqual(self.n.said, [])
        w.check_net(False, NOW + timedelta(minutes=1))
        self.assertFalse(w.net_ok)
        self.assertEqual(downs, [1])
        self.assertIn("Nätet är nere", self.n.said[0][1])
        w.check_net(False, NOW + timedelta(minutes=2))
        self.assertEqual(len(self.n.said), 1)
        w.check_net(True, NOW + timedelta(minutes=4))
        self.assertEqual(self.n.said[-1], ("notice", "Nätet är tillbaka."))
        self.assertTrue(w.net_ok)


class FakeHomeAssistant(BaseHTTPRequestHandler):
    """person.joel from server.person, other states from server.states, and every service call recorded in
    server.calls."""
    def do_GET(self):
        state = self.server.states.get(self.path.rsplit("/", 1)[-1], self.server.person)
        body = json.dumps({"entity_id": self.path.rsplit("/", 1)[-1], "state": state}).encode()
        self.send_response(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        data = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))))
        self.server.calls.append((self.path, data))
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"[]")

    def log_message(self, *args):
        pass


class History:
    def __init__(self):
        self.rows = []

    def add(self, cid, role, text, ok=True, meta=None):
        self.rows.append((cid, role, text))


class NotifyTest(unittest.TestCase):
    def setUp(self):
        self.ha = ThreadingHTTPServer(("127.0.0.1", 0), FakeHomeAssistant)
        self.ha.person, self.ha.calls, self.ha.states = "home", [], {}
        threading.Thread(target=self.ha.serve_forever, daemon=True).start()
        self.addCleanup(self.ha.shutdown)
        tmp = Path(tempfile.mkdtemp(prefix="hallpanel-notify-"))
        (tmp / "token").write_text("t\n")
        self.saved_env = os.environ.get("HA_TOKEN_FILE")
        os.environ["HA_TOKEN_FILE"] = str(tmp / "token")
        hass.URL = f"http://127.0.0.1:{self.ha.server_port}"
        hass.reset()
        self.addCleanup(self.restore)
        self.history = History()
        self.n = notify.Notifier(self.history)
        self.n.panel, self.n.phone = "assist_satellite.hallpanel", "mobile_app_test"
        self.day, self.night = NOW, NOW.replace(hour=23)

    def restore(self):
        hass.URL = None
        hass.reset()
        if self.saved_env is None:
            os.environ.pop("HA_TOKEN_FILE", None)
        else:
            os.environ["HA_TOKEN_FILE"] = self.saved_env

    def paths(self):
        return [p for p, _ in self.ha.calls]

    def test_night_hours(self):
        self.assertTrue(notify.is_night(NOW.replace(hour=23)))
        self.assertTrue(notify.is_night(NOW.replace(hour=5)))
        self.assertFalse(notify.is_night(NOW.replace(hour=6)))
        self.assertFalse(notify.is_night(NOW.replace(hour=21), "22-7"))
        self.assertTrue(notify.is_night(NOW.replace(hour=14), "13-15"))
        self.assertTrue(notify.is_night(NOW.replace(hour=6, minute=10), "22:30-6:15"))
        self.assertFalse(notify.is_night(NOW.replace(hour=6, minute=15), "22:30-6:15"))

    def test_night_follows_the_panels_times_in_home_assistant(self):
        self.ha.states = {"time.hallpanel_natt_borjar": "23:00:00", "time.hallpanel_natt_slutar": "07:30:00"}
        self.assertEqual(notify.quiet_hours(), (23 * 60, 7 * 60 + 30))
        self.assertFalse(notify.is_night(NOW.replace(hour=22, minute=30)))
        self.assertTrue(notify.is_night(NOW.replace(hour=7, minute=10)))
        self.ha.states["time.hallpanel_natt_slutar"] = "unavailable"          # falls back to 22-6
        self.assertEqual(notify.quiet_hours(), (22 * 60, 6 * 60))

    def test_notice_on_the_panel_by_day_only_when_home(self):
        self.assertEqual(self.n.say("Om 15 minuter: Standup.", "notice", self.day), "panel")
        self.assertEqual(self.ha.calls[0], ("/api/services/assist_satellite/announce",
                                            {"entity_id": "assist_satellite.hallpanel",
                                             "message": "Om 15 minuter: Standup.", "preannounce": True}))
        self.assertEqual(self.history.rows, [("notice", "jarvis", "Om 15 minuter: Standup.")])
        self.assertIsNone(self.n.say("Nätet är tillbaka.", "notice", self.night))
        self.ha.person = "not_home"
        self.assertIsNone(self.n.say("Ytterdörren har stått öppen i 11 minuter.", "notice", self.day))
        self.assertEqual(len(self.ha.calls), 1)

    def test_reminder_panel_when_home_phone_when_away(self):
        self.assertEqual(self.n.say("Ring mamma.", "reminder", self.night), "panel")
        self.ha.person = "not_home"
        self.assertEqual(self.n.say("Ring mamma.", "reminder", self.day), "phone")
        self.assertEqual(self.ha.calls[-1], ("/api/services/notify/mobile_app_test",
                                             {"message": "Ring mamma.", "title": "Påminnelse"}))
        self.ha.person = "home"
        self.n.panel = None                          # the panel is not adopted yet: the phone
        self.assertEqual(self.n.say("Ring mamma.", "reminder", self.day), "phone")

    def test_urgent_on_the_panel_at_night_and_never_pushed(self):
        self.assertEqual(self.n.say("Brandvarnaren larmar!", "urgent", self.night), "panel")
        self.assertEqual(self.paths(), ["/api/services/assist_satellite/announce"])

    def test_ambiguous_panel_delivery_never_falls_back_to_a_second_notification(self):
        self.n.home=Mock(return_value=True)
        with patch.object(hass,"call_service",side_effect=TimeoutError("accepted but reply lost")) as call:
            with self.assertRaises(notify.DeliveryUnconfirmed):
                self.n.say("Dags nu.","reminder",self.day)
        self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.args[0],"assist_satellite")

    def test_refused_connection_is_known_not_to_have_delivered(self):
        with patch.object(hass,"call_service",side_effect=URLError(ConnectionRefusedError())):
            self.assertTrue(self.n.announce("Dags nu.")[0])
            self.assertTrue(self.n.push("Dags nu.")[0])

    def test_phone_tool_only_when_asked_or_after_a_yes(self):
        self.n.said, self.n.last_reply = "vad ska jag laga till middag", ""
        self.assertTrue(self.n.phone_tool({"text": "Recept"})[0])
        self.n.said = "skicka receptet till telefonen"
        self.assertEqual(self.n.phone_tool({"text": "Recept", "title": "Pasta", "url": "https://example.com/r"}),
                         (False, "Notisen är skickad till telefonen."))
        self.assertEqual(self.ha.calls[-1][1], {"message": "Recept", "title": "Pasta",
                                                "data": {"url": "https://example.com/r"}})
        self.n.said, self.n.last_reply = "ja gärna", "Ska jag skicka hela receptet till telefonen?"
        self.assertFalse(self.n.phone_tool({"text": "Recept"})[0])
        self.n.said, self.n.last_reply = "ja", "Vill du ha mer kaffe?"
        self.assertTrue(self.n.phone_tool({"text": "Recept"})[0])
        self.n.said = "skicka länken"
        self.assertTrue(self.n.phone_tool({"text": "x", "url": "javascript:alert(1)"})[0])
        self.assertEqual(len(self.ha.calls), 2)

    def test_phone_gets_the_calendar_event_instead_of_the_plan_twice(self):
        self.n.said, self.n.last_reply = "boka in det och skicka planen till telefonen", ""
        event = {"calendar": "Annat", "title": "Utflykt: Tekniska museet", "start": "2026-10-24 11:18",
                 "end": "2026-10-24 14:40", "location": "Museivägen 7",
                 "notes": "• Besök 12:00–14:30\n• Gå 11:18 · linje 10 avgår 11:23", "url": "https://tekniskamuseet.se"}
        self.assertEqual(self.n.phone_tool({"from_calendar": True, "event": event}),
                         (False, "Notisen är skickad till telefonen."))
        self.assertEqual(self.ha.calls[-1][1], {
            "message": "lördag 24 oktober 11:18–14:40\nMuseivägen 7\n• Besök 12:00–14:30\n• Gå 11:18 · linje 10 avgår 11:23",
            "title": "Utflykt: Tekniska museet", "data": {"url": "https://tekniskamuseet.se"}})
        self.assertTrue(self.n.phone_tool({"from_calendar": True})[0])          # no event in the same step
        self.n.said = "ja"                                                       # a yes to the planned outing
        self.n.last_reply = "Ska jag lägga in den i kalendern och skicka planen till mobilen?"
        self.assertFalse(self.n.phone_tool({"from_calendar": True, "event": event})[0])
        self.assertEqual(notify.when("2026-10-25"), "söndag 25 oktober")
        self.assertEqual(notify.when("i morgon"), "i morgon")

    def test_no_phone_no_tool(self):
        self.n.phone = None
        self.assertEqual(self.n.tools(), [])
        self.assertTrue(self.n.push("x")[0])

    def test_english_phone_request_and_yes_to_offer(self):
        self.n.said, self.n.last_reply = "What's for dinner?", ""
        self.assertTrue(self.n.phone_tool({"text": "Recipe"})[0])
        self.n.said = "Send that to my phone"
        self.assertFalse(self.n.phone_tool({"text": "Recipe"})[0])
        self.n.said, self.n.last_reply = "Yes please", "Shall I send the recipe to your phone?"
        self.assertFalse(self.n.phone_tool({"text": "Recipe"})[0])
        self.n.said, self.n.last_reply = "Yes", "Would you like more coffee?"
        self.assertTrue(self.n.phone_tool({"text": "Recipe"})[0])
        self.assertEqual(len(self.ha.calls), 2)


if __name__ == "__main__":
    unittest.main()
