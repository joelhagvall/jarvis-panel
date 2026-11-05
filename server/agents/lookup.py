#!/usr/bin/env python3
"""Free, read-only lookups for Jarvis: Wikipedia and news feeds. Standard library only, no keys.

  wikipedia(query)       the introduction of the best Wikipedia match, Swedish first, else English
                         (people with their dates of birth, places, history, explanations)
  news(source, count)    the latest headlines from an RSS feed: svt, stockholm, sport, dn, aftonbladet,
                         expressen or bbc. Each feed is kept 5 minutes.
  planes(radius_km)      the aircraft nearest home right now (adsb.lol, crowd-sourced receivers), with
                         airline, type, height, speed and heading, and the route from adsbdb's database
                         when it fits the plane's position and course
  iss()                  where the International Space Station is now (wheretheiss.at): over which country,
                         how far from home and how high above or below our horizon, and when it can be seen
                         from home the next day (its orbit from ARISS, worked out with sgp4)
  police(place, query)   the police's latest events (polisen.se) for a municipality or county, optionally
                         only those that mention a word such as "Kungsholmen"
  websearch(query, url)  web search through Jina (s.jina.ai) with the key JINA_API_KEY or
                         ~/.config/hallpanel/jina.env: titles, snippets and addresses only (about 1.7 s and
                         10 000 tokens a search; with the pages' text it took 21 s and 51 000), or with url the
                         text of one page (r.jina.ai). Offered only when there is a key.

Both return (is_error, text) for the agent's tool loop. The text comes from other people's sites, so it
is data for the model, never instructions (the prompt says so too).

  python3 server/agents/lookup.py wikipedia "Anna Book"
  python3 server/agents/lookup.py news sport
  python3 server/agents/lookup.py planes | iss | police Kungsholmen
"""
import email.utils
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from env_files import env_file

TZ = ZoneInfo("Europe/Stockholm")
AGENT = "hallpanel/1 (Jarvis, a home voice assistant; https://github.com/joelhagvall/jarvis-panel)"
MAX_BYTES = 2_000_000
FEEDS = {
    "svt": ("SVT Nyheter", "https://www.svt.se/nyheter/rss.xml"),
    "stockholm": ("SVT Stockholm", "https://www.svt.se/nyheter/lokalt/stockholm/rss.xml"),
    "sport": ("SVT Sport", "https://www.svt.se/sport/rss.xml"),
    "dn": ("DN", "https://www.dn.se/rss/"),
    "aftonbladet": ("Aftonbladet", "https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/"),
    "expressen": ("Expressen", "https://www.expressen.se/rss/nyheter"),
    "bbc": ("BBC News", "https://feeds.bbci.co.uk/news/world/rss.xml"),
}
HOME = (59.33, 18.03)                         # Kungsholmen, as for SMHI in home_status.py
_cache = {}                                     # url -> (time.monotonic(), parsed data)


