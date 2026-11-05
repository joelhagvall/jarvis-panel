"""Time-specific SL journeys, no network or paid model calls."""
import copy
import json
import sys
import threading
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "server" / "agents"))
import journey
import agent_core


def leg(start, end, origin="Stadshagen", destination="Kungsträdgården", walk=False):
    return {"origin": {"name": origin, "departureTimePlanned": f"2026-10-23T{start}:00Z"},
            "destination": {"name": destination, "arrivalTimePlanned": f"2026-10-23T{end}:00Z"},
            "transportation": {"product": {"class": 100 if walk else 2, "name": "footpath" if walk else "Tunnelbana"},
                               "disassembledName": "" if walk else "11", "destination": {"name": destination}}}


class JourneyTest(unittest.TestCase):
    def setUp(self):
        journey.location.cache_clear()
        self.outward = [{"interchanges": 0, "legs": [leg("09:00", "09:10"),
                        leg("09:10", "09:25", "Kungsträdgården", "Moderna museet", True)]}]
        self.backward = [{"interchanges": 0, "legs": [leg("12:00", "12:15", "Moderna museet", "Kungsträdgården", True),
                         leg("12:15", "12:25", "Kungsträdgården", "Stadshagen")]}]
        self.calls = []
        self.barrier = None
        self.clock = patch.object(journey, "datetime", wraps=datetime).start()
        self.clock.now.return_value = datetime(2026, 10, 23, 6, tzinfo=timezone.utc)
        patch.dict(journey.os.environ, {"JARVIS_WALK_MIN": "3"}).start()
        patch.object(journey, "fetch", side_effect=self.fetch).start()
        self.addCleanup(patch.stopall)
        self.addCleanup(journey.location.cache_clear)

    def fetch(self, url, timeout=6):
        q = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        endpoint = urlparse(url).path.rsplit("/", 1)[-1]
        self.calls.append((endpoint, q))
        if self.barrier:
            self.barrier.wait(timeout=1)
        if endpoint == "stop-finder":
            name = q["name_sf"]
            return json.dumps({"locations": [{"id": name, "name": name, "isBest": True}],
                               "systemMessages": [{"type": "error", "code": -8011}]}).encode()
        return json.dumps({"journeys": self.outward if q["name_origin"] == "Stadshagen" else self.backward}).encode()

    def plan(self, **kwargs):
        return journey.plan("Moderna museet", arrival_at="2026-10-23T11:30", **kwargs)

    def test_round_trip_includes_walks_buffers_and_correct_api_time(self):
        error, raw = self.plan(return_at="2026-10-23T14:00", home_by="2026-10-23T15:00")
        self.assertFalse(error, raw)
        p = json.loads(raw)
        self.assertEqual(p["leave_origin_at"], "2026-10-23T10:55:00+02:00")
        self.assertEqual(p["back_at_origin"], "2026-10-23T14:28:00+02:00")
        self.assertEqual(p["outbound"]["legs"][1]["mode"], "gång")
        queries = [q for endpoint, q in self.calls if endpoint == "trips"]
        self.assertEqual({(q["itd_time"], q["itd_trip_date_time_dep_arr"]) for q in queries},
                         {("1130", "arr"), ("1400", "dep")})
        self.assertTrue(all(q["itd_date"] == "20261023" for q in queries))

    def test_place_lookups_and_both_trips_run_in_parallel(self):
        self.barrier = threading.Barrier(2)
        error, raw = self.plan(return_at="2026-10-23T14:00")
        self.assertFalse(error, raw)

    def test_visit_departure_boarding_and_calendar_times_are_separate(self):
        self.outward[0]["legs"] = [leg("09:00", "09:03", "Stadshagen", "Plattformen", True),
                                  leg("09:03", "09:10"),
                                  leg("09:10", "09:25", "Kungsträdgården", "Moderna museet", True)]
        self.backward[0]["legs"][-1]["destination"]["arrivalTimeEstimated"] = "2026-10-23T12:25:42Z"
        error, raw = self.plan(return_at="2026-10-23T14:00")
        self.assertFalse(error, raw)
        p = json.loads(raw)
        self.assertEqual(p["visit"], {"date": "2026-10-23", "from": "2026-10-23T11:30:00+02:00",
                                     "to": "2026-10-23T14:00:00+02:00"})
        self.assertEqual(p["leave_origin_at"], "2026-10-23T10:55:00+02:00")
        self.assertEqual(p["first_vehicle"]["departure"], "2026-10-23T11:03:00+02:00")
        self.assertEqual(p["leave_venue_at"], "2026-10-23T14:00:00+02:00")
        self.assertEqual(p["first_return_vehicle"]["departure"], "2026-10-23T14:15:00+02:00")
        self.assertEqual(p["back_at_origin"], "2026-10-23T14:28:00+02:00")      # SL's 14:28:42, to the minute
        self.assertNotIn(":42+", json.dumps(p))                                 # no seconds anywhere
        self.assertEqual(p["calendar_interval"], {"from": "2026-10-23T10:55:00+02:00",
                                               "to": "2026-10-23T14:29:00+02:00"})

    def test_late_estimate_is_rejected_instead_of_using_planned_time(self):
        self.outward[0]["legs"][-1]["destination"]["arrivalTimeEstimated"] = "2026-10-23T09:31:00Z"
        error, raw = self.plan()
        self.assertTrue(error)
        self.assertIn("ingen genomförbar utresa", raw)

    def test_latest_feasible_arrival_is_chosen_and_later_trip_is_rejected(self):
        self.outward += [{"legs": [leg("09:10", "09:28")]}, {"legs": [leg("09:15", "09:31")]}]
        error, raw = self.plan()
        self.assertFalse(error, raw)
        self.assertEqual(json.loads(raw)["outbound"]["departure"], "2026-10-23T11:10:00+02:00")

    def test_return_deadline_includes_walk_and_ten_minute_margin(self):
        for deadline, expected_error in (("14:38", False), ("14:37", True)):
            with self.subTest(deadline=deadline):
                error, raw = self.plan(return_at="2026-10-23T14:00", home_by="2026-10-23T" + deadline)
                self.assertEqual(error, expected_error, raw)

    def test_return_cannot_leave_before_end_of_visit(self):
        self.backward[0]["legs"][0]["origin"]["departureTimePlanned"] = "2026-10-23T11:59:00Z"
        error, raw = self.plan(return_at="2026-10-23T14:00")
        self.assertTrue(error)
        self.assertIn("ingen hemresa", raw)

    def test_departure_mode_starts_at_home_and_never_suggests_earlier_train(self):
        self.outward += [{"legs": [leg("09:10", "09:30")]}]
        error, raw = journey.plan("Moderna museet", departure_at="2026-10-23T11:00")
        self.assertFalse(error, raw)
        self.assertEqual(json.loads(raw)["leave_origin_at"], "2026-10-23T11:05:00+02:00")
        query = next(q for endpoint, q in self.calls if endpoint == "trips")
        self.assertEqual((query["itd_time"], query["itd_trip_date_time_dep_arr"]), ("1105", "dep"))

    def test_invalid_and_past_times_make_no_api_calls(self):
        for kwargs in ({}, {"arrival_at": "11:30"}, {"arrival_at": "2026-10-23T07:00"},
                       {"arrival_at": "2026-10-23T11:30", "departure_at": "2026-10-23T10:30"},
                       {"arrival_at": "2026-10-23T11:30", "home_by": "2026-10-23T15:00"},
                       {"arrival_at": "2026-10-23T11:30", "return_at": "2026-10-23T11:00"},
                       {"arrival_at": "2026-10-25T02:30"}, {"arrival_at": "2027-03-28T02:30"}):
            with self.subTest(kwargs=kwargs):
                self.assertTrue(journey.plan("Moderna museet", **kwargs)[0])
        self.assertEqual(self.calls, [])

    def test_ambiguous_place_requires_clarification_and_is_not_cached(self):
        with patch.object(journey, "request", return_value={"locations": [
                {"id": "1", "name": "Kyrkan A"}, {"id": "2", "name": "Kyrkan B"}]}):
            error, raw = journey.plan("Kyrkan", arrival_at="2026-10-23T11:30")
        self.assertTrue(error)
        self.assertIn("Precisera", raw)
        self.assertEqual(journey.location.cache_info().currsize, 0)

    def test_arlanda_is_its_pendeltag_station_and_weak_matches_are_dropped(self):
        asked = []
        def finder(endpoint, **params):
            asked.append(params["name_sf"])
            return {"locations": [{"id": "1", "name": "Stockholm, Bromma flygplats", "isBest": True, "matchQuality": 549},
                                  {"id": "2", "name": "Sigtuna, Arlanda central (pendeltåg)", "matchQuality": 1000}]}
        with patch.object(journey, "request", side_effect=finder):
            for name in ("Arlanda", "Arlanda flygplats", "Stockholm-Arlanda", "arlanda terminal 5"):
                self.assertEqual(journey.location(name)["id"], "2")
        self.assertEqual(set(asked), {"Arlanda central"})
        with patch.object(journey, "request", return_value={"locations": [
                {"id": "1", "name": "Stockholm, Bromma flygplats", "isBest": True, "matchQuality": 549}]}):
            self.assertRaises(ValueError, journey.location, "Landvetter flygplats")

    def test_spoken_place_forms_reach_the_stop_sl_is_sure_of(self):
        # SL's real scores on 2026-11-04: only the bare name reached MIN_MATCH, and every form below failed.
        scores = {"Zinkensdamm": 1000, "Zinkens damm": 836, "Zinkensdamm, Södermalm, Stockholm": 700,
                  "Zinkens damm, Södermalm, Stockholm": 650, "Zinkensdamm tunnelbana": 567}
        asked = []
        def finder(endpoint, **params):
            asked.append(params["name_sf"])
            return {"locations": [{"id": "Z", "name": "Stockholm, Zinkensdamm", "disassembledName": "Zinkensdamm",
                                   "isBest": True, "matchQuality": scores.get(params["name_sf"], 400)}]}
        with patch.object(journey, "request", side_effect=finder):
            for name in scores:
                self.assertEqual(journey.location(name)["id"], "Z", name)
        self.assertLessEqual(len(asked), 3 * len(scores))
        self.assertEqual(journey.variants("Hornsgatan 100, Stockholm"), ["Hornsgatan 100, Stockholm", "Hornsgatan 100"])

    def test_an_unsure_place_names_the_sound_alike_stop_first(self):
        with patch.object(journey, "request", return_value={"locations": [
                {"id": "1", "name": "Tensta sim- och idrottshall", "isBest": True, "matchQuality": 577},
                {"id": "2", "name": "Stockholm, Zinkensdamm", "disassembledName": "Zinkensdamm", "matchQuality": 458}]}):
            with self.assertRaises(ValueError) as error:
                journey.location("Sinkenstam")
        self.assertIn("osäkert: Stockholm, Zinkensdamm / Tensta", str(error.exception))

    def test_lookup_cache_is_reused_but_trip_times_are_refreshed(self):
        self.assertFalse(self.plan()[0])
        self.assertFalse(self.plan()[0])
        self.assertEqual([e for e, q in self.calls].count("stop-finder"), 2)
        self.assertEqual([e for e, q in self.calls].count("trips"), 2)

    def test_missing_cancelled_and_impossible_connections_fail_closed(self):
        broken = copy.deepcopy(self.outward[0])
        broken["legs"][1]["origin"]["departureTimePlanned"] = "2026-10-23T09:09:00Z"
        for data in ([], [{"legs": []}], [{"cancelled": True, "legs": self.outward[0]["legs"]}], [broken]):
            with self.subTest(data=data):
                self.outward = data
                self.assertTrue(self.plan()[0])

    def test_api_failure_returns_error_without_current_departure_fallback(self):
        with patch.object(journey, "fetch", side_effect=TimeoutError("timeout")):
            error, raw = self.plan()
        self.assertTrue(error)
        self.assertIn("kunde inte verifieras", raw)

    def test_tool_is_registered_and_read_only(self):
        with patch.dict(journey.os.environ, {"JARVIS_TV_MCP": "", "JARVIS_HOME_TOKEN_FILE": ""}), \
             patch.object(agent_core.lookup, "jina_key", return_value=None):
            agent = agent_core.ToolAgent()
            agent._init_tools(False, None, None, None)
            agent._ensure_tools()
        self.assertIn("resa", agent.read_only)
        error, raw = agent.run_tool("resa", {"destination": "Moderna museet", "arrival_at": "2026-10-23T11:30"})
        self.assertFalse(error, raw)
        self.assertEqual(agent.last_tools[0]["name"], "resa")


if __name__ == "__main__":
    unittest.main()
