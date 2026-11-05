#!/usr/bin/env python3
"""SL's metro board must survive SL as it really behaves, without the network.

On 2026-10-26 SL answered 200 {"departures": []} to most requests while trains ran, answered
429 "Quota has been exceeded" for minutes in a row, and sometimes took 10 s or timed out. The panel
then said "Saknar SL-kontakt" at start and lost its trains after a while. These tests replay that mix
through the real server code (Home.metro, as Home Assistant calls GET /panel every 15 s) and check
what the panel would show.

  python3 -m unittest tests/test_sl_resilience.py
"""
import json
import random
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
sys.path.insert(0, str(ROOT / "server"))
import check_sl  # noqa: E402
import home_status  # noqa: E402

FIXTURES = ROOT / "tests" / "fixtures" / "sl"
TZ = home_status.TZ
START = datetime(2026, 10, 26, 7, 0, tzinfo=TZ)
POLL_S = 15                                     # HA's HOME_INTERVAL_S for GET /panel


class InlineThread:
    def __init__(self, *, target, **kwargs):
        self.target = target

    def start(self):
        self.target()


def board(now):
    """A realistic SL answer: both directions, a train every 4 minutes for the next hour, naive local times."""
    rows = []
    for minute in range(1, 60, 4):
        for direction, destination, line in ((1, "Hjulsta", "10"), (2, "Kungsträdgården", "11")):
            when = (now + timedelta(minutes=minute + direction)).replace(tzinfo=None, microsecond=0)
            rows.append({"direction_code": direction, "destination": destination, "state": "EXPECTED",
                         "expected": when.isoformat(), "scheduled": when.isoformat(),
                         "line": {"designation": line}})
    return {"departures": rows, "stop_deviations": []}


def quota():
    return home_status.urllib.error.HTTPError(home_status.SL, 429, "Quota has been exceeded", {}, None)


class SL:
    """Scripted SL: each call draws an outcome; records when it was asked."""

    def __init__(self, clock, outcomes):
        self.clock, self.outcomes, self.calls = clock, outcomes, []

    def __call__(self, url, timeout=3, **kwargs):
        if url == home_status.SL_LINES:      # the blue line's messages (metro_note), not the departures replayed here
            return []
        at = self.clock[0]
        kind = self.outcomes(at)
        self.calls.append((at, kind))
        if kind == "ok":
            self.last_ok = at
            return board(START + timedelta(seconds=at))
        if kind == "empty":
            return json.loads((FIXTURES / "departures_spurious_empty.json").read_text())
        if kind == "429":
            raise quota()
        if kind == "timeout":
            raise TimeoutError("The read operation timed out")
        raise AssertionError(kind)


def replay(outcomes, hours=3, walk=3):
    """Poll metro() like HA does; yield (seconds, last good answer or None, panel fields, sl)."""
    clock = [0.0]
    sl = SL(clock, outcomes)
    sl.last_ok = None
    with patch.object(home_status, "Thread", InlineThread), \
            patch.object(home_status.time, "monotonic", side_effect=lambda: clock[0]), \
            patch.object(home_status, "get_json", side_effect=sl), \
            patch("builtins.print"):
        home = home_status.Home()
        for step in range(int(hours * 3600 / POLL_S)):
            clock[0] = step * POLL_S
            out = home.metro(START + timedelta(seconds=clock[0]), walk)
            yield clock[0], sl.last_ok, out, sl, home


