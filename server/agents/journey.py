"""SL Journey Planner v2: time-specific trips, including walking and an optional return.

Free, read-only, no API key. https://www.trafiklab.se/api/other-apis/sl/journey-planner-2/
Stop lookups are cached; outbound and return requests run in parallel. No model calls here.
"""
import json
import os
import re
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from zoneinfo import ZoneInfo

from lookup import fetch

BASE = "https://journeyplanner.integration.sl.se/v2/"
SOURCE = "https://www.trafiklab.se/api/other-apis/sl/journey-planner-2/"
TZ = ZoneInfo("Europe/Stockholm")
# SL's stop finder has no airport called "Arlanda flygplats": it answered Bromma flygplats as its best match
# (2026-10-23), and the terminals' bus stops give no trip. The pendeltåg station is under the terminals.
ARLANDA = re.compile(r"(stockholm[- ])?arlanda( flygplats| airport| c| central(station)?| terminal \d( och \d)?)?", re.I)
MIN_MATCH = 900     # SL's matchQuality; real places scored 938-1000, Bromma for "Arlanda flygplats" 549


def request(endpoint, **params):
    data = json.loads(fetch(BASE + endpoint + "?" + urllib.parse.urlencode(params), timeout=6))
    if not isinstance(data, dict):
        raise ValueError("SL gav ett ogiltigt svar")
    return data


# What a spoken place often carries that SL's stop finder scores down: "Zinkensdamm tunnelbana" scored 567 and
# "Zinkensdamm, Södermalm, Stockholm" 700 against 1000 for "Zinkensdamm", and "Zinkens damm" 836 (2026-11-04).
STOP_WORDS = re.compile(r"\s+(tunnelbanestation|tunnelbana|t-bana|tbana|pendeltågsstation|pendeltåg|station|"
                        r"busshållplats|hållplats)$", re.I)


def variants(name):
    """name, then the forms SL scores higher: the first part before a comma, without a trailing "tunnelbana" or
    "station", and with its spaces closed ("Zinkens damm"). An address (a number in it) is only cut at the comma."""
    out = [name.strip()]
    first = name.split(",")[0].strip()
    out.append(first)
    bare = STOP_WORDS.sub("", first).strip()
    out.append(bare)
    words = bare.split()
    if 1 < len(words) <= 3 and not any(c.isdigit() for c in bare):
        out.append("".join(words[:1] + [w.lower() for w in words[1:]]))
    return [v for v in dict.fromkeys(out) if v]


def pick(points, name):
    """The one place SL's answer is sure of for name, or None."""
    points = [p for p in points if p.get("matchQuality", MIN_MATCH) >= MIN_MATCH]
    best = [p for p in points if p.get("isBest")]
    if len(best) == 1:
        return best[0]
    exact = [p for p in points if str(p.get("disassembledName") or p["name"]).casefold() == name.casefold()]
    if len(exact) == 1:
        return exact[0]
    if len(points) == 1:
        return points[0]
    return None


@lru_cache(maxsize=128)
def location(name):
    if ARLANDA.fullmatch(name.strip()):
        name = "Arlanda central"
    seen = []
    for query in variants(name):
        points = request("stop-finder", name_sf=query, type_sf="any", any_obj_filter_sf=46).get("locations") or []
        points = [p for p in points if p.get("id") and p.get("name")]
        if (found := pick(points, query)) is not None:
            return {k: found[k] for k in ("id", "name")}
        seen += points
    # Nothing sure: SL's closest names, so the model can ask "menar du Zinkensdamm?" instead of only saying the
    # place is unclear. A name that sounds the same comes first: speech recognition heard Zinkensdamm as
    # "Sinkenstam", and SL scored Tensta sim- och idrottshall above it (2026-11-04).
    seen.sort(key=lambda p: (sound(p.get("disassembledName") or p["name"]) != sound(name), -p.get("matchQuality", 0)))
    options = " / ".join(list(dict.fromkeys(p["name"] for p in seen))[:5])     # SL names hold commas
    raise ValueError(f"Platsen {name!r} är oklar. Precisera plats eller adress"
                     + (f". Närmast hos SL, osäkert: {options}" if options else "."))


def sound(text):
    """A rough Swedish sound of a place name: s for z, t for d, no spaces or doubled letters."""
    text = re.sub(r"[^a-zåäö]", "", str(text).casefold().replace("z", "s").replace("d", "t"))
    return re.sub(r"(.)\1+", r"\1", text)


