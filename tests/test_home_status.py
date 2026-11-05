#!/usr/bin/env python3
"""The home status Jarvis reads (server/agents/home_status.py), without the network:
Home Assistant's states, SMHI's forecast and SL's departures as fixed examples, and the agent's
`hemmet` tool and warm-up snapshot with a fake Home.

  python3 -m unittest tests/test_home_status.py
"""
import json
import os
import sys
import time
import unittest
from threading import Event, Thread
from unittest.mock import patch
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import home_status  # noqa: E402
import openai_agent  # noqa: E402

TZ = home_status.TZ
NOW = datetime(2026, 10, 21, 19, 50, tzinfo=TZ)
E = home_status.ENTITIES


class InlineThread:
    """Drive refresh completion deterministically for cache age tests."""
    def __init__(self, *, target, **kwargs):self.target=target
    def start(self):self.target()


class SLRefreshTest(unittest.TestCase):
    def test_watch_and_both_voice_agents_share_the_production_cache(self):
        with patch.object(home_status.Home,'_shared',None), \
                patch.dict(os.environ,{'JARVIS_HOME_TOKEN_FILE':'configured'}):
            watch,agent,fallback=(home_status.Home.from_env() for _ in range(3))
            self.assertIs(watch,agent);self.assertIs(watch,fallback)
            with patch.dict(os.environ,{'JARVIS_HOME_TOKEN_FILE':''}):
                self.assertIsNone(home_status.Home.from_env())

    def test_inflight_fetch_never_hides_valid_rows_or_starts_another_voice_fetch(self):
        home=home_status.Home()
        data={'departures':[{'direction_code':1,'line':{'designation':'10'},
              'destination':'Hjulsta','expected':'2026-10-21T19:58:00+02:00'}]}
        home.cache[home_status.SL]=(time.monotonic()-home_status.SL_STALE_S-5,data)
        entered,release=Event(),Event();threads=[]
        def fetch(*args,**kwargs):
            entered.set()
            if not release.wait(2):raise TimeoutError('test worker was not released')
            return data
        def thread(**kwargs):
            worker=Thread(**kwargs);threads.append(worker);return worker
        with patch.object(home_status,'get_json',side_effect=fetch) as network, \
                patch.object(home_status,'Thread',side_effect=thread) as workers:
            try:
                self.assertIn('Hjulsta',home.metro(NOW,3)['sl0'])
                self.assertTrue(entered.wait(1));self.assertFalse(release.is_set())
                for _ in range(5):
                    self.assertEqual(home.metro(NOW,3)['sl_walk'],'SL uppdateras')
                    self.assertIs(home._cached(home_status.SL,20),data)
                self.assertEqual(network.call_count,1);self.assertEqual(workers.call_count,1)
            finally:
                release.set()
                for worker in threads:worker.join(2)
        self.assertEqual(home.metro(NOW,3)['sl_walk'],'3 min att gå')

    @patch.object(home_status,'Thread',InlineThread)
    def test_rate_limit_honors_retry_after_without_hammering_or_erasing_cache(self):
        home=home_status.Home()
        data={'departures':[{'direction_code':1,'line':{'designation':'10'},
              'destination':'Hjulsta','expected':'2026-10-21T19:58:00+02:00'}]}
        home.cache[home_status.SL]=(90,data)
        error=home_status.urllib.error.HTTPError(home_status.SL,429,'busy',{'Retry-After':'120'},None)
        with patch.object(home_status.time,'monotonic',return_value=100) as clock, \
                patch.object(home_status,'get_json',side_effect=error) as network:
            self.assertIn('Hjulsta',home.metro(NOW,3)['sl0'])
            for at in (101,160,219):
                clock.return_value=at;home.metro(NOW,3)
            self.assertEqual(network.call_count,1)
            clock.return_value=220;home.metro(NOW,3)
            self.assertEqual(network.call_count,2)

    @patch.object(home_status,'Thread',InlineThread)
    def test_bad_or_spurious_empty_response_preserves_a_board_with_trains(self):
        # An empty success while the board still has trains was SL flakiness on 2026-10-26, not an empty platform.
        home=home_status.Home()
        data={'departures':[{'direction_code':1,'line':{'designation':'10'},
              'destination':'Hjulsta','expected':'2026-10-21T19:58:00+02:00'}]}
        home.cache[home_status.SL]=(90,data)
        with patch.object(home_status.time,'monotonic',return_value=100) as clock, \
                patch.object(home_status,'get_json',side_effect=departures_only([{}, {'departures':[]}])) as network:
            self.assertIn('Hjulsta',home.metro(NOW,3)['sl0'])
            self.assertFalse(home.metro_read(NOW)[0].startswith('SL: uppdatering saknas'))
            self.assertIs(home.cache[home_status.SL][1],data)
            clock.return_value=159;home.metro(NOW,3)
            self.assertEqual(sl_calls(network),1)
            clock.return_value=160
            rows=home.metro(NOW,3)
            self.assertIn('Hjulsta',rows['sl0']);self.assertEqual(rows['sl_walk'],'3 min att gå')
            self.assertEqual(sl_calls(network),2)


def departures_only(answers):
    """get_json's side effect for SL's departures; the blue line's messages (SL_LINES) answer [] apart."""
    answers = iter(answers)
    def get(url, **kw):
        if url == home_status.SL_LINES:
            return []
        answer = next(answers)
        if isinstance(answer, Exception):
            raise answer
        return answer
    return get


def sl_calls(network):
    return sum(1 for c in network.call_args_list if c.args[0] == home_status.SL)


def state(key, value, **attributes):
    return {"entity_id": E[key], "state": value, "attributes": attributes,
            "last_changed": "2026-10-21T17:30:00+00:00"}