class SLResilienceTest(unittest.TestCase):
    def check_invariants(self, outcomes, seed_label):
        missing = updating = 0
        last_429 = None
        for at, last_ok, out, sl, home in replay(outcomes):
            rows = [out[f"sl{i}"] for i in range(4)]
            age = None if last_ok is None else at - last_ok
            with self.subTest(case=seed_label, at=at):
                # A good answer young enough to bridge must always reach the panel.
                if age is not None and age <= home_status.SL_MAX_AGE_S - home_status.SL_RETRY_S:
                    self.assertTrue(all(rows), (age, out))
                    self.assertNotIn(out["sl_walk"], (home_status.SL_MISSING, "Inga avgångar"))
                # "SL uppdateras" only once the board is really old; no flicker on a young board.
                if out["sl_walk"] == "SL uppdateras":
                    self.assertGreaterEqual(age, home_status.SL_STALE_S)
                    updating += 1
                if out["sl_walk"] == home_status.SL_MISSING:
                    missing += 1
        # Never hammer SL: at most one request per retry interval, and a full minute after a 429.
        times = [t for t, _ in sl.calls]
        for before, after in zip(sl.calls, sl.calls[1:]):
            self.assertGreaterEqual(after[0] - before[0], home_status.SL_RETRY_S - POLL_S + 1, seed_label)
            if before[1] == "429":
                self.assertGreaterEqual(after[0] - before[0], home_status.SL_REFRESH_S, seed_label)
        self.assertLessEqual(len(times), 3 * 3600 / home_status.SL_RETRY_S + 1)
        return missing, updating, home.sl_status()

    def test_mostly_empty_replies_keep_the_board(self):
        # 2026-10-26 18:30: about 80% empty answers from both Jarvis and the Mac.
        for seed in range(5):
            rng = random.Random(seed)
            missing, _, status = self.check_invariants(
                lambda at: rng.choices(("ok", "empty", "429", "timeout"), (0.2, 0.7, 0.07, 0.03))[0], f"seed {seed}")
            # Empty answers are believed only after ten minutes without trains; the invariants above prove
            # that never hid a usable board.
            self.assertGreater(status["replies"]["empty_kept"], 10 * status["replies"]["empty_accepted"])

    def test_quota_storm_bridges_ten_minutes_then_says_so(self):
        # 15:36-15:45 on 2026-10-26: 429 on every try for about nine minutes.
        storm = lambda at: "429" if 600 <= at < 600 + 9 * 60 else "ok"
        missing, updating, status = self.check_invariants(storm, "nine-minute 429 storm")
        self.assertEqual(missing, 0)
        self.assertGreater(updating, 0)
        self.assertGreater(status["http"]["429"], 0)
        long = lambda at: "429" if 600 <= at < 600 + 20 * 60 else "ok"
        missing, _, status = self.check_invariants(long, "twenty-minute 429 storm")
        self.assertGreater(missing, 0)
        self.assertGreater(status["longest_missing_s"], 0)

    def test_server_start_during_degraded_sl_never_says_missing_before_trying(self):
        rng = random.Random(7)
        first = next(replay(lambda at: rng.choices(("ok", "empty"), (0.2, 0.8))[0]))
        self.assertIn(first[2]["sl_walk"], ("Uppdaterar avgångar", "3 min att gå"))

    def test_night_without_trains_says_no_departures_not_missing_contact(self):
        # After the last train, every answer is empty and the old board runs out on its own.
        night = lambda at: "ok" if at < 60 else "empty"
        clock_at_end = None
        for at, _, out, _, home in replay(night, hours=1.5):
            clock_at_end = (at, out)
        # board() always has trains, so model the real end of service: the kept board ages out.
        self.assertEqual(clock_at_end[1]["sl_walk"], "Inga avgångar")
        self.assertGreater(home.sl_status()["replies"]["empty_accepted"], 0)


class SLHealthCheckTest(unittest.TestCase):
    """server/check_sl.py, the live check behind `tools/panel sl-check`."""

    def test_degraded_sl_passes_and_an_outage_is_a_note_not_a_bug(self):
        rng = random.Random(3)
        for at, _, _, _, home in replay(lambda at: rng.choices(("ok", "empty", "429"), (0.2, 0.7, 0.1))[0]):
            if at % 600 == 0:
                problems, _ = check_sl.verdict(home.sl_status())
                self.assertEqual(problems, [], at)
        outage = lambda at: "ok" if at < 60 else "429"
        for at, _, _, _, home in replay(outage, hours=1):
            status = home.sl_status()               # read while the simulated clock is still in place
        problems, notes = check_sl.verdict(status)
        self.assertEqual(problems, [])
        self.assertTrue(any(note.startswith("sl_outage") for note in notes))

    def test_flags_the_old_bug_and_a_silent_home_assistant(self):
        limits = {"refresh_s": 60, "retry_s": 20, "stale_s": 180, "max_age_s": 600}
        base = {"uptime_s": 3600, "board_age_s": 30, "last_ok_age_s": 30, "last_ok_trains": True,
                "shown": "Inga avgångar", "shown_for_s": 30, "last_panel_read_age_s": 5,
                "longest_missing_s": 0, "replies": {}, "http": {}, "limits": limits}
        self.assertTrue(check_sl.verdict(base)[0][0].startswith("bug:"))
        quiet = {**base, "shown": "3 min att gå", "last_panel_read_age_s": 600}
        self.assertIn("Home Assistant", check_sl.verdict(quiet)[0][0])
        stuck = {**base, "shown": "Uppdaterar avgångar", "shown_for_s": 900, "last_ok_age_s": None}
        self.assertIn("still says", check_sl.verdict(stuck)[0][0])