def timestamp(value):
    if not isinstance(value, str) or "T" not in value:
        raise ValueError("Ange datum och tid som YYYY-MM-DDTHH:MM, svensk tid, eller med UTC-offset")
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        first, second = dt.replace(tzinfo=TZ, fold=0), dt.replace(tzinfo=TZ, fold=1)
        if first.utcoffset() != second.utcoffset():
            raise ValueError("Tiden ligger vid sommartidsbyte; ange UTC-offset")
        dt = first
    return dt.astimezone(timezone.utc)


def iso(dt):
    return dt.astimezone(TZ).isoformat(timespec="seconds")


def to_minutes(text):
    """The result's times to the minute, for the model only: SL gives seconds ("10:33:54") and a model reads
    them out. Everything above is worked out from the exact times, so the calendar still rounds outward."""
    return re.sub(r"(T\d\d:\d\d):\d\d([+-]\d\d:\d\d|Z)", r"\1:00\2", text)


def first_vehicle(trip):
    """Boarding is not leaving home/the venue: a trip may start with a walking leg."""
    return next((leg for leg in trip["legs"] if leg["mode"] != "gång"), None)


def leg_time(point, kind):
    for suffix in ("Estimated", "Planned", "BaseTimetable"):
        if value := point.get(kind + "Time" + suffix):
            return timestamp(value)
    raise ValueError("SL saknar tid för en delresa")


def compact_trip(journey):
    legs = journey.get("legs") or []
    if not legs or journey.get("isCancelled") or journey.get("cancelled"):
        return None
    out = []
    for leg in legs:
        if leg.get("isCancelled") or leg.get("cancelled") or "CANCELLED" in (leg.get("realtimeStatus") or []):
            return None
        origin, dest = leg["origin"], leg["destination"]
        depart, arrive = leg_time(origin, "departure"), leg_time(dest, "arrival")
        if arrive < depart:
            return None
        mode = leg.get("transportation") or {}
        product = mode.get("product") or {}
        out.append({"from": origin["name"], "to": dest["name"], "departure": iso(depart), "arrival": iso(arrive),
                    "mode": "gång" if product.get("class") in (99, 100) else product.get("name", "kollektivtrafik"),
                    "line": mode.get("disassembledName") or mode.get("number", ""),
                    "towards": (mode.get("destination") or {}).get("name", ""),
                    "notices": [str(i.get("content") or i.get("title") or "")[:500]
                                for i in (leg.get("infos") or []) + (leg.get("hints") or [])]})
    if any(timestamp(a["arrival"]) > timestamp(b["departure"]) for a, b in zip(out, out[1:])):
        return None
    return {"departure": out[0]["departure"], "arrival": out[-1]["arrival"],
            "changes": journey.get("interchanges"), "legs": out}


def trips(origin, destination, when, arrival):
    local = when.astimezone(TZ)
    data = request("trips", type_origin="any", name_origin=origin["id"], type_destination="any",
                   name_destination=destination["id"], calc_number_of_trips=3, language="sv",
                   itd_date=local.strftime("%Y%m%d"), itd_time=local.strftime("%H%M"),
                   itd_trip_date_time_dep_arr="arr" if arrival else "dep", calc_one_direction="true",
                   gen_c="false", compute_monomodal_trip_bicycle="false")
    out = []
    for j in data.get("journeys") or []:
        try:
            trip = compact_trip(j)
        except (KeyError, ValueError, TypeError):
            continue
        if trip is not None:
            out.append(trip)
    return out