def fetch(url, timeout=4, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": AGENT, **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(MAX_BYTES)


def wikipedia(query, langs=("sv", "en")):
    query = " ".join(str(query or "").split())[:200]
    if not query:
        return True, "Ange vad som ska slås upp."
    for lang in langs:
        params = {"action": "query", "format": "json", "formatversion": "2", "redirects": "1",
                  "generator": "search", "gsrsearch": query, "gsrlimit": "1",
                  "prop": "extracts|description", "exintro": "1", "explaintext": "1", "exchars": "1200"}
        try:
            data = json.loads(fetch(f"https://{lang}.wikipedia.org/w/api.php?" + urllib.parse.urlencode(params)))
        except (OSError, ValueError, urllib.error.URLError) as e:
            return True, f"Wikipedia svarade inte ({type(e).__name__})."
        pages = (data.get("query") or {}).get("pages") or []
        if pages and pages[0].get("extract"):
            page = pages[0]
            head = page["title"] + (f" ({page['description']})" if page.get("description") else "")
            return False, f"Wikipedia ({lang}), {head}: {page['extract'].strip()}"
    return False, f"Wikipedia har ingen artikel som passar \"{query}\"."


def _text(node, tag):
    found = node.find(tag)
    raw = (found.text or "") if found is not None else ""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", raw)).split())


def _items(url):
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < 300:
        return hit[1]
    root = ET.fromstring(fetch(url))
    items = []
    for item in root.iter("item"):
        when = None
        if stamp := _text(item, "pubDate"):
            try:
                when = email.utils.parsedate_to_datetime(stamp).astimezone(TZ)
            except (TypeError, ValueError):
                pass
        items.append({"title": _text(item, "title"), "summary": _text(item, "description"), "when": when})
    items.sort(key=lambda i: i["when"] or datetime.min.replace(tzinfo=TZ), reverse=True)
    _cache[url] = (time.monotonic(), items)
    return items


def news(source="svt", count=8, shown=None):
    """The latest headlines as text for the model. shown(name, titles), when given, gets the same headlines
    for the panel's card (screen.Screen.offer_news)."""
    key = str(source or "svt").lower().strip()
    if key not in FEEDS:
        return True, f"Okänd källa {source}. Välj en av: {', '.join(FEEDS)}."
    name, url = FEEDS[key]
    try:
        count = max(1, min(int(count or 8), 15))
    except (TypeError, ValueError):
        count = 8
    try:
        items = _items(url)
    except (OSError, ET.ParseError, urllib.error.URLError) as e:
        return True, f"{name} svarade inte ({type(e).__name__})."
    today = datetime.now(TZ).date()
    lines = []
    for i in items[:count]:
        when = ""
        if i["when"]:
            when = i["when"].strftime("%H:%M" if i["when"].date() == today else "%-d/%-m %H:%M") + " "
        summary = f" ({i['summary'][:200]})" if i["summary"] and i["summary"] != i["title"] else ""
        lines.append(f"{when}{i['title']}{summary}")
    if shown and items[:count]:
        shown(name, [i["title"] for i in items[:count]])
    return False, f"{name}, senaste rubrikerna:\n" + ("\n".join(lines) or "(inga rubriker)")


def _json_cached(url, keep_s, timeout=4):
    hit = _cache.get(url)
    if hit and time.monotonic() - hit[0] < keep_s:
        return hit[1]
    data = json.loads(fetch(url, timeout))
    _cache[url] = (time.monotonic(), data)
    return data


def compass(degrees):
    names = ["norr", "nordost", "öster", "sydost", "söder", "sydväst", "väster", "nordväst"]
    return names[round((degrees or 0) % 360 / 45) % 8]


def km(value):
    return f"{value:.0f}" if value >= 10 else f"{value:.1f}".replace(".", ",")


def _response(data, key):
    """adsbdb's answer under key; for an unknown callsign or aircraft, "response" is a plain string."""
    response = data.get("response") if isinstance(data, dict) else None
    return response.get(key) if isinstance(response, dict) else None


def _bearing(start, end):
    """Initial compass bearing in degrees from start to end, both (lat, lon)."""
    la1, lo1, la2, lo2 = map(math.radians, (*start, *end))
    y = math.sin(lo2 - lo1) * math.cos(la2)
    x = math.cos(la1) * math.sin(la2) - math.sin(la1) * math.cos(la2) * math.cos(lo2 - lo1)
    return math.degrees(math.atan2(y, x)) % 360


def route_fits(route, here, track):
    """True if a plane at here (lat, lon) flying track fits adsbdb's route. adsbdb knows routes by callsign
    and they go stale, so a route is told only when the plane lies roughly on the way from origin to
    destination and, unless it is near one of the airports (where planes turn), flies towards the
    destination. A route that does not fit is left out rather than told with a caveat."""
    try:
        origin = (float(route["origin"]["latitude"]), float(route["origin"]["longitude"]))
        dest = (float(route["destination"]["latitude"]), float(route["destination"]["longitude"]))
    except (KeyError, TypeError, ValueError):
        return False
    from_origin, to_dest = _ground_km(*here, home=origin), _ground_km(*dest, home=here)
    if from_origin + to_dest > 1.25 * _ground_km(*dest, home=origin) + 100:
        return False
    if track is None or min(from_origin, to_dest) < 60:
        return True
    return abs((track - _bearing(here, dest) + 180) % 360 - 180) <= 90


def planes(radius_km=25):
    """The aircraft nearest home, at most four, with their routes when they fit the plane's course."""
    try:
        radius_km = max(2, min(int(radius_km or 25), 100))
    except (TypeError, ValueError):
        radius_km = 25
    nm = radius_km / 1.852
    try:
        data = _json_cached(f"https://api.adsb.lol/v2/point/{HOME[0]}/{HOME[1]}/{nm:.0f}", 10)
    except (OSError, ValueError, urllib.error.URLError) as e:
        return True, f"Flygdata svarade inte ({type(e).__name__})."
    aircraft = sorted((a for a in data.get("ac") or [] if a.get("dst") is not None), key=lambda a: a["dst"])[:4]
    if not aircraft:
        if radius_km < 100:                     # none close by: the nearest within 100 km rather than nothing
            error, wider = planes(100)
            if not error and not wider.startswith("Inga"):
                return False, f"Inga flygplan inom {radius_km} km just nu. " + wider
        return False, f"Inga flygplan inom {radius_km} km just nu (adsb.lol)."

    # Both identifiers are already in the position response: route, model and the operator behind the
    # callsign's three-letter prefix (FNF is Finnish Air Force, which publishes no routes) can be fetched at
    # once. One flat pool bounds the total to twelve requests, and repeated identifiers share one lookup.
    requests = {}
    for a in aircraft:
        callsign = (a.get("flight") or "").strip()
        prefix = callsign[:3] if re.fullmatch(r"[A-Z]{3}\d\w*", callsign) else ""
        for key, identifier, endpoint, keep_s in (
            ("flightroute", callsign, "callsign", 3600),
            ("aircraft", (a.get("r") or "").strip(), "aircraft", 86400),
            ("airline", prefix, "airline", 86400),
        ):
            if identifier:
                requests[key, identifier] = (f"https://api.adsbdb.com/v0/{endpoint}/{urllib.parse.quote(identifier)}", keep_s)

    def detail(item):
        key, (url, keep_s) = item
        try:
            data = _json_cached(url, keep_s, 3)
            # the airline endpoint answers a list under "response"; the others a dict under their key
            result = (data.get("response") or [None])[0] if key[0] == "airline" else _response(data, key[0])
        except (OSError, ValueError, urllib.error.URLError, AttributeError, IndexError, KeyError):
            result = None
        return key, result if isinstance(result, dict) else None

    extra = {}
    if requests:
        with ThreadPoolExecutor(max_workers=len(requests)) as pool:
            extra = dict(pool.map(detail, requests.items()))
    lines = [f"Flygplanen närmast er just nu, inom {radius_km} km (adsb.lol):"]
    for a in aircraft:
        callsign, reg = (a.get("flight") or "").strip(), (a.get("r") or "").strip()
        route, plane = extra.get(("flightroute", callsign)), extra.get(("aircraft", reg))
        operator = extra.get(("airline", callsign[:3])) or {}
        military = bool((a.get("dbFlags") or 0) & 1)            # adsb.lol's database flag for military aircraft
        callsign = callsign or "utan anropssignal"
        airline = ((route or {}).get("airline") or {}).get("name")
        who = [x for x in (airline, (plane or {}).get("registered_owner"), operator.get("name")) if x]
        kind = " ".join(x for x in ((plane or {}).get("manufacturer"), (plane or {}).get("type")) if x) or a.get("t")
        name = ", ".join(dict.fromkeys(x for x in (who[0] if who else None, kind, a.get("r")) if x))
        alt = a.get("alt_baro")
        height = "på marken" if alt == "ground" else f"{round((alt or 0) * 0.3048, -1):.0f} m upp"
        speed = f"{(a.get('gs') or 0) * 1.852:.0f} km/h"
        moving = "" if alt == "ground" else f", {speed}" + (
            f" på väg mot {compass(a.get('track'))}" if a.get("track") is not None else "")
        line = (f"{callsign} ({name}{', militärt' if military else ''}): {km(a['dst'] * 1.852)} km "
                f"{compass(a.get('dir'))} om er, {height}{moving}.")
        if route and None not in (a.get("lat"), a.get("lon")) and route_fits(
                route, (a["lat"], a["lon"]), None if alt == "ground" else a.get("track")):
            o, d = route["origin"], route["destination"]
            line += f" Rutt: {o.get('municipality') or o.get('name')} till {d.get('municipality') or d.get('name')}."
        elif alt != "ground":
            line += " Rutt okänd: militärt flyg publicerar ingen." if military else " Rutt okänd."
        lines.append(line)
    return False, "\n".join(lines)


def _elevation(lat, lon, alt_km, home=HOME):
    """Degrees above (or below) home's horizon of a point lat/lon at alt_km, on a spherical Earth."""
    r = 6371.0
    def ecef(la, lo, h):
        la, lo = math.radians(la), math.radians(lo)
        return [(r + h) * math.cos(la) * math.cos(lo), (r + h) * math.cos(la) * math.sin(lo), (r + h) * math.sin(la)]
    o, t = ecef(*home, 0), ecef(lat, lon, alt_km)
    d = [t[i] - o[i] for i in range(3)]
    up = [x / r for x in o]
    dist = math.sqrt(sum(x * x for x in d))
    return math.degrees(math.asin(sum(d[i] * up[i] for i in range(3)) / dist))


def _ground_km(lat, lon, home=HOME):
    la1, lo1, la2, lo2 = map(math.radians, (*home, lat, lon))
    h = math.sin((la2 - la1) / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin((lo2 - lo1) / 2) ** 2
    return 2 * 6371.0 * math.asin(math.sqrt(h))


TLE_URLS = ("https://live.ariss.org/iss.txt",                                    # ARISS, the ISS's radio amateurs
            "https://celestrak.org/NORAD/elements/gp.php?CATNR=25544&FORMAT=TLE")
EARTH_KM = 6371.0


def _iss_tle():
    """The ISS's two orbit lines from ARISS (Celestrak if not), kept six hours."""
    hit = _cache.get("iss-tle")
    if hit and time.monotonic() - hit[0] < 6 * 3600:
        return hit[1]
    for url in TLE_URLS:
        try:
            lines = [l.strip() for l in fetch(url, 5).decode("ascii", "replace").splitlines()]
        except (OSError, urllib.error.URLError):
            continue
        tle = next(((a, b) for a, b in zip(lines, lines[1:]) if a.startswith("1 25544") and b.startswith("2 25544")), None)
        if tle:
            _cache["iss-tle"] = (time.monotonic(), tle)
            return tle
    raise OSError("no ISS orbit lines")


def _sky(sat, when, home=HOME):
    """(elevation and azimuth of the ISS from home in degrees, sunlit, the sun's elevation in degrees) at when (UTC).
    SGP4 gives the ISS in TEME; home is turned into it by Greenwich sidereal time, and the sun is the Astronomical
    Almanac's low-precision position: good to a fraction of a degree, plenty to say when it can be seen."""
    from sgp4.api import jday
    jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute, when.second + when.microsecond / 1e6)
    error, r, _ = sat.sgp4(jd, fr)
    if error:
        return None
    d = jd + fr - 2451545.0
    gmst = math.radians((280.46061837 + 360.98564736629 * d) % 360)
    lat, lon = math.radians(home[0]), math.radians(home[1])
    a, e2 = 6378.137, 0.00669437999014                      # WGS84
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    th = gmst + lon
    obs = (n * math.cos(lat) * math.cos(th), n * math.cos(lat) * math.sin(th), n * (1 - e2) * math.sin(lat))
    up = (math.cos(lat) * math.cos(th), math.cos(lat) * math.sin(th), math.sin(lat))
    east = (-math.sin(th), math.cos(th), 0.0)
    north = (-math.sin(lat) * math.cos(th), -math.sin(lat) * math.sin(th), math.cos(lat))
    rho = [r[i] - obs[i] for i in range(3)]
    dist = math.sqrt(sum(x * x for x in rho))
    dot = lambda u, v: sum(u[i] * v[i] for i in range(3))   # noqa: E731
    elevation = math.degrees(math.asin(dot(rho, up) / dist))
    azimuth = math.degrees(math.atan2(dot(rho, east), dot(rho, north))) % 360
    sun = _sun(d)
    return elevation, azimuth, _sunlit(r, sun), math.degrees(math.asin(dot(sun, up)))


def _sun(d):
    """Unit vector to the sun, d days after J2000 (the Astronomical Almanac's low-precision formula)."""
    g = math.radians((357.529 + 0.98560028 * d) % 360)
    ecliptic = math.radians((280.459 + 0.98564736 * d + 1.915 * math.sin(g) + 0.020 * math.sin(2 * g)) % 360)
    tilt = math.radians(23.439 - 0.00000036 * d)
    return math.cos(ecliptic), math.cos(tilt) * math.sin(ecliptic), math.sin(tilt) * math.sin(ecliptic)


def _sunlit(r, sun):
    """Whether a point r (km, TEME) is outside the earth's shadow, taken as a cylinder behind it."""
    along = sum(r[i] * sun[i] for i in range(3))
    return along > 0 or math.sqrt(max(sum(x * x for x in r) - along * along, 0)) > EARTH_KM


def _iss_now(when=None):
    """Where the ISS is, worked out from its orbit lines with sgp4 instead of asking wheretheiss.at (a second
    web call, 2026-10-23): {"latitude", "longitude", "altitude" km, "velocity" km/h, "visibility"}."""
    from sgp4.api import Satrec, jday
    when = (when or datetime.now(TZ)).astimezone(ZoneInfo("UTC"))
    jd, fr = jday(when.year, when.month, when.day, when.hour, when.minute, when.second + when.microsecond / 1e6)
    error, r, v = Satrec.twoline2rv(*_iss_tle()).sgp4(jd, fr)
    if error:
        raise ValueError(f"sgp4 error {error}")
    d = jd + fr - 2451545.0
    gmst = math.radians((280.46061837 + 360.98564736629 * d) % 360)
    a, e2 = 6378.137, 0.00669437999014                      # WGS84
    p = math.hypot(r[0], r[1])
    lat = math.atan2(r[2], p * (1 - e2))
    for _ in range(5):
        n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
        lat = math.atan2(r[2] + e2 * n * math.sin(lat), p)
    n = a / math.sqrt(1 - e2 * math.sin(lat) ** 2)
    lon = (math.degrees(math.atan2(r[1], r[0]) - gmst) + 180) % 360 - 180
    return {"latitude": math.degrees(lat), "longitude": lon, "altitude": p / math.cos(lat) - n,
            "velocity": math.sqrt(sum(x * x for x in v)) * 3600,
            "visibility": "daylight" if _sunlit(r, _sun(d)) else "eclipsed"}


def iss_passes(start=None, hours=24, step_s=20, home=HOME):
    """The ISS's passes the next hours that can be seen from home with the naked eye: at least 10 degrees up,
    in sunlight, while the sun is 6 degrees or more below the horizon here, for a minute or more. A list of
    (first seen, highest, last seen, (azimuth first, highest elevation, azimuth last)), local times."""
    from sgp4.api import Satrec
    sat = Satrec.twoline2rv(*_iss_tle())
    start = (start or datetime.now(TZ)).astimezone(TZ)

    def seen(when):
        sky = _sky(sat, when.astimezone(ZoneInfo("UTC")), home)
        return sky if sky is not None and sky[0] >= 10 and sky[2] and sky[3] <= -6 else None

    def edge(before, after):
        """The first second that differs from before, between two samples: start and end to the second."""
        was = seen(before) is not None
        while (after - before).total_seconds() > 1:
            middle = before + (after - before) / 2
            before, after = (middle, after) if (seen(middle) is not None) == was else (before, middle)
        return after.replace(microsecond=0)

    passes, current, previous = [], None, start
    for k in range(int(hours * 3600 / step_s) + 1):
        when = start + timedelta(seconds=k * step_s)
        sky = seen(when)
        if sky:
            if current is None:
                first = edge(previous, when) if k else when
                current = {"first": first, "az_first": seen(first)[1] if seen(first) else sky[1],
                           "top": when, "max": sky[0]}
            if sky[0] > current["max"]:
                current.update(top=when, max=sky[0])
            current.update(last=when, az_last=sky[1])
        elif current is not None:
            last = edge(current["last"], when) - timedelta(seconds=1)
            if last - current["first"] >= timedelta(minutes=1):      # a glimpse of seconds is not worth going out for
                passes.append((current["first"], current["top"], last,
                               (current["az_first"], round(current["max"]), current["az_last"])))
            current = None
        previous = when
    return passes


def iss():
    try:
        try:
            now = _iss_now()
        except (ImportError, OSError, ValueError):
            now = _json_cached("https://api.wheretheiss.at/v1/satellites/25544", 5)
        lat, lon = now["latitude"], now["longitude"]
        try:
            under = _json_cached(f"https://api.wheretheiss.at/v1/coordinates/{lat:.2f},{lon:.2f}", 5,
                                 timeout=2).get("country_code")      # took 0.8-2.0 s on Jarvis
        except (OSError, ValueError, urllib.error.URLError):
            under = None
    except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
        return True, f"ISS-tjänsten svarade inte ({type(e).__name__})."
    where = (f"{abs(lat):.1f}° {'N' if lat >= 0 else 'S'}, {abs(lon):.1f}° {'O' if lon >= 0 else 'V'}").replace(".", ",")
    land = (f"landkod {under}" if under and under != "??" else "inget land, alltså hav eller is" if under
            else "okänt land (uppslaget svarade inte)")
    up = _elevation(lat, lon, now["altitude"])
    sky = (f"ovanför er horisont, {up:.0f}° upp" if up > 0 else f"under er horisont ({up:.0f}°)")
    lit = "solbelyst" if now.get("visibility") == "daylight" else "i jordens skugga"
    return False, (f"ISS just nu: {where}, under den {land}. {now['altitude']:.0f} km upp, "
                   f"{now['velocity']:.0f} km/h, {lit}. Från er: {_ground_km(lat, lon):.0f} km längs marken, {sky}. "
                   + _passes_text())


def _passes_text(start=None):
    """When the ISS can be seen from home the next day, or why that is not known."""
    try:
        passes = iss_passes(start)
    except ImportError:
        return "Passagerna kan inte räknas ut här (paketet sgp4 saknas)."
    except (OSError, ValueError):
        return "Passagerna gick inte att räkna ut just nu (banelementen svarade inte)."
    if not passes:
        return ("Synliga passager härifrån det närmaste dygnet: inga. Den passerar bara i dagsljus, i jordens skugga "
                "eller för lågt.")
    today = (start or datetime.now(TZ)).astimezone(TZ).date()
    def day(t):
        return "i dag" if t.date() == today else "i morgon" if t.date() == today + timedelta(days=1) else t.strftime("%-d/%-m")
    lines = [f"{day(first)} {first:%H:%M}–{last:%H:%M}, från {compass(az0)} mot {compass(az1)}, högst {top}° kl. "
             f"{peak:%H:%M}" for first, peak, last, (az0, top, az1) in passes]
    return ("Synliga passager härifrån det närmaste dygnet (minst 10° upp, solbelyst, mörkt hos er): "
            + "; ".join(lines) + ".")


def police(place="Stockholm", query=None, hours=24, count=8):
    place = " ".join(str(place or "Stockholm").split())[:60]
    try:
        hours = max(1, min(int(hours or 24), 168))
    except (TypeError, ValueError):
        hours = 24
    url = "https://polisen.se/api/events?" + urllib.parse.urlencode({"locationname": place})
    try:
        events = _json_cached(url, 120, 6)
    except (OSError, ValueError, urllib.error.URLError) as e:
        return True, f"Polisens sida svarade inte ({type(e).__name__})."
    since = datetime.now(TZ) - timedelta(hours=hours)
    words = [w.lower() for w in str(query or "").split()]
    picked = []
    for e in events if isinstance(events, list) else []:
        try:
            when = datetime.strptime(e["datetime"], "%Y-%m-%d %H:%M:%S %z")
        except (KeyError, ValueError):
            continue
        text = f"{e.get('name', '')} {e.get('summary', '')}"
        if when >= since and all(w in text.lower() for w in words):
            picked.append(f"{e.get('name', '').strip()}: {e.get('summary', '').strip()}")
    head = f"Polisens händelser i {place} de senaste {hours} timmarna" + (f" som nämner {query}" if words else "")
    if not picked:
        return False, head + ": inga."
    return False, head + " (polisens egna korta sammanfattningar, senast publicerade först):\n" + "\n".join(picked[:max(1, min(int(count or 8), 15))])


def jina_key():
    """JINA_API_KEY from the environment or ~/.config/hallpanel/jina.env, or None."""
    return os.environ.get("JINA_API_KEY") or env_file("jina.env").get("JINA_API_KEY") or None


def websearch(query=None, url=None, count=6):
    key = jina_key()
    if not key:
        return True, "Webbsök är inte påslaget (ingen Jina-nyckel)."
    auth = {"Authorization": f"Bearer {key}"}
    if url:
        url = str(url).strip()
        if not re.match(r"https?://", url):
            return True, "Ange en hel adress som börjar med https://."
        try:
            # 8 s at most: a voice answer would rather go without the page than keep him waiting
            text = fetch("https://r.jina.ai/" + url, 8, {**auth, "X-Retain-Images": "none"}).decode("utf-8", "replace")
        except (OSError, urllib.error.URLError) as e:
            return True, f"Sidan gick inte att läsa ({type(e).__name__})."
        return False, f"Sidan {url} (de första 5 000 tecknen):\n" + text[:5000]
    query = " ".join(str(query or "").split())[:200]
    if not query:
        return True, "Ange vad som ska sökas."
    params = urllib.parse.urlencode({"q": query, "gl": "SE", "hl": "sv"})
    for attempt in (1, 2):                      # one more try: Jina now and then fails a search that works again
        try:
            data = json.loads(fetch("https://s.jina.ai/?" + params, 5,
                                    {**auth, "Accept": "application/json", "X-Respond-With": "no-content"}))
            break
        except (OSError, ValueError, urllib.error.URLError) as e:
            code = getattr(e, "code", None)
            print(f"web search failed (attempt {attempt}): {type(e).__name__} {code or ''} {e}"[:300], file=sys.stderr)
            if attempt == 2 or code in (400, 401, 402, 403):
                return True, f"Webbsöket svarade inte ({type(e).__name__}{f' {code}' if code else ''})."
            time.sleep(0.5)
    hits = [h for h in data.get("data") or [] if h.get("title") or h.get("description")][:max(1, min(int(count or 6), 10))]
    if not hits:
        return False, f"Webbsöket hittade inget om \"{query}\"."
    lines = [f"- {' '.join((h.get('title') or '').split())}: {' '.join((h.get('description') or '').split())[:300]} ({h.get('url')})"
             for h in hits]
    return False, (f"Webbsök efter \"{query}\" (rubrik, utdrag och adress; utdragen kan vara gamla, "
                   "kolla datum):\n" + "\n".join(lines))


if __name__ == "__main__":
    kind, *rest = sys.argv[1:] or ["news"]
    t0 = time.monotonic()
    run = {"wikipedia": lambda: wikipedia(" ".join(rest)), "news": lambda: news(*(rest[:1] or ["svt"])),
           "planes": lambda: planes(*rest[:1]), "iss": iss,
           "police": lambda: police("Stockholm", " ".join(rest) or None),
           "search": lambda: websearch(" ".join(rest)), "read": lambda: websearch(url=rest[0])}[kind]
    error, text = run()
    print(("FEL: " if error else "") + text)
    print(f"({time.monotonic() - t0:.2f} s)")