class SLAfterRestartTest(unittest.TestCase):
    """Voice right after a server start (2026-10-26: "Tunnelbanan failed: No recent SL departures")."""

    def test_voice_waits_for_the_first_answer_in_flight(self):
        import threading
        release = threading.Event()

        def slow(url, timeout=3, **kwargs):
            release.wait(2)
            return board(datetime.now(TZ))
        home = home_status.Home()
        with patch.object(home_status, "get_json", side_effect=slow), patch("builtins.print"):
            home.prefetch_sl()                       # what the server does at start
            threading.Timer(0.2, release.set).start()
            lines = home.metro_read(datetime.now(TZ))
        self.assertTrue(lines[0].startswith("Tunnelbanan från Stadshagen mot"), lines)

    def test_voice_says_plainly_when_sl_has_not_answered(self):
        home = home_status.Home()
        with patch.object(home_status, "Thread", InlineThread), \
                patch.object(home_status, "get_json", side_effect=TimeoutError()), patch("builtins.print"):
            lines = home.metro_read(datetime.now(TZ), wait_s=0.1)
        self.assertEqual(len(lines), 1)
        self.assertIn("inte", lines[0])

    def test_server_start_prefetches_sl(self):
        source = (ROOT / "server/hallpanel_server.py").read_text()
        self.assertIn("WATCH.home.prefetch_sl()", source)


class SLPushTest(unittest.TestCase):
    """check_sl.py --notify: a push only when an alert appears or clears, never at night."""

    def test_push_on_change_only(self):
        state, sent = {}, []
        def step(current, night=False):
            nonlocal state
            text, state = check_sl.decide(state, current, night)
            if text:
                sent.append(text)
        outage = {"outage": "SL har inte gett avgångar på 20 minuter."}
        step({})
        step(outage)
        step(outage)
        step(outage)
        step({})
        step({})
        self.assertEqual(len(sent), 2, sent)
        self.assertIn("20 minuter", sent[0])
        self.assertEqual(sent[1], check_sl.ALL_CLEAR)

    def test_night_defers_both_alert_and_all_clear_to_the_morning(self):
        alert = {"bug": "Panelen tappade avgångar som SL hade gett (fel i Jarvis kod)."}
        text, state = check_sl.decide({}, alert, night=True)
        self.assertIsNone(text)
        text, state = check_sl.decide(state, alert, night=False)
        self.assertIn("tappade", text)
        text, state = check_sl.decide(state, {}, night=True)
        self.assertIsNone(text)
        text, state = check_sl.decide(state, {}, night=False)
        self.assertEqual(text, check_sl.ALL_CLEAR)

    def test_alert_categories(self):
        status = {"shown": check_sl.MISSING, "shown_for_s": check_sl.OUTAGE_ALERT_S}
        self.assertIn("outage", check_sl.alerts(status, [], 0))
        self.assertNotIn("outage", check_sl.alerts({**status, "shown_for_s": 60}, [], 0))
        self.assertEqual(set(check_sl.alerts(None, [], 1)), set())        # one failed read is a restart blip
        self.assertEqual(set(check_sl.alerts(None, [], 2)), {"server"})
        self.assertEqual(set(check_sl.alerts({}, ["bug: x"], 0)), {"bug"})

    def test_deploy_installs_and_enables_the_timer(self):
        deploy = (ROOT / "tools/deploy_jarvis.sh").read_text()
        self.assertIn("hallpanel-sl-check.timer ~/.config/systemd/user/", deploy)
        self.assertIn("enable --now hallpanel-sl-check.timer", deploy)
        # Unplugged on purpose: a deploy must not turn the "panelen svarar inte" pushes back on (2026-10-28).
        self.assertIn("if [ -f ~/.config/hallpanel/sl-check.paused ]; then", deploy)
        service = (ROOT / "server/hallpanel-sl-check.service").read_text()
        self.assertIn("check_sl.py --notify", service)
        self.assertIn("OnUnitActiveSec=5min", (ROOT / "server/hallpanel-sl-check.timer").read_text())


