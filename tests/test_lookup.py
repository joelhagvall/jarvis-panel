#!/usr/bin/env python3
"""Jarvis's free lookups (server/agents/lookup.py) without the network: a fake fetch serves Wikipedia,
an RSS feed, adsb.lol and adsbdb, wheretheiss.at and polisen.se; the agent offers and runs the tools,
and offers only the allowed tools of a fake TV connector.

  python3 -m unittest tests/test_lookup.py
"""
import json
import os
import sys
import threading
import time
import unittest
import urllib.error
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import agent_core  # noqa: E402
import lookup  # noqa: E402
import openai_agent  # noqa: E402

RSS = """<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel><title>SVT</title>
<item><title>Äldre nyhet</title><description>&lt;p&gt;Sammanfattning&lt;/p&gt;</description>
<pubDate>Wed, 21 Oct 2026 08:00:00 GMT</pubDate></item>
<item><title><![CDATA[Nyaste nyheten]]></title><description>Nyaste nyheten</description>
<pubDate>Wed, 21 Oct 2026 10:30:00 GMT</pubDate></item>
</channel></rss>""".encode()
# Airports as adsbdb gives them, for the route check.
ARN = {"latitude": 59.6519, "longitude": 17.9186}
CDG = {"latitude": 49.0097, "longitude": 2.5479}
CPH = {"latitude": 55.6180, "longitude": 12.6560}
HEL = {"latitude": 60.3172, "longitude": 24.9633}
MAD = {"latitude": 40.4719, "longitude": -3.5626}