def plan(destination, arrival_at=None, departure_at=None, origin="Stadshagen", return_at=None, home_by=None):
    """(is_error, JSON plan or short error). A failed return means the whole proposed outing is unverified."""
    try:
        if not isinstance(destination, str) or not destination.strip() or len(destination) > 200:
            raise ValueError("Ange destinationens namn eller adress (högst 200 tecken)")
        if not isinstance(origin, str) or not origin.strip() or len(origin) > 200:
            raise ValueError("Ange en startplats (högst 200 tecken)")
        origin, destination = origin.strip(), destination.strip()
        if origin.casefold() in ("hem", "hemma", "home"):
            origin = "Stadshagen"
        if bool(arrival_at) == bool(departure_at):
            raise ValueError("Ange exakt en av arrival_at (framme senast) och departure_at (avresa tidigast)")
        when = timestamp(arrival_at or departure_at)
        now = datetime.now(timezone.utc)
        if when <= now:
            raise ValueError("Resetiden har redan passerat; välj en framtida tid")
        back_at = timestamp(return_at) if return_at else None
        deadline = timestamp(home_by) if home_by else None
        if deadline and not back_at:
            raise ValueError("home_by kräver return_at för att kontrollera hemresan")
        if back_at and (back_at <= when or (deadline and deadline <= back_at)):
            raise ValueError("Hemresan måste börja efter utresan/besöksstarten och före home_by")
        walk = int(os.environ.get("JARVIS_WALK_MIN", "3")) if origin.casefold() == "stadshagen" else 0
        if not 0 <= walk <= 60:
            raise ValueError("JARVIS_WALK_MIN måste vara 0–60 minuter")
        # Resolve names concurrently, then use the same resolved places in both directions.
        with ThreadPoolExecutor(max_workers=2) as pool:
            o, d = pool.submit(location, origin), pool.submit(location, destination)
            o, d = o.result(), d.result()
        # departure_at is the time to leave home; include the walk and a two-minute boarding buffer.
        start_allowance = timedelta(minutes=walk + (2 if walk else 0))
        query_when = when if arrival_at else when + start_allowance
        with ThreadPoolExecutor(max_workers=2) as pool:
            outbound = pool.submit(trips, o, d, query_when, bool(arrival_at))
            inbound = pool.submit(trips, d, o, back_at, False) if back_at else None
            outward, backward = outbound.result(), inbound.result() if inbound else []
        outward = [t for t in outward if timestamp(t["departure"]) - start_allowance >= now
                   and (timestamp(t["arrival"]) <= when if arrival_at
                        else timestamp(t["departure"]) >= query_when)]
        if back_at:
            outward = [t for t in outward if timestamp(t["arrival"]) < back_at]
        if not outward:
            raise ValueError("SL gav ingen genomförbar utresa för den valda tiden. Boka inte utflykten på dessa tider")
        outward.sort(key=lambda t: timestamp(t["departure"] if arrival_at else t["arrival"]), reverse=bool(arrival_at))
        chosen = outward[0]
        leave_origin = timestamp(chosen["departure"]) - start_allowance
        result = {"source": "SL Journey Planner v2", "source_url": SOURCE, "checked_at": iso(now),
                  "timezone": "Europe/Stockholm", "origin": o["name"], "destination": d["name"],
                  "requested_arrival": iso(when) if arrival_at else None,
                  "requested_departure": iso(when) if departure_at else None,
                  "station_walk_minutes": walk, "boarding_buffer_minutes": 2 if walk else 0,
                  "leave_origin_at": iso(leave_origin), "outbound": chosen, "first_vehicle": first_vehicle(chosen),
                  "note": "Gångdelar från SL ingår. Framtida avgångar kan ändras; kontrollera igen före avfärd."}
        if "Arlanda central" in o["name"] + d["name"]:
            result["note"] += " Pendeltåg till och från Arlanda central kostar en passageavgift utöver SL-biljetten."
        if back_at:
            backward = [t for t in backward if timestamp(t["departure"]) >= back_at
                        and (deadline is None or timestamp(t["arrival"]) + timedelta(minutes=walk + 10) <= deadline)]
            if not backward:
                raise ValueError("SL gav ingen hemresa som passar besökstiden och home_by med 10 minuters marginal. "
                                 "Välj annan besökstid innan du bokar; gissa inte hemkomsttiden")
            back = min(backward, key=lambda t: timestamp(t["arrival"]))
            back_origin = timestamp(back["arrival"]) + timedelta(minutes=walk)
            # Reserve complete travel time, rounding outward to whole minutes, never five-minute guesses.
            calendar_end = back_origin.replace(second=0, microsecond=0)
            if calendar_end < back_origin:
                calendar_end += timedelta(minutes=1)
            result.update({"return": back, "back_at_origin": iso(timestamp(back["arrival"]) + timedelta(minutes=walk)),
                           "leave_venue_at": back["departure"], "first_return_vehicle": first_vehicle(back),
                           "visit": {"date": when.astimezone(TZ).date().isoformat(),
                                     "from": iso(when) if arrival_at else chosen["arrival"], "to": iso(back_at)},
                           "calendar_interval": {"from": iso(leave_origin.replace(second=0, microsecond=0)),
                                                 "to": iso(calendar_end)},
                           "home_by": iso(deadline) if deadline else None, "home_margin_minutes": 10 if deadline else 0})
        return False, to_minutes(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, TypeError, KeyError) as e:
        # Do not fall back to the next-departures board; it describes another time entirely.
        return True, f"Reseplaneringen kunde inte verifieras: {e}."