class PanelHealthAlertTest(unittest.TestCase):
    """server/panel_health.py and its pushes through check_sl.notify."""

    def history(self, now, reasons, alerts=()):
        entries = [{"time": now - 600 * (len(reasons) - i), "ok": True,
                    "status": {"boot_id": f"b{i}", "reset_reason": reason}} for i, reason in enumerate(reasons)]
        return {"schema": 1, "entries": entries, "active_alerts": [], "alerts": list(alerts)}

    def test_only_crash_resets_count_and_each_once(self):
        import panel_health
        now = 1_800_000_000
        history = self.history(now, ["1", "3", "11", "4", "6", "9"])
        found = panel_health.crashes(history, now)
        self.assertEqual([boot for boot, _ in found], ["b3", "b4", "b5"])
        self.assertEqual(panel_health.crashes(history, now, seen={"b3", "b4", "b5"}), [])
        self.assertEqual(panel_health.crashes(history, now + 2 * 86400), [])        # older than a day

    def test_memory_floor_pushes_once_per_boot_that_stayed_up(self):
        import panel_health
        now = 1_800_000_000
        def entry(boot, uptime_min, internal, psram=16_000_000, age=600):
            return {"time": now - age, "ok": True, "status": {"boot_id": boot, "uptime_ms": uptime_min * 60000,
                    "internal_min_free": internal, "psram_min_free": psram}}
        history = {"entries": [entry("dev", 10, 4096),                 # flashed over within minutes: quiet
                               entry("ok", 600, 90 * 1024),            # a normal long run
                               entry("low", 25, 7 * 1024, age=900),     # not up 30 minutes yet at this sample
                               entry("low", 35, 7 * 1024, 900_000),
                               entry("old", 600, 4096, age=2 * 86400)]}  # older than a day
        found = panel_health.memory_floors(history, now)
        self.assertEqual([boot for boot, _ in found], ["low"])
        self.assertIn("7 KiB", found[0][1])
        self.assertIn("0.9 MB", found[0][1])
        self.assertEqual(panel_health.memory_floors(history, now, seen={"low"}), [])

    def test_lasting_alerts_and_a_stalled_monitor(self):
        import panel_health
        now = 1_800_000_000
        history = self.history(now, ["3"], [{"kind": "repeated_failures", "count": 3},
                                            {"kind": "possible_memory_leak", "metric": "psram_free"},
                                            {"kind": "unexpected_restart", "reset_reason": "3"}])
        self.assertEqual(set(panel_health.lasting(history, now)), {"panel_unreachable", "panel_leak_psram_free"})
        self.assertIn("panel_monitor", panel_health.lasting(history, now + panel_health.STALE_S + 1200))
        self.assertIn("panel_monitor", panel_health.lasting(None, now))

    def run_notify(self, tmp, history, state=None, night=False):
        import json as _json
        import types
        pushed = []
        phone = types.SimpleNamespace(is_night=lambda: night, DeliveryUnconfirmed=OSError,
                                      Notifier=lambda: types.SimpleNamespace(
                                          push=lambda text, title=None: (pushed.append(text), (False, "ok"))[1]))
        path = Path(tmp) / "health.json"
        path.write_text(_json.dumps(history))
        state_file = Path(tmp) / "state.json"
        if state is not None:
            state_file.write_text(_json.dumps(state))
        import panel_health
        with patch.dict(sys.modules, {"notify": phone}), patch.object(check_sl, "STATE", state_file), \
                patch.object(panel_health, "HISTORY", path), patch("builtins.print"):
            check_sl.notify({"shown": "3 min att gå"}, [], False)
        return pushed, _json.loads(state_file.read_text())

    def test_first_run_treats_existing_crashes_as_old_news_then_pushes_new_ones(self):
        import tempfile
        import time as _time
        now = _time.time()
        with tempfile.TemporaryDirectory() as tmp:
            pushed, state = self.run_notify(tmp, self.history(now, ["4", "3"]))
            self.assertEqual(pushed, [])
            pushed, state = self.run_notify(tmp, self.history(now, ["4", "3", "3", "6"]), state)
            self.assertEqual(len(pushed), 1)
            self.assertIn("watchdog", pushed[0])
            pushed, state = self.run_notify(tmp, self.history(now, ["4", "3", "3", "6"]), state)
            self.assertEqual(pushed, [])

    def test_first_run_treats_existing_memory_floors_as_old_news(self):
        import tempfile
        import time as _time
        now = _time.time()
        def history(*boots):
            return {"schema": 1, "active_alerts": [], "alerts": [], "entries": [
                {"time": now - 60, "ok": True, "status": {"boot_id": boot, "reset_reason": "3",
                 "uptime_ms": 40 * 60000, "internal_min_free": 5000, "psram_min_free": 9_000_000}} for boot in boots]}
        with tempfile.TemporaryDirectory() as tmp:
            pushed, state = self.run_notify(tmp, history("a"))
            self.assertEqual(pushed, [])
            pushed, state = self.run_notify(tmp, history("a", "b"), state)
            self.assertEqual(len(pushed), 1)
            self.assertIn("nästan slut", pushed[0])
            pushed, state = self.run_notify(tmp, history("a", "b"), state)
            self.assertEqual(pushed, [])

    def test_crash_at_night_waits_for_the_morning(self):
        import tempfile
        import time as _time
        now = _time.time()
        with tempfile.TemporaryDirectory() as tmp:
            _, state = self.run_notify(tmp, self.history(now, ["3"]))
            pushed, state = self.run_notify(tmp, self.history(now, ["3", "4"]), state, night=True)
            self.assertEqual(pushed, [])
            pushed, state = self.run_notify(tmp, self.history(now, ["3", "4"]), state)
            self.assertEqual(len(pushed), 1)
            self.assertIn("panic", pushed[0])


    def test_unreachable_panel_is_never_pushed(self):
        """Joel 2026-10-28: no "Panelen svarar inte" on the phone; other panel alerts still push."""
        import tempfile
        import time as _time
        now = _time.time()
        unreachable = [{"kind": "repeated_failures", "count": 3}]
        with tempfile.TemporaryDirectory() as tmp:
            _, state = self.run_notify(tmp, self.history(now, ["1"]))
            pushed, state = self.run_notify(tmp, self.history(now, ["1"], unreachable), state)
            self.assertEqual(pushed, [])
            pushed, state = self.run_notify(tmp, self.history(now, ["1"]), state)
            self.assertEqual(pushed, [])                                      # and no "fungerar igen"
            leak = unreachable + [{"kind": "possible_memory_leak", "metric": "psram_free"}]
            pushed, _ = self.run_notify(tmp, self.history(now, ["1"], leak), state)
            self.assertEqual(len(pushed), 1)
            self.assertNotIn("svarar inte", pushed[0])