class LookupTest(unittest.TestCase):
    def setUp(self):
        self.urls = []
        self.real_fetch = lookup.fetch
        lookup._cache.clear()

        def fake(url, timeout=4, headers=None):
            self.headers = headers
            self.urls.append(url)
            if "wikipedia.org" in url:
                lang = urlparse(url).netloc.split(".")[0]
                query = parse_qs(urlparse(url).query)["gsrsearch"][0]
                if lang == "sv" and query == "Anna Book":
                    return json.dumps({"query": {"pages": [{"title": "Anna Book", "description": "svensk sångerska",
                                                             "extract": "Anna Book, född 10 september 1970."}]}}).encode()
                return json.dumps({"batchcomplete": True}).encode()
            if "s.jina.ai" in url and "fail-once" in url and not getattr(self, "failed", False):
                self.failed = True
                raise urllib.error.HTTPError(url, 502, "Bad Gateway", {}, None)
            if "s.jina.ai" in url:
                return json.dumps({"data": [{"title": "AIK - Senaste matcher", "url": "https://example.se/aik",
                                             "description": "IK Oddevold 0 - 3 AIK, mån 9 mars"}]}).encode()
            if "r.jina.ai" in url:
                return ("Title: AIK\n" + "x" * 9000).encode()
            if "adsb.lol" in url:
                return json.dumps({"ac": [
                    {"flight": "SAS362G ", "r": "SE-ROU", "t": "A20N", "alt_baro": 2000, "gs": 150, "track": 190,
                     "dst": 5.4, "dir": 40, "lat": 59.403, "lon": 18.140},
                    {"flight": "        ", "r": "SE-JRK", "t": "A139", "alt_baro": "ground", "gs": 0, "dst": 12.0,
                     "dir": 220}]}).encode()
            if "adsbdb.com/v0/callsign/SAS362G" in url:
                return json.dumps({"response": {"flightroute": {
                    "airline": {"name": "Scandinavian Airlines System"},
                    "origin": {"municipality": "Paris", **CDG}, "destination": {"municipality": "Stockholm", **ARN}}}}).encode()
            if "adsbdb.com/v0/aircraft/SE-ROU" in url:
                return json.dumps({"response": {"aircraft": {"manufacturer": "Airbus", "type": "A320 251NSL",
                                                             "registered_owner": "Scandinavian Airlines System"}}}).encode()
            if "adsbdb.com" in url:
                return json.dumps({"response": "unknown callsign"}).encode()
            if "wheretheiss.at/v1/satellites" in url:
                return json.dumps({"latitude": 60.0, "longitude": 18.0, "altitude": 420.0, "velocity": 27600.0,
                                   "visibility": "daylight"}).encode()
            if "wheretheiss.at/v1/coordinates" in url:
                return json.dumps({"country_code": "SE"}).encode()
            if "polisen.se" in url:
                recent = (datetime.now(lookup.TZ) - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M:%S +02:00")
                return json.dumps([
                    {"datetime": recent, "name": "22 oktober 09.07, Brand, Stockholm", "summary": "Brand på Kungsholmen."},
                    {"datetime": recent, "name": "22 oktober 08.00, Stöld, Stockholm", "summary": "Cykelstöld i Bandhagen."},
                    {"datetime": "2026-01-01 10:00:00 +01:00", "name": "1 januari, Brand, Stockholm",
                     "summary": "Gammal brand på Kungsholmen."}]).encode()
            return RSS
        lookup.fetch = fake

    def tearDown(self):
        lookup.fetch = self.real_fetch

    def test_wikipedia(self):
        self.assertEqual(lookup.wikipedia("Anna  Book"),
                         (False, "Wikipedia (sv), Anna Book (svensk sångerska): Anna Book, född 10 september 1970."))
        error, text = lookup.wikipedia("ingenting")
        self.assertFalse(error)
        self.assertIn("ingen artikel", text)
        self.assertEqual(len([u for u in self.urls if "en.wikipedia" in u]), 1)   # tried English too
        self.assertTrue(lookup.wikipedia("  ")[0])

    def test_news_newest_first_and_cached(self):
        error, text = lookup.news("svt", 5)
        self.assertFalse(error)
        lines = text.splitlines()
        self.assertEqual(lines[0], "SVT Nyheter, senaste rubrikerna:")
        self.assertTrue(lines[1].endswith("Nyaste nyheten"))                  # no summary that repeats the title
        self.assertTrue(lines[2].endswith("Äldre nyhet (Sammanfattning)"))     # HTML stripped
        lookup.news("svt")
        self.assertEqual(len(self.urls), 1)                                    # the feed is kept 5 minutes
        self.assertTrue(lookup.news("okänd")[0])

    def test_planes(self):
        error, text = lookup.planes()
        lines = text.splitlines()
        self.assertFalse(error)
        self.assertEqual(lines[1], "SAS362G (Scandinavian Airlines System, Airbus A320 251NSL, SE-ROU): 10 km nordost "
                                   "om er, 610 m upp, 278 km/h på väg mot söder. Rutt: Paris till Stockholm.")
        self.assertEqual(lines[2], "utan anropssignal (A139, SE-JRK): 22 km sydväst om er, på marken.")

    def test_iss_visible_passes(self):
        """Checked against Skyfield on 2026-10-23: from Kungsholmen the ISS rises 05:48:18, peaks 05:51:01 and
        sets 05:53:45 the next morning with the sun 15 degrees down; the 04:13 pass is in the earth's shadow and
        the 07:24 one in twilight."""
        lookup._cache["iss-tle"] = (time.monotonic(), (
            "1 25544U 98067A   26295.98338418  .00009682  00000-0  18356-3 0  9999",
            "2 25544  51.6334  27.6879 0006864 297.6388  62.3903 15.49006294591569"))
        start = datetime(2026, 10, 23, 20, 0, tzinfo=lookup.TZ)
        passes = lookup.iss_passes(start)
        self.assertEqual([(f"{a:%H:%M}", f"{c:%H:%M}", d[1]) for a, b, c, d in passes], [("05:49", "05:53", 22)])
        self.assertEqual(lookup._passes_text(start), "Synliga passager härifrån det närmaste dygnet (minst 10° upp, "
                         "solbelyst, mörkt hos er): i morgon 05:49–05:53, från sydväst mot sydost, högst 22° kl. 05:51.")

    def test_iss_position_from_its_orbit_lines(self):
        """wheretheiss.at gave -31.926, 52.347, 425.0 km and 27 572 km/h, eclipsed, for 2026-10-23 20:00 Stockholm."""
        lookup._cache["iss-tle"] = (time.monotonic(), (
            "1 25544U 98067A   26295.98338418  .00009682  00000-0  18356-3 0  9999",
            "2 25544  51.6334  27.6879 0006864 297.6388  62.3903 15.49006294591569"))
        now = lookup._iss_now(datetime(2026, 10, 23, 20, 0, tzinfo=lookup.TZ))
        self.assertAlmostEqual(now["latitude"], -31.926, delta=0.05)
        self.assertAlmostEqual(now["longitude"], 52.347, delta=0.05)
        self.assertAlmostEqual(now["altitude"], 425.0, delta=1)
        self.assertAlmostEqual(now["velocity"], 27572, delta=5)
        self.assertEqual(now["visibility"], "eclipsed")
        error, text = lookup.iss()
        self.assertFalse(error)
        self.assertNotIn("wheretheiss.at/v1/satellites", " ".join(self.urls))   # one web call, for the country

    def test_iss_country_lookup_failing_is_not_called_the_sea(self):
        lookup._cache["iss-tle"] = (time.monotonic(), (
            "1 25544U 98067A   26295.98338418  .00009682  00000-0  18356-3 0  9999",
            "2 25544  51.6334  27.6879 0006864 297.6388  62.3903 15.49006294591569"))
        fake = lookup.fetch
        def failing(url, *args, **kwargs):
            if "coordinates" in url:
                raise OSError("down")
            return fake(url, *args, **kwargs)
        lookup.fetch = failing
        self.assertIn("okänt land", lookup.iss()[1])

    def test_iss_overhead(self):
        error, text = lookup.iss()
        self.assertFalse(error)
        self.assertIn("landkod SE", text)
        self.assertIn("ovanför er horisont", text)
        self.assertIn("solbelyst", text)

    def test_plane_route_and_model_start_together(self):
        original = lookup.fetch
        barrier = threading.Barrier(3, timeout=1)                     # route, model and operator

        def fetch(url, *args, **kwargs):
            if "adsb.lol" in url:
                return json.dumps({"ac": [{"flight": "SAS362G", "r": "SE-ROU", "dst": 1, "lat": 59.4, "lon": 18.1}]}).encode()
            barrier.wait()  # fails if model lookup waits for route lookup to return
            return original(url, *args, **kwargs)

        lookup.fetch = fetch
        error, text = lookup.planes()
        self.assertFalse(error)
        self.assertIn("Paris till Stockholm", text)
        self.assertIn("Airbus A320", text)

    def test_repeated_identifiers_are_fetched_once(self):
        original = lookup.fetch

        def fetch(url, *args, **kwargs):
            if "adsb.lol" in url:
                return json.dumps({"ac": [{"flight": "SAS362G", "r": "SE-ROU", "dst": distance}
                                          for distance in (1, 2)]}).encode()
            return original(url, *args, **kwargs)

        lookup.fetch = fetch
        self.assertFalse(lookup.planes()[0])
        self.assertEqual(len([u for u in self.urls if "callsign/SAS362G" in u]), 1)
        self.assertEqual(len([u for u in self.urls if "aircraft/SE-ROU" in u]), 1)

    def test_route_failure_does_not_drop_model_or_position(self):
        original = lookup.fetch

        def fetch(url, *args, **kwargs):
            if "callsign/" in url:
                raise OSError("route unavailable")
            return original(url, *args, **kwargs)

        lookup.fetch = fetch
        error, text = lookup.planes()
        self.assertFalse(error)
        self.assertIn("Airbus A320", text)
        self.assertNotIn("Paris till Stockholm", text)

    def test_route_must_fit_the_planes_position_and_course(self):
        over_kungsholmen = (59.40, 18.14)
        overflight = {"origin": HEL, "destination": CPH}
        self.assertTrue(lookup.route_fits(overflight, over_kungsholmen, 230))       # south-west, towards Copenhagen
        self.assertFalse(lookup.route_fits(overflight, over_kungsholmen, 50))       # flying away from it
        self.assertTrue(lookup.route_fits(overflight, over_kungsholmen, None))      # no course: position only
        self.assertFalse(lookup.route_fits({"origin": MAD, "destination": CDG}, over_kungsholmen, 220))  # stale
        self.assertTrue(lookup.route_fits({"origin": ARN, "destination": CDG}, (59.70, 17.95), 10))  # turns after take-off
        self.assertFalse(lookup.route_fits({"origin": {"municipality": "Paris"}, "destination": ARN},
                                           over_kungsholmen, 190))                  # no coordinates: left out

    def test_a_stale_route_is_left_out(self):
        original = lookup.fetch

        def fetch(url, *args, **kwargs):
            if "callsign/SAS362G" in url:
                return json.dumps({"response": {"flightroute": {
                    "origin": {"municipality": "Madrid", **MAD}, "destination": {"municipality": "Paris", **CDG}}}}).encode()
            return original(url, *args, **kwargs)

        lookup.fetch = fetch
        error, text = lookup.planes()
        self.assertFalse(error)
        self.assertEqual(text.splitlines()[1], "SAS362G (Scandinavian Airlines System, Airbus A320 251NSL, SE-ROU): "
                                               "10 km nordost om er, 610 m upp, 278 km/h på väg mot söder. Rutt okänd.")

    def test_no_plane_nearby_gives_the_nearest_within_100_km(self):
        original = lookup.fetch

        def fetch(url, *args, **kwargs):
            if "adsb.lol" in url and not url.endswith("/54"):         # 100 km is 54 nautical miles
                return json.dumps({"ac": []}).encode()
            return original(url, *args, **kwargs)

        lookup.fetch = fetch
        error, text = lookup.planes()
        self.assertFalse(error)
        self.assertTrue(text.startswith("Inga flygplan inom 25 km just nu. Flygplanen närmast er just nu, inom 100 km"))
        self.assertIn("SAS362G", text)
        lookup.fetch = lambda url, *args, **kwargs: json.dumps({"ac": []}).encode()
        lookup._cache.clear()
        self.assertEqual(lookup.planes(), (False, "Inga flygplan inom 25 km just nu (adsb.lol)."))

    def test_military_plane_gets_its_operator_and_an_unknown_route(self):
        original = lookup.fetch

        def fetch(url, *args, **kwargs):
            if "adsb.lol" in url:
                return json.dumps({"ac": [{"flight": "FNF471  ", "r": "PI-06", "t": "PC12", "dbFlags": 1,
                                           "alt_baro": 28000, "gs": 227, "track": 220, "dst": 13.5, "dir": 270,
                                           "lat": 59.22, "lon": 17.39}]}).encode()
            if "adsbdb.com/v0/airline/FNF" in url:
                return json.dumps({"response": [{"name": "Finnish Air Force", "icao": "FNF"}]}).encode()
            return original(url, *args, **kwargs)

        lookup.fetch = fetch
        error, text = lookup.planes()
        self.assertFalse(error)
        self.assertEqual(text.splitlines()[1], "FNF471 (Finnish Air Force, PC12, PI-06, militärt): 25 km väster om "
                                               "er, 8530 m upp, 420 km/h på väg mot sydväst. Rutt okänd: militärt "
                                               "flyg publicerar ingen.")

    def test_planes_without_identifiers_still_have_positions(self):
        lookup.fetch = lambda *args, **kwargs: json.dumps({"ac": [{"dst": 1, "alt_baro": "ground"}]}).encode()
        error, text = lookup.planes()
        self.assertFalse(error)
        self.assertIn("utan anropssignal", text)
        self.assertIn("på marken", text)

    def test_police_recent_and_filtered(self):
        error, text = lookup.police("Stockholm", "kungsholmen")
        self.assertFalse(error)
        self.assertIn("Brand på Kungsholmen.", text)
        self.assertNotIn("Bandhagen", text)
        self.assertNotIn("Gammal brand", text)                              # older than 24 hours
        self.assertIn("inga", lookup.police("Stockholm", "Solna")[1])

    def test_websearch(self):
        os.environ["JINA_API_KEY"] = "jina-test"
        try:
            error, text = lookup.websearch("AIK senaste match")
            self.assertFalse(error)
            self.assertIn("IK Oddevold 0 - 3 AIK", text)
            self.assertEqual(self.headers["X-Respond-With"], "no-content")      # snippets only: fast and cheap
            self.assertEqual(self.headers["Authorization"], "Bearer jina-test")
            self.assertIn("hl=sv", self.urls[-1])
            error, text = lookup.websearch(url="https://example.se/aik")
            self.assertFalse(error)
            self.assertLess(len(text), 5200)                                   # one page, cut to 5 000 characters
            self.assertTrue(lookup.websearch(url="file:///etc/passwd")[0])
            self.assertFalse(lookup.websearch("fail-once AIK")[0])            # a 502 is tried once more
            agent = openai_agent.LunaAgent(key="test", mock_tools=True)
            agent._ensure_tools()
            self.assertIn("webbsok", [t["name"] for t in agent.tools])
        finally:
            os.environ.pop("JINA_API_KEY", None)

    def test_agent_offers_and_runs_them(self):
        agent = openai_agent.LunaAgent(key="test", mock_tools=True)
        agent._ensure_tools()
        names = [t["name"] for t in agent.tools]
        self.assertIn("wikipedia", names)
        self.assertIn("nyheter", names)
        self.assertTrue({"wikipedia", "nyheter"} <= agent.read_only)
        self.assertIn("1970", agent.run_tool("wikipedia", {"query": "Anna Book"})[1])
        self.assertIn("Nyaste nyheten", agent.run_tool("nyheter", {"source": "sport"})[1])
        self.assertTrue({"flygplan", "iss", "polisen"} <= set(names))
        self.assertIn("SAS362G", agent.run_tool("flygplan", {})[1])


class TVPromptTest(unittest.TestCase):
    def test_tv_rules_replace_the_cannot_line(self):
        """The TV rules go in by replacing one sentence of the prompt; a reworded sentence would drop them
        silently and Jarvis would say he cannot run the TV."""
        import subprocess
        code = ("import hallpanel_server as h; print('kan du inte styra' in h.INSTRUCTIONS, "
                "'play_latest_youtube' in h.INSTRUCTIONS, 'power' in h.INSTRUCTIONS)")
        for tv, want in (("", "True False False"), ("http://tv.invalid/mcp", "False True True")):
            env = {**os.environ, "JARVIS_TV_MCP": tv, "JARVIS_MOCK_TOOLS": "0",
                   "PYTHONPATH": f"{ROOT / 'server'}:{ROOT / 'server' / 'agents'}"}
            out = subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual(out.stdout.strip().splitlines()[-1:], [want], out.stderr[-500:])


class FakeTV:
    def __init__(self, url, token):
        self.calls = []

    def start(self):
        return [{"name": "tv_status", "annotations": {"readOnlyHint": True}, "inputSchema": {"type": "object"}},
                {"name": "play_streaming", "inputSchema": {"type": "object"}},
                {"name": "play_latest_youtube", "inputSchema": {"type": "object"}},
                {"name": "press_keys", "inputSchema": {"type": "object"}}]

    def run(self, name, args, timeout=60):
        self.calls.append((name, args, timeout))
        if name == "play_streaming":
            raise OSError("timed out")
        return False, "Apple TV: av"


class TVTest(unittest.TestCase):
    def setUp(self):
        self.real = agent_core.MCPClient
        agent_core.MCPClient = FakeTV
        os.environ["JARVIS_TV_MCP"] = "http://tv.invalid/mcp"

    def tearDown(self):
        agent_core.MCPClient = self.real
        os.environ.pop("JARVIS_TV_MCP", None)

    def test_only_allowed_tv_tools_and_errors_answer(self):
        agent = openai_agent.LunaAgent(key="test", mock_tools=True)
        agent._ensure_tools()
        names = [t["name"] for t in agent.tools]
        self.assertIn("tv_status", names)
        self.assertIn("play_streaming", names)
        self.assertIn("play_latest_youtube", names)             # "sätt på X:s senaste video"
        self.assertNotIn("press_keys", names)                   # not offered by voice
        agent.agenda = (0, "en sak")
        self.assertEqual(agent.run_tool("play_streaming", {"title": "The White Lotus"}),
                         (True, "Tv-styrningen svarade inte (OSError)."))
        self.assertIsNotNone(agent.agenda)                      # a TV command leaves the calendar alone
        self.assertEqual(agent.tv.calls[0][2], 50)

    def test_tv_down_leaves_the_rest(self):
        def down(self):
            raise OSError("connection refused")
        FakeTV.start, real_start = down, FakeTV.start
        try:
            agent = openai_agent.LunaAgent(key="test", mock_tools=True)
            agent._ensure_tools()
            self.assertIn("wikipedia", [t["name"] for t in agent.tools])
            self.assertNotIn("tv_status", [t["name"] for t in agent.tools])
        finally:
            FakeTV.start = real_start


if __name__ == "__main__":
    unittest.main()