STATES = [state("door", "off"), state("temperature", "22.62"), state("pm25", "11"), state("purifier", "on"),
          state("mode", "Sleep"), state("filter", "78"), state("smoke", "off"), state("water", "off"),
          state("sun", "below_horizon", next_rising="2026-10-22T05:42:00+00:00",
                next_setting="2026-10-22T15:20:00+00:00"),
          state("laundry", "Torsdag 22 Okt 06:00–09:00"),
          state("moon", "Avtagande måne", illumination=88, next_full="2026-11-24T15:53+00:00",
                next_new="2026-11-09T08:02+00:00"),
          {"entity_id": "switch.something_else", "state": "on", "attributes": {}, "last_changed": ""}]


def hour(start_utc, temp, rain=0.0, frozen=0, symbol=4):
    h = int(start_utc[11:13])
    end = f"{start_utc[:11]}{h + 1:02d}:00:00Z"
    return {"time": end, "intervalParametersStartTime": start_utc,
            "data": {"air_temperature": temp, "wind_speed": 5.6, "symbol_code": symbol,
                     "precipitation_amount_mean": rain, "probability_of_frozen_precipitation": frozen}}


class HomeStatusTest(unittest.TestCase):
    def test_home_assistant(self):
        lines = home_status.ha_lines(STATES, NOW)
        self.assertEqual(lines, [
            "Ytterdörren: stängd sedan 19:30.",
            "Inne: 22,6 °C.",
            "Luften: PM2,5 11 µg/m³ (bra luft), luftrenaren på, läge natt, filtret 78 % kvar.",
            "Larmen: ingen rök, ingen vattenläcka.",
            "Solen: går upp i morgon 07:42 och går ner i morgon 17:20.",
            "Nästa tvättid: Torsdag 22 Okt 06:00–09:00."])

    def test_open_door_smoke_and_unavailable(self):
        states = [state("door", "on"), state("smoke", "on"), state("temperature", "unavailable")]
        lines = home_status.ha_lines(states, NOW)
        self.assertEqual(lines[0], "Ytterdörren: öppen sedan 19:30 (20 minuter).")
        self.assertIn("RÖK I LÄGENHETEN", lines[1])
        self.assertFalse(any(line.startswith("Inne") for line in lines))

    def test_weather_rain_later(self):
        forecast = {"timeSeries": [hour("2026-10-21T16:00:00Z", 13.0), hour("2026-10-21T17:00:00Z", 11.6),
                                   hour("2026-10-21T18:00:00Z", 10.2), hour("2026-10-21T19:00:00Z", 9.4, rain=0.4)]}
        self.assertEqual(home_status.weather_lines(forecast, NOW), [
            "Ute nu: 11,6 °C, molnigt, vind 6 m/s.",
            "De närmaste 12 timmarna: mellan 9 och 12 °C.",
            "Nederbörd: regn från 21:00, cirka 0,4 mm i timmen."])

    def test_weather_snow_now_and_dry(self):
        snow = {"timeSeries": [hour("2026-10-21T17:00:00Z", -1.0, rain=1.2, frozen=90, symbol=26)]}
        self.assertEqual(home_status.weather_lines(snow, NOW)[-1], "Nederbörd: snö nu, cirka 1,2 mm i timmen.")
        dry = {"timeSeries": [hour("2026-10-21T17:00:00Z", 11.6)]}
        self.assertIn("inget regn", home_status.weather_lines(dry, NOW)[-1])
        self.assertIn("kring 12", home_status.weather_lines(dry, NOW)[1])

    def test_the_door_line_fits_the_larm_card(self):
        """The card has gen.DOT_TEXT_W (184 px) beside the dot; "sedan 28/10 20:43" ran to its edge (2026-10-29)."""
        def since(stamp):
            door = dict(state("door", "off"), last_changed=stamp)
            return home_status.panel_fields([door], NOW)["door_since"]
        self.assertEqual(since("2026-10-21T17:30:00+00:00"), "sedan 19:30")
        self.assertEqual(since("2026-10-20T22:01:00+00:00"), "sedan 00:01")       # today, local time
        self.assertEqual(since("2026-10-20T18:43:00+00:00"), "sedan i går")
        self.assertEqual(since("2026-10-18T18:43:00+00:00"), "sedan 18/10")
        sys.path.insert(0, str(ROOT / "styles" / "glass-v2"))
        import gen
        font = ROOT / "styles/glass-v2/.esphome/font/Inter@500@False@v1.ttf"   # Inter 500 13, the line's font
        worst = ["sedan 23:59", "sedan i går", "sedan 28/12", "sedan 00:00"]
        if font.exists():
            from PIL import ImageFont
            inter = ImageFont.truetype(str(font), 13)
            for text in worst:
                self.assertLessEqual(inter.getlength("Ytterdörren · " + text), gen.DOT_TEXT_W, text)
        else:   # no ESPHome font cache here: no longer than the longest measured fit (172 px)
            self.assertLessEqual(max(map(len, worst)), len("sedan 28/12"))
        self.assertEqual(gen.DOT_TEXT_W, 184)

    def test_every_live_line_in_hemmets_cards_has_a_width(self):
        """A label the server fills must stop at its card's padding: without a width a long value runs to the
        card's edge, as the door line did (2026-10-29). Only these short, fixed-vocabulary values may go without."""
        sys.path.insert(0, str(ROOT / "styles" / "glass-v2"))
        import gen
        short = {"h_inside", "h_pm25", "h_air", "h_rooms"}       # "21,9°", "PM2,5 10 µg/m³", "Bra luft", "Blå + Röd"
        short.add("h_rem_head")      # a fixed title; its id is only for the press hint of the reminder page's tap
        missing = []

        def walk(node):
            for kind, body in node.items() if isinstance(node, dict) else ():
                if not isinstance(body, dict):
                    continue
                if kind == "label" and body.get("id") and "width" not in body and body["id"] not in short:
                    missing.append(body["id"])
                for child in body.get("widgets", []):
                    walk(child)
        for card in gen.hemmet_page()["widgets"][4:]:
            walk(card)
        self.assertEqual(missing, [])

    def test_outside_for_the_panel(self):
        rain = {"timeSeries": [hour("2026-10-21T18:00:00Z", 10.2), hour("2026-10-21T19:00:00Z", 9.4, rain=0.4)]}
        self.assertEqual(home_status.outside_short(rain, NOW), "10°, regn från 21:00")
        dry = {"timeSeries": [hour("2026-10-21T17:00:00Z", 11.6, symbol=3)]}
        self.assertEqual(home_status.outside_short(dry, NOW), "12°, halvklart")
        self.assertEqual(home_status.outside_short({"timeSeries": []}, NOW), "")

    def test_panel_fields_calm(self):
        f = home_status.panel_fields(STATES, NOW)
        self.assertEqual(f["status"], "Allt lugnt hemma.")
        self.assertEqual((f["alert"], f["smoke_on"], f["water_on"]), ("off", "off", "off"))
        self.assertEqual((f["door"], f["door_since"]), ("Stängd", "sedan 19:30"))
        self.assertEqual((f["inside"], f["pm25"], f["air"]), ("22,6°", "11 µg/m³", "Bra luft"))
        self.assertEqual(f["purifier"], "Natt · filter 78 %")
        self.assertEqual(f["laundry"], "tors 22 okt 06:00–09:00")
        self.assertEqual(f["sun"], "Solen går upp i morgon 07:42")
        self.assertEqual(f["moon"], "Avtagande måne, 88 % · fullmåne 24 nov")
        self.assertEqual(f["summary"], "Dörren stängd · 22,6° inne · bra luft")

    def test_moon_line_names_the_next_full_or_new_moon(self):
        def moon(phase, **a):
            return {"state": phase, "attributes": {"illumination": 99, **a}}
        self.assertEqual(home_status.moon_line(moon("Fullmåne", next_new="2026-11-09T08:02+00:00",
                                                    next_full="2026-12-24T01:00+00:00"), NOW),
                         "Fullmåne, 99 % · nymåne 9 nov")
        soon = (NOW + home_status.timedelta(hours=2)).isoformat()
        self.assertTrue(home_status.moon_line(moon("Tilltagande måne", next_full=soon), NOW).endswith(
            "fullmåne i dag " + home_status.clock(home_status.parse_time(soon))))
        self.assertEqual(home_status.moon_line(moon("Nymåne"), NOW), "Nymåne, 99 %")
        self.assertEqual(home_status.panel_fields([], NOW)["moon"], "")

    def test_panel_fields_alerts_in_order(self):
        f = home_status.panel_fields([state("door", "on"), state("pm25", "40")], NOW)
        self.assertEqual((f["status"], f["alert"]), ("Ytterdörren har stått öppen i 20 minuter.", "on"))
        f = home_status.panel_fields([state("door", "on"), state("smoke", "on"), state("water", "on")], NOW)
        self.assertEqual((f["status"], f["smoke"], f["water_on"]), ("Brandvarnaren larmar!", "Rök!", "on"))
        f = home_status.panel_fields([state("pm25", "40"), state("purifier", "off")], NOW)
        self.assertEqual((f["status"], f["air"], f["purifier"]), ("Dålig luft, PM2,5 är 40.", "Dålig luft", "Av"))
        f = home_status.panel_fields([state("laundry", "Onsdag 21 Okt 06:00–09:00: Gul")], NOW)
        self.assertEqual(f["laundry"], "")      # that slot is over and the booker has no new one yet
        self.assertIn("har redan varit", home_status.ha_lines([state("laundry", "Onsdag 21 Okt 06:00–09:00")], NOW)[0])
        f = home_status.panel_fields([state("temperature", "unavailable")], NOW)
        self.assertEqual((f["status"], f["inside"], f["alert"]), ("Hemstatus saknas.", "", "on"))

    def test_missing_sensor_status_is_not_reported_as_calm_or_as_an_actual_alarm(self):
        f=home_status.panel_fields([state('door','off'),state('smoke','unavailable')],NOW)
        self.assertEqual((f['status'],f['alert'],f['smoke_on']),('Delar av hemstatus saknas.','on','off'))
        f=home_status.panel_fields([state('door','off'),state('smoke','on'),state('water','unavailable')],NOW)
        self.assertEqual(f['status'],'Brandvarnaren larmar!')

    def test_metro(self):
        def dep(dest, code, expected, line, state="EXPECTED"):
            return {"destination": dest, "direction_code": code, "expected": expected, "scheduled": expected,
                    "state": state, "line": {"designation": line}}
        departures = {"departures": [
            dep("Kungsträdgården", 2, "2026-10-21T19:50:20", "11"), dep("Akalla", 1, "2026-10-21T19:52:36", "11"),
            dep("Kungsträdgården", 2, "2026-10-21T19:53:56", "10", state="CANCELLED"),
            dep("Kungsträdgården", 2, "2026-10-21T19:59:18", "11"), dep("Hjulsta", 1, "2026-10-21T19:57:36", "10"),
            dep("Kungsträdgården", 2, "2026-10-21T20:03:56", "10"), dep("Kungsträdgården", 2, "2026-10-21T20:09:00", "11")],
            "stop_deviations": [{"message": "Hissen är ur funktion. "}]}
        self.assertEqual(home_status.metro_lines(departures, NOW), [
            "Tunnelbanan från Stadshagen mot Kungsträdgården: 19:50 (linje 11, nu).",
            "Tunnelbanan från Stadshagen mot Akalla: 19:52 (linje 11, om 2 min).",
            "Tunnelbanan från Stadshagen mot Hjulsta: 19:57 (linje 10, om 7 min).",
            "Tunnelbanan från Stadshagen mot Kungsträdgården: 19:59 (linje 11, om 9 min).",
            "Tunnelbanan från Stadshagen mot Kungsträdgården: 20:03 (linje 10, om 13 min).",
            "SL meddelar: Hissen är ur funktion."])
        self.assertEqual(home_status.metro_lines({"departures": []}, NOW),
                         ["Tunnelbanan från Stadshagen: inga avgångar den närmaste timmen."])
        # One chronological list across both directions; 3 minutes' walk.
        self.assertEqual(home_status.metro_panel(departures, NOW, walk=3), {
            "sl0": "11|Akalla|hinner inte|2|missed",       # 19:50 is gone (0 min, 3 to walk)
            "sl1": "10|Hjulsta|gå om 4 min|7|",
            "sl2": "11|Kungsträdgården|gå om 6 min|9|",
            "sl3": "10|Kungsträdgården|gå om 10 min|13|"})
        soon = {"departures": [dep("Kungsträdgården", 2, "2026-10-21T19:54:10", "10")]}
        self.assertEqual(home_status.metro_panel(soon, NOW, walk=3)["sl0"], "10|Kungsträdgården|gå nu|4|go")
        self.assertEqual(home_status.metro_panel({}, NOW), {"sl0": "", "sl1": "", "sl2": "", "sl3": ""})

    @patch.object(home_status, 'Thread', InlineThread)
    def test_metro_bridges_short_outage_recomputes_time_and_never_freezes_expired_trains(self):
        home = home_status.Home()
        data = {"departures": [{"direction_code": 1, "line": {"designation": "10"},
                                "destination": "Hjulsta", "expected": "2026-10-21T19:58:00+02:00"}]}
        with patch.object(home_status.time, 'monotonic', return_value=100), \
             patch.object(home_status, 'get_json', return_value=data):
            first = home.metro(NOW, 3)
        self.assertIn('|8|', first['sl0'])
        with patch.object(home_status.time, 'monotonic', return_value=101 + home_status.SL_STALE_S), \
             patch.object(home_status, 'get_json', side_effect=OSError('temporary DNS failure')):
            retry = home.metro(NOW + home_status.timedelta(minutes=1), 3)
        self.assertIn('|7|', retry['sl0'])
        self.assertEqual(retry['sl_walk'], 'SL uppdateras')
        with patch.object(home_status.time, 'monotonic', return_value=101 + home_status.SL_MAX_AGE_S), \
             patch.object(home_status, 'get_json', side_effect=OSError('offline')):
            expired = home.metro(NOW, 3)
        self.assertEqual([expired[f'sl{i}'] for i in range(4)], [''] * 4)
        self.assertEqual(expired['sl_walk'], 'Saknar SL-kontakt')
        with patch.object(home_status.time, 'monotonic', return_value=101 + home_status.SL_STALE_S), \
             patch.object(home_status, 'get_json', side_effect=OSError('offline')):
            departed = home.metro(NOW + home_status.timedelta(minutes=9), 3)
        self.assertEqual(departed['sl0'], '')
        self.assertEqual(departed['sl_walk'], 'Saknar SL-kontakt')

    @patch.object(home_status, 'Thread', InlineThread)
    def test_metro_empty_success_clears_old_trains_and_recovery_clears_error_status(self):
        home = home_status.Home()
        home.cache[home_status.SL] = (1, {'departures': []})
        with patch.object(home_status.time, 'monotonic', return_value=100), \
             patch.object(home_status, 'get_json', return_value={'departures': []}):
            empty = home.metro(NOW, 3)
        self.assertEqual(empty['sl_walk'], 'Inga avgångar')
        with patch.object(home_status.time, 'monotonic', return_value=101 + home_status.SL_STALE_S), \
             patch.object(home_status, 'get_json', side_effect=OSError('offline')):
            self.assertEqual(home.metro(NOW, 3)['sl_walk'], 'Saknar SL-kontakt')
        with patch.object(home_status.time, 'monotonic', return_value=101 + home_status.SL_STALE_S + home_status.SL_REFRESH_S), \
             patch.object(home_status, 'get_json', return_value={'departures': [{
                 'direction_code': 1, 'expected': '2026-10-21T19:58:00+02:00',
                 'line': {'designation': '11'}, 'destination': 'Akalla'}]}):
            recovered = home.metro(NOW, 4)
        self.assertEqual(recovered['sl_walk'], '4 min att gå')
        self.assertIn('Akalla', recovered['sl0'])

    @patch.object(home_status, 'Thread', InlineThread)
    def test_sl_worker_waits_long_enough_for_slow_sl_replies(self):
        # SL answered correctly after 9.8 s on 2026-10-26; an 8 s limit discarded those replies.
        home = home_status.Home()
        with patch.object(home_status.time, 'monotonic', return_value=100), \
             patch.object(home_status, 'get_json', return_value={'departures': []}) as network:
            home.metro(NOW, 3)
        self.assertEqual(network.call_args.kwargs['timeout'], home_status.SL_TIMEOUT_S)
        self.assertGreaterEqual(home_status.SL_TIMEOUT_S, 12)
        self.assertLess(home_status.SL_TIMEOUT_S, home_status.SL_REFRESH_S)

    @patch.object(home_status, 'Thread', InlineThread)
    def test_empty_sl_reply_never_erases_a_board_with_trains(self):
        # 2026-10-26: SL answered 200 {"departures": []} to 6 of 16 probes while trains ran.
        home = home_status.Home()
        data = {'departures': [{'direction_code': 1, 'line': {'designation': '10'},
                                'destination': 'Hjulsta', 'expected': '2026-10-21T19:58:00+02:00'}]}
        with patch.object(home_status.time, 'monotonic', return_value=100), \
             patch.object(home_status, 'get_json', return_value=data):
            home.metro(NOW, 3)
        with patch.object(home_status.time, 'monotonic', return_value=161), \
             patch.object(home_status, 'get_json', return_value={'departures': []}):
            after = home.metro(NOW, 3)
        self.assertIn('Hjulsta', after['sl0'])
        self.assertIs(home.cache[home_status.SL][1], data)
        self.assertEqual(home._sl_next, 161 + home_status.SL_RETRY_S)
        with patch.object(home_status.time, 'monotonic', return_value=181), \
             patch.object(home_status, 'get_json', return_value=data):
            self.assertEqual(home.metro(NOW, 3)['sl_walk'], '3 min att gå')

    @patch.object(home_status, 'Thread', InlineThread)
    def test_empty_sl_reply_is_accepted_when_the_old_board_is_done_or_sl_said_so_for_ten_minutes(self):
        data = {'departures': [{'direction_code': 1, 'line': {'designation': '10'},
                                'destination': 'Hjulsta', 'expected': '2026-10-21T19:58:00+02:00'}]}
        for at, now in ((161, NOW + home_status.timedelta(minutes=9)), (100 + home_status.SL_MAX_AGE_S - 10, NOW)):
            with self.subTest(at=at):
                home = home_status.Home()
                home.cache[home_status.SL] = (100, data)
                home._sl_empty_since = 100          # SL has answered empty since this board
                with patch.object(home_status.time, 'monotonic', return_value=at), \
                     patch.object(home_status, 'get_json', return_value={'departures': []}):
                    out = home.metro(now, 3)
                self.assertEqual(out['sl_walk'], 'Inga avgångar')
                self.assertEqual(out['sl0'], '')

    def test_server_start_says_updating_until_sl_has_answered(self):
        class Pending:
            def __init__(self, *, target, **kwargs): pass
            def start(self): pass
        home = home_status.Home()
        with patch.object(home_status, 'Thread', Pending):
            self.assertEqual(home.metro(NOW, 3)['sl_walk'], 'Uppdaterar avgångar')
        home = home_status.Home()
        with patch.object(home_status, 'Thread', InlineThread), \
             patch.object(home_status.time, 'monotonic', return_value=100) as clock, \
             patch.object(home_status, 'get_json', side_effect=TimeoutError()):
            self.assertEqual(home.metro(NOW, 3)['sl_walk'], 'Uppdaterar avgångar')
            # Nothing to show yet: retry soon instead of leaving the card empty for a full minute.
            self.assertEqual(home._sl_next, 100 + home_status.SL_RETRY_S)
            clock.return_value = 100 + home_status.SL_STALE_S
            self.assertEqual(home.metro(NOW, 3)['sl_walk'], 'Saknar SL-kontakt')

    @patch.object(home_status, 'Thread', InlineThread)
    def test_rate_limit_without_retry_after_still_waits_a_full_refresh(self):
        home = home_status.Home()
        error = home_status.urllib.error.HTTPError(home_status.SL, 429, 'Quota has been exceeded', {}, None)
        with patch.object(home_status.time, 'monotonic', return_value=100), \
             patch.object(home_status, 'get_json', side_effect=error):
            home.metro(NOW, 3)
        self.assertEqual(home._sl_next, 100 + home_status.SL_REFRESH_S)

    def test_metro_sorts_expected_times_and_takes_closest_four_without_direction_quota(self):
        def dep(destination, expected, **extra):
            return {"destination": destination, "direction_code": 2, "expected": expected,
                    "scheduled": "2026-10-21T20:40:00", "line": {"designation": "11"}, **extra}
        departures = {"departures": [
            dep("Later", "2026-10-21T20:10:00", direction_code=1),
            dep("Fourth", "2026-10-21T19:55:40"), dep("Second", "2026-10-21T19:54:50"),
            dep("First", "2026-10-21T19:54:10"), dep("Third", "2026-10-21T19:55:10"),
            dep("Past", "2026-10-21T19:49:59"), dep("Cancelled", "2026-10-21T19:54:00", state="CANCELLED"),
            dep("Inhibited", "2026-10-21T19:54:00", state="INHIBITED"),
            dep("Bad", "invalid"), dep("Scheduled", None, scheduled="2026-10-21T20:20:00"),
            {"direction_code": 1}, None]}
        rows = home_status.metro_panel(departures, NOW)
        self.assertEqual([rows[f"sl{i}"].split("|")[1] for i in range(4)], ["First", "Second", "Third", "Fourth"])
        trains = home_status.metro_departures(departures, NOW)
        self.assertEqual([d["destination"] for _, d in trains], ["First", "Second", "Third", "Fourth", "Later", "Scheduled"])

    def test_weather_for_the_dashboard(self):
        rain = {"timeSeries": [hour("2026-10-21T18:00:00Z", 10.2, symbol=3), hour("2026-10-21T19:00:00Z", 9.4, rain=0.4)]}
        self.assertEqual(home_status.weather_panel(rain, NOW),
                         {"w_temp": "10°", "w_line": "Regn från 21:00", "w_tip": "Ta paraply", "w_icon": "rain"})
        later = {"timeSeries": [hour("2026-10-21T18:00:00Z", 10.2, symbol=1), hour("2026-10-22T03:00:00Z", 6, rain=1)]}
        self.assertEqual(home_status.weather_panel(later, NOW, sun_up=False)["w_icon"], "moon")   # rain only later
        snow = {"timeSeries": [hour("2026-10-21T17:00:00Z", -1.0, rain=1.2, frozen=90, symbol=26)]}
        self.assertEqual(home_status.weather_panel(snow, NOW),
                         {"w_temp": "-1°", "w_line": "Snö nu", "w_tip": "Ta varma kläder", "w_icon": "snow"})
        dry = {"timeSeries": [hour("2026-10-21T17:00:00Z", 11.6, symbol=6)]}
        self.assertEqual(home_status.weather_panel(dry, NOW)["w_line"], "Mulet")
        self.assertEqual(home_status.weather_panel({}, NOW)["w_temp"], "")

    def test_calendar_for_the_dashboard(self):
        ev = lambda h, m, title, day=21, cal="Jobb": (datetime(2026, 10, day, h, m, tzinfo=TZ), cal, title, title)
        events = [ev(19, 0, "Lunch"), ev(19, 30, "Gym"), ev(20, 30, "Ring Pelle", cal="Att göra"), ev(21, 0, "Film"), ev(22, 0, "Sova"),
                  ev(9, 30, "Standup", day=22)]
        self.assertEqual(home_status.calendar_panel(events, NOW), {
            "cal_head": "IDAG", "cal0": "Gym|19:30|Jobb", "cal1": "Ring Pelle|20:30|Att göra", "cal2": "Film|21:00|Jobb"})
        late = NOW.replace(hour=23)
        self.assertEqual(home_status.calendar_panel(events, late),
                         {"cal_head": "I MORGON", "cal0": "Standup|09:30|Jobb", "cal1": "", "cal2": ""})
        self.assertEqual(home_status.calendar_panel([], NOW)["cal0"], "Inget mer inlagt||")
        # One event left today: tomorrow's fill the card under the same IDAG, their time marked (2026-11-02).
        evening = NOW.replace(hour=21, minute=50)
        self.assertEqual(home_status.calendar_panel(events + [ev(11, 0, "Möte", day=22)], evening), {
            "cal_head": "IDAG", "cal0": "Sova|22:00|Jobb", "cal1": "Standup|i morgon 09:30|Jobb",
            "cal2": "Möte|i morgon 11:00|Jobb"})


    def test_next_up_for_the_jarvis_card(self):
        # 2026-11-02: what is coming, while the chat is empty; never what the dashboard's cards already show.
        at = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=TZ)
        now = at(21, 19)
        reminders = [(at(21, 20, 30), "20:30 Ring Pelle"), (at(22, 7), "i morgon 07:00 Ta ut soporna"),
                     (at(23, 9), "fredag 09:00 Tandläkaren")]
        laundry = "Onsdag 22 Okt 06:00–09:00: Gul + Röd"
        self.assertEqual(home_status.next_up(now, "Ytterdörren har stått öppen i 12 minuter.", reminders, laundry,
                                             "Väckning 08:45"),
                         "Ytterdörren har stått öppen i 12 minuter.\n20:30 Ring Pelle\ni morgon 07:00 Ta ut soporna\n"
                         "Tvättid i morgon 06:00–09:00\nVäckning 08:45")       # five lines since the small mic went
        self.assertEqual(home_status.next_up(now, "Delar av hemstatus saknas.", reminders[2:], laundry, "Väckning 08:45"),
                         "Tvättid i morgon 06:00–09:00\nVäckning 08:45")       # far reminder left out
        self.assertEqual(home_status.next_up(at(22, 7), "", (), laundry, "Väckning 08:45"), "Tvättid nu, till 09:00")
        self.assertEqual(home_status.next_up(at(20, 12), "", (), laundry, "Ingen väckning"), "")   # laundry 42 h away
        self.assertEqual(home_status.next_up(at(21, 10), "", (), "", "Snooze till 10:05"), "Snooze till 10:05")
        self.assertEqual(home_status.next_up(at(21, 10), "", (), "unknown", "Väckning 08:45"), "")
        # A disruption near home that runs or starts within 24 hours comes right after the alert (2026-11-02).
        house = [(True, "Exergi: planerat fjärrvärmearbete i morgon 09:00–13:00"), (False, "Stockholm Vatten: vattenavbrott fre 08:00")]
        self.assertEqual(home_status.next_up(at(21, 10), "", (), "", "", house),
                         "Exergi: planerat fjärrvärmearbete i morgon 09:00–13:00")
        self.assertEqual(home_status.next_up(at(21, 10), "", (), "", "", house[1:]), "")

    def test_calendar_card_shows_the_week_when_today_and_tomorrow_are_empty(self):
        # Joel, 2026-11-02: an evening card said only "Inget mer inlagt"; the week's next events take its place.
        at = lambda d, h, m=0: datetime(2026, 10, d, h, m, tzinfo=TZ)
        week = [(at(21, 9), at(21, 10), "Jobb", "Förbi"), (at(23, 0), None, "Annat", "Namnsdag"),
                (at(24, 18), at(24, 19), "Träna", "Pass|1"), (at(23, 18), at(23, 20), "Jobb", "Middag"),
                (at(26, 8), at(26, 9), "Jobb", "Sent")]
        colors = {"Jobb": "#0A84FF", "Träna": "#30D158"}
        self.assertEqual(home_status.calendar_panel([], NOW, colors, later=week), {
            "cal_head": "KOMMANDE", "cal0": "Namnsdag|fre heldag|Annat|", "cal1": "Middag|fre 18:00|Jobb|#0A84FF",
            "cal2": "Pass 1|lör 18:00|Träna|#30D158"})
        self.assertEqual(home_status.calendar_panel([], NOW, later=[]),
                         {"cal_head": "KOMMANDE", "cal0": "Inget inlagt på sju dagar||", "cal1": "", "cal2": ""})
        # Before the week's first read, and whenever today or tomorrow has an event, the card is as before.
        self.assertEqual(home_status.calendar_panel([], NOW, later=None)["cal0"], "Inget mer inlagt||")
        tomorrow = [(at(22, 9, 30), "Jobb", "Standup", "x")]
        self.assertEqual(home_status.calendar_panel(tomorrow, NOW, later=week)["cal_head"], "I MORGON")

    def test_metro_disruption_line(self):
        # Joel, 2026-11-02: SL's disruptions on the card; lifts and other stations' entrances say nothing to him.
        dep = lambda line, devs=(): {"line": {"designation": line}, "deviations": list(devs)}
        departures = {"departures": [dep("10", [{"message": "Försenad på grund av signalfel"}]), dep("11"),
                                     dep("17", [{"message": "Grön linje försenad"}])],
                      "stop_deviations": [{"message": "Stadshagen: En hiss mot Mariedalsvägen är avstängd."},
                                          {"message": "Stadshagen: Entrén mot Fridhemsplan är stängd.\nGå runt."}]}
        lines = ["Avstängd hiss vid Brommaplan", "Ingen trafik mellan Fridhemsplan och T-Centralen 14–16 nov"]
        self.assertEqual(home_status.metro_note(departures, lines), "Entrén mot Fridhemsplan är stängd. (+2)")
        self.assertEqual(home_status.metro_note({"departures": [dep("11")]}, lines[:1]), "")
        self.assertEqual(home_status.metro_note({}, lines[1:]), lines[1])
        sl = [{"message_variants": [{"header": "Signal fault", "language": "en"},
                                    {"header": "Signalfel vid Odenplan", "language": "sv"}]}, {"message_variants": []}]
        self.assertEqual(home_status.line_headers(sl), ["Signalfel vid Odenplan"])
        self.assertEqual(home_status.line_headers({"error": "x"}), [])

    def test_a_train_cancelled_only_by_its_deviation_is_named_and_never_offered(self):
        # Joel, 2026-11-03: SL kept state EXPECTED and said only "Inställd"; the card showed a bare "Inställd"
        # and would have told him "gå nu" for a train that does not run.
        off = {"importance_level": 7, "consequence": "CANCELLED", "message": "Inställd"}
        dep = lambda dest, at, line, devs=(): {"destination": dest, "direction_code": 2, "expected": at,
                                               "scheduled": at, "state": "EXPECTED", "line": {"designation": line},
                                               "deviations": list(devs)}
        data = {"departures": [dep("Kungsträdgården", "2026-10-21T19:54:00", "10", [off]),
                               dep("Kungsträdgården", "2026-10-21T19:58:00", "11"),
                               dep("Kungsträdgården", "2026-10-21T19:40:00", "10", [off])]}
        self.assertEqual(home_status.metro_note(data, (), NOW), "Inställd: 10 mot Kungsträdgården 19:54")
        self.assertEqual(home_status.metro_panel(data, NOW, walk=3)["sl0"], "11|Kungsträdgården|gå om 5 min|8|")
        self.assertEqual([d["line"]["designation"] for _, d in home_status.metro_departures(data, NOW)], ["11"])
        late = {"departures": [dep("Hjulsta", "2026-10-21T19:56:00", "10", [{"message": "Försenad"}])]}
        self.assertEqual(home_status.metro_note(late, (), NOW), "10 mot Hjulsta 19:56: Försenad")

    @patch.object(home_status, 'Thread', InlineThread)
    def test_metro_card_gives_the_fourth_row_to_the_disruption_line(self):
        home = home_status.Home()
        data = {"departures": [{"direction_code": 1, "line": {"designation": "10"}, "destination": "Hjulsta",
                                "expected": f"2026-10-21T20:{5 + 4 * i:02d}:00+02:00", "deviations": []}
                               for i in range(4)],
                "stop_deviations": [{"message": "Stadshagen: Signalfel, räkna med förseningar."}]}
        answers = {home_status.SL: data, home_status.SL_LINES: []}
        get = lambda url, **kw: answers[url]
        with patch.object(home_status.time, 'monotonic', return_value=100), \
             patch.object(home_status, 'get_json', side_effect=get):
            shown = home.metro(NOW, 3)
        self.assertEqual(shown['sl_note'], "Signalfel, räkna med förseningar.")
        self.assertTrue(shown['sl2'] and not shown['sl3'])
        answers[home_status.SL_LINES] = [{"message_variants": [{"header": "Ingen trafik i helgen", "language": "sv"}]}]
        with patch.object(home_status.time, 'monotonic', return_value=100 + home_status.SL_LINES_S), \
             patch.object(home_status, 'get_json', side_effect=get):
            home.metro(NOW, 3)
            self.assertEqual(home.sl_lines(), ["Ingen trafik i helgen"])
        with patch.object(home_status.time, 'monotonic',
                          return_value=200 + home_status.SL_LINES_S + home_status.SL_LINES_MAX_S):
            self.assertEqual(home.sl_lines(), [])        # too old to show
        # A rate-limited departures reply never asks the deviations API too.
        error = home_status.urllib.error.HTTPError(home_status.SL, 429, 'busy', {}, None)
        busy = home_status.Home()
        with patch.object(home_status.time, 'monotonic', return_value=100), \
             patch.object(home_status, 'get_json', side_effect=error) as network:
            busy.metro(NOW, 3)
        self.assertEqual([c.args[0] for c in network.call_args_list], [home_status.SL])

    def test_house_line_from_his_own_providers_over_home(self):
        # Joel, 2026-11-02: "varför inte ellevio som jag har bara": only Ellevio, Exergi and Stockholm Vatten, and
        # only when the outage's area covers home; never a radius (Ellevio alone has ~25 a day around Stockholm).
        home = (59.33, 18.03)
        square = lambda d: json.dumps([[[[home[1] - d, home[0] - d], [home[1] + d, home[0] - d],
                                          [home[1] + d, home[0] + d], [home[1] - d, home[0] + d]]]])
        beside = json.dumps([[[[home[1] + 0.001, home[0]], [home[1] + 0.01, home[0]], [home[1] + 0.01, home[0] + 0.01]]]])
        holed = json.dumps([[[[home[1] - 0.1, home[0] - 0.1], [home[1] + 0.1, home[0] - 0.1], [home[1] + 0.1, home[0] + 0.1]],
                             [[home[1] - 0.01, home[0] - 0.01], [home[1] + 0.01, home[0] - 0.01], [home[1], home[0] + 0.01]]],
                            [[[home[1] - 0.1, home[0] - 0.1], [home[1] - 0.05, home[0] - 0.1], [home[1] - 0.05, home[0] - 0.05]]]])
        rows = [("exergi", "Stockholm Exergi", "planned", square(0.01), "2026-10-22T07:00:00.000Z", "2026-10-22 13:00"),
                ("stromkoll", "Ellevio AB", "unplanned", square(0.05), "2026-10-21T17:00:00.000Z", None),
                ("stromkoll", "Vattenfall Eldistribution AB", "unplanned", square(0.05), "2026-10-21T17:00:00.000Z", None),
                ("stromkoll", "Ellevio AB", "unplanned", beside, "2026-10-21T17:00:00.000Z", None),        # next street
                ("svoa", "Stockholm Vatten och Avfall", "unknown", holed, "2026-10-21T16:00:00.000Z", None),  # in a hole
                ("svoa", None, "unknown", square(0.01), "2026-10-21T16:00:00.000Z", "2026-10-21 10:00"),  # passed estimate
                ("stromkoll", "Ellevio AB", "planned", square(0.01), "2026-11-10T07:00:00.000Z", None),      # > a week
                ("stromkoll", "Ellevio AB", "planned", "not json", "2026-10-22T07:00:00.000Z", None),
                ("stromkoll", "Ellevio AB", "planned", square(0.01), "2026-10-24T06:00:00.000Z", "2026-10-24 10:00")]
        found = home_status.own_disruptions(rows, home, NOW)
        self.assertEqual(found, [(True, "Stockholm Vatten: vattenavbrott pågår"),
                                 (True, "Ellevio: strömavbrott pågår"),
                                 (True, "Exergi: planerat fjärrvärmearbete i morgon 09:00–13:00"),
                                 (False, "Ellevio: planerat elarbete lör 08:00–10:00")])
        self.assertEqual(home_status.house_line(found[:1]), "störning|Stockholm Vatten: vattenavbrott pågår")
        self.assertEqual(home_status.house_line(found), "störning|Stockholm Vatten: vattenavbrott pågår (+3)")
        self.assertEqual(home_status.house_line([]), "")

    def test_house_reads_foljdfel_read_only_and_needs_the_home_zone(self):
        import sqlite3
        import tempfile
        from contextlib import closing
        area = json.dumps([[[[18.02, 59.32], [18.04, 59.32], [18.04, 59.34], [18.02, 59.34]]]])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "foljdfel.sqlite"
            with closing(sqlite3.connect(path)) as db, db:
                db.execute("CREATE TABLE observation (source, operator, sector, kind, geometry, start_utc, "
                           "claimed_end_estimate, gone_at)")
                db.executemany("INSERT INTO observation VALUES (?,?,?,?,?,?,?,?)", [
                    ("exergi", "Stockholm Exergi", "heat", "unplanned", area, "2026-10-21T17:00:00.000Z", None, None),
                    ("exergi", "Stockholm Exergi", "heat", "unplanned", area, "2026-10-21T15:00:00.000Z", None,
                     "2026-10-21T16:00:00Z"),                                                   # ended
                    ("telia", "Telia", "mobile", "unknown", area, "2026-10-21T15:00:00.000Z", None, None)])
            zone = {"entity_id": "zone.home", "attributes": {"latitude": 59.33, "longitude": 18.03}}
            with patch.object(home_status, "FOLJDFEL_DB", path):
                home = home_status.Home()
                self.assertEqual(home.disruptions([zone], NOW), [(True, "Exergi: fjärrvärmeavbrott pågår")])
                self.assertEqual(home.disruptions([], NOW), [])
            with patch.object(home_status, "FOLJDFEL_DB", Path(tmp) / "missing.sqlite"):
                self.assertEqual(home_status.Home().disruptions([zone], NOW), [])