class SLFixtureTest(unittest.TestCase):
    """Real SL bodies captured from transport.integration.sl.se on 2026-10-26."""

    def test_real_board_with_naive_local_times_fills_four_rows(self):
        data = json.loads((FIXTURES / "departures_trains.json").read_text())
        first = min(home_status.parse_time(d["expected"]) for d in data["departures"])
        self.assertEqual(first.tzinfo, TZ)
        rows = home_status.metro_panel(data, first - timedelta(minutes=6), 3)
        self.assertTrue(all(rows[f"sl{i}"] for i in range(4)), rows)
        for row in rows.values():
            line, destination, note, minutes, state = row.split("|")
            self.assertTrue(line and destination and minutes.isdigit())

    @patch.object(home_status, "Thread", InlineThread)
    def test_real_spurious_empty_body_never_replaces_the_real_board(self):
        data = json.loads((FIXTURES / "departures_trains.json").read_text())
        empty = json.loads((FIXTURES / "departures_spurious_empty.json").read_text())
        now = min(home_status.parse_time(d["expected"]) for d in data["departures"]) - timedelta(minutes=6)
        home = home_status.Home()
        with patch.object(home_status.time, "monotonic", return_value=100), \
                patch.object(home_status, "get_json", return_value=data):
            home.metro(now, 3)
        with patch.object(home_status.time, "monotonic", return_value=161), \
                patch.object(home_status, "get_json", return_value=empty), patch("builtins.print"):
            out = home.metro(now, 3)
        self.assertTrue(all(out[f"sl{i}"] for i in range(4)))
        self.assertIs(home.cache[home_status.SL][1], data)


if __name__ == "__main__":
    unittest.main()