class FakeHome:
    def __init__(self):
        self.reads = 0

    def read(self):
        self.reads += 1
        return "Ytterdörren: stängd sedan 19:30."


class AgentHomeTest(unittest.TestCase):
    def test_tool_and_snapshot(self):
        os.environ.pop("JARVIS_HOME_TOKEN_FILE", None)
        agent = openai_agent.LunaAgent(key="test", mock_tools=True)
        agent._ensure_tools()
        self.assertNotIn("hemmet", [t["name"] for t in agent.tools])   # off without JARVIS_HOME_TOKEN_FILE
        agent = openai_agent.LunaAgent(key="test", mock_tools=True)
        agent.home = FakeHome()
        agent._ensure_tools()
        self.assertIn("hemmet", [t["name"] for t in agent.tools])
        self.assertIsNone(agent.fresh_home())
        agent.agenda = (time.monotonic(), "en sak")
        self.assertEqual(agent.run_tool("hemmet", {}), (False, "Ytterdörren: stängd sedan 19:30."))
        self.assertIsNotNone(agent.agenda)            # reading the home is not a change: the agenda stays
        self.assertEqual(agent.fresh_home()[0], "Ytterdörren: stängd sedan 19:30.")
        agent.home_seen = (time.monotonic() - openai_agent.AGENDA_MAX_AGE - 1, "gammalt")
        self.assertIsNone(agent.fresh_home())


if __name__ == "__main__":
    unittest.main()
