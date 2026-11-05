#!/usr/bin/env python3
"""What Jarvis knows about the home right now, read only. Standard library only.

  Home Assistant  the states of a fixed list of entities (front door, temperature inside, air and the
                  purifier, smoke and water alarms, the sun, the next laundry slot), from GET /api/states
                  on 127.0.0.1:8123 through hass.py. Nothing else is called. On only when
                  JARVIS_HOME_TOKEN_FILE is set.
  SMHI            the hourly point forecast for Kungsholmen (snow1g version 1, no key): now, the next
                  12 hours' temperatures and when rain or snow starts. Kept 15 minutes.
  SL              metro departures from Stadshagen (site 9307, no key), both directions, and any notices
                  for the stop. Shared asynchronous cache, refreshed at most once a minute. The blue line's
                  deviation messages (deviations API, no key) on the same worker, at most every 5 minutes.
  Följdfel        Ellevio's, Stockholm Exergi's and Stockholm Vatten's disruptions whose area covers home,
                  from the collector's SQLite database on Jarvis (read-only, JARVIS_FOLJDFEL_DB). Kept a minute.
  The panel       which NASA picture of the day is its background (apod.json, written by server/apod.py).

A refused Home Assistant token (401 or 403) stops every call to Home Assistant from the server (hass.py):
every refused request counts as a failed login there, and five ban the address (ip_ban).

  python3 server/agents/home_status.py     print what Jarvis would see now (on Jarvis)
"""
import json
import os
import re
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from threading import Event, Lock, Thread
from zoneinfo import ZoneInfo

import background
import hass
import weather

TZ = ZoneInfo("Europe/Stockholm")
SMHI = ("https://opendata-download-metfcst.smhi.se/api/category/snow1g/version/1/geotype/point"
        "/lon/18.03/lat/59.33/data.json")
SL = "https://transport.integration.sl.se/v1/sites/9307/departures?transport=METRO&forecast=60"
# Countdowns are recomputed from SL's absolute times, so a 10-minute-old board stays usable (shown as
# "SL uppdateras"). SL rate-limited Jarvis (429) for ~9 minutes in a row on 2026-10-26.
SL_REFRESH_S, SL_MAX_AGE_S = 60, 600
# SL sometimes answers 200 with an empty list while trains run (6 of 16 probes on 2026-10-26).
# Such a reply never replaces a board that still has trains; the worker asks again sooner.
SL_RETRY_S = 20
# A good reply often takes several tries while SL is degraded; say "SL uppdateras" only after three minutes
# so the label does not flicker. Countdowns stay correct because they use SL's absolute times.
SL_STALE_S = 180
SL_MISSING = 'Saknar SL-kontakt'
# SL took 9.8 s for a good reply on 2026-10-26; the worker never blocks GET /panel.
SL_TIMEOUT_S = 15
# The blue line's own deviation messages (planned works, signal faults), for the metro card's disruption line
# (metro_note). Asked by the SL worker at most every SL_LINES_S; an answer older than SL_LINES_MAX_S is dropped.
SL_LINES = "https://deviations.integration.sl.se/v1/messages?transport_mode=METRO&line=10&line=11"
SL_LINES_S, SL_LINES_MAX_S = 5 * 60, 30 * 60
BLUE_LINES = ("10", "11")
SL_LIFT = re.compile(r"\b(?:hiss\w*|rulltrapp\w*)", re.I)
SL_FACILITY = re.compile(r"\b(?:hiss\w*|rulltrapp\w*|entré\w*|ingång\w*|uppgång\w*|toalett\w*|biljettautomat\w*)", re.I)
PURIFIER = "xiaomi_de_918752611_cpa4"
ENTITIES = {
    "door": "binary_sensor.0x7c31fafffef1b1f0_contact",
    "temperature": "sensor.smoke_alarm_temperature",
    "pm25": f"sensor.{PURIFIER}_pm2_5_density_p_3_4",
    "purifier": f"switch.{PURIFIER}_on_p_2_1",
    "mode": f"select.{PURIFIER}_mode_p_2_4",
    "filter": f"sensor.{PURIFIER}_filter_life_level_p_4_1",
    "smoke": "binary_sensor.smoke_alarm_smoke",
    "water": "binary_sensor.water_leak",
    "sun": "sun.sun",
    "laundry": "sensor.tvatt_bokning_nasta",
    "moon": "sensor.jarvis_manen",          # jarvis_agent/moon.py
}
MODES = {"Auto": "auto", "Sleep": "natt", "Manual": "manuellt"}
# SMHI's weather symbols (Wsymb2), 1 to 27
SYMBOLS = ["klart", "lätt molnighet", "halvklart", "molnigt", "mycket moln", "mulet", "dimma", "lätta regnskurar",
           "regnskurar", "kraftiga regnskurar", "åskskurar", "lätta byar av regn och snö", "byar av regn och snö",
           "kraftiga byar av regn och snö", "lätta snöbyar", "snöbyar", "kraftiga snöbyar", "lätt regn", "regn",
           "kraftigt regn", "åska", "lätt snöblandat regn", "snöblandat regn", "kraftigt snöblandat regn",
           "lätt snöfall", "snöfall", "kraftigt snöfall"]
UNKNOWN = ("unknown", "unavailable", None, "")
APOD_JSON = Path(os.environ.get("JARVIS_APOD_JSON", "~/docker/homeassistant/config/www/hallpanel/apod.json")).expanduser()


def num(value, decimals=1):
    """12.25 -> "12,3", 4.0 -> "4", 40 -> "40" (Swedish decimal comma)."""
    text = f"{float(value):.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text.replace(".", ",")


def clock(when):
    return when.astimezone(TZ).strftime("%H:%M")


def day_clock(when, now):
    """"07:42", or "i morgon 07:42" when it is tomorrow."""
    days = (when.astimezone(TZ).date() - now.astimezone(TZ).date()).days
    return {0: "", 1: "i morgon "}.get(days, when.astimezone(TZ).strftime("%-d/%-m ")) + clock(when)


def parse_time(text):
    when = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return when if when.tzinfo else when.replace(tzinfo=TZ)


def ha_lines(states, now):
    """Swedish lines from Home Assistant's states (the list from /api/states)."""
    by_id = {s["entity_id"]: s for s in states}

    def state(key):
        s = by_id.get(ENTITIES[key])
        return (s or {}).get("state")

    lines = []
    if (door := by_id.get(ENTITIES["door"])) and door["state"] not in UNKNOWN:
        since = parse_time(door["last_changed"])
        minutes = int((now - since).total_seconds() // 60)
        if door["state"] == "on":
            lines.append(f"Ytterdörren: öppen sedan {clock(since)} ({minutes} minuter).")
        else:
            lines.append(f"Ytterdörren: stängd sedan {clock(since)}.")
    if state("temperature") not in UNKNOWN:
        lines.append(f"Inne: {num(state('temperature'))} °C.")
    air = []
    if state("pm25") not in UNKNOWN:
        pm = float(state("pm25"))
        air.append(f"PM2,5 {num(pm, 0)} µg/m³ ({'bra' if pm <= 12 else 'okej' if pm <= 35 else 'dålig'} luft)")
    if state("purifier") in ("on", "off"):
        purifier = "på" if state("purifier") == "on" else "av"
        if state("purifier") == "on" and state("mode") not in UNKNOWN:
            purifier += f", läge {MODES.get(state('mode'), state('mode').lower())}"
        if state("filter") not in UNKNOWN:
            purifier += f", filtret {num(state('filter'), 0)} % kvar"
        air.append(f"luftrenaren {purifier}")
    if air:
        lines.append("Luften: " + ", ".join(air) + ".")
    alarms = []
    if state("smoke") in ("on", "off"):
        alarms.append("RÖK I LÄGENHETEN" if state("smoke") == "on" else "ingen rök")
    if state("water") in ("on", "off"):
        alarms.append("VATTENLÄCKA" if state("water") == "on" else "ingen vattenläcka")
    if alarms:
        lines.append("Larmen: " + ", ".join(alarms) + ".")
    if sun := by_id.get(ENTITIES["sun"]):
        a = sun.get("attributes") or {}
        if a.get("next_rising") and a.get("next_setting"):
            up, down = parse_time(a["next_rising"]), parse_time(a["next_setting"])
            events = sorted([(up, "går upp"), (down, "går ner")])
            lines.append("Solen: " + " och ".join(f"{verb} {day_clock(when, now)}" for when, verb in events) + ".")
    if state("laundry") not in UNKNOWN:
        slot = laundry_slot(state("laundry"), now)
        if slot and slot[1] <= now:
            lines.append(f"Senaste tvättbokningen: {state('laundry')} (har redan varit; ingen ny bokning än).")
        else:
            lines.append(f"Nästa tvättid: {state('laundry')}.")
    return lines


def forecast_hours(forecast, now):
    """[(start, data)] of SMHI's hours from now and 12 hours on."""
    hours = []
    for t in forecast.get("timeSeries") or []:
        start = parse_time(t.get("intervalParametersStartTime") or t["time"])
        if parse_time(t["time"]) > now and start < now + timedelta(hours=12):
            hours.append((start, t.get("data") or {}))
    return hours


def sky_word(data):
    symbol = data.get("symbol_code")
    return SYMBOLS[symbol - 1] if isinstance(symbol, int) and 1 <= symbol <= len(SYMBOLS) else None


def first_wet(hours):
    """(start, "regn" or "snö", data) of the first hour with rain or snow, or None."""
    for start, d in hours:
        if (d.get("precipitation_amount_mean") or 0) >= 0.1:
            return start, "snö" if (d.get("probability_of_frozen_precipitation") or 0) > 50 else "regn", d
    return None


def weather_lines(forecast, now):
    """Swedish lines from SMHI's point forecast: now, the next 12 hours, when rain or snow starts."""
    hours = forecast_hours(forecast, now)
    if not hours:
        return []
    _, first = hours[0]
    sky = sky_word(first)
    lines = [f"Ute nu: {num(first['air_temperature'])} °C" + (f", {sky}" if sky else "")
             + f", vind {num(first.get('wind_speed', 0), 0)} m/s."]
    temps = [d["air_temperature"] for _, d in hours if "air_temperature" in d]
    low, high = num(min(temps), 0), num(max(temps), 0)
    lines.append(f"De närmaste 12 timmarna: {'kring ' + low if low == high else 'mellan ' + low + ' och ' + high} °C.")
    if wet := first_wet(hours):
        start, kind, d = wet
        when = "nu" if start <= now else f"från {clock(start)}"
        lines.append(f"Nederbörd: {kind} {when}, cirka {num(d['precipitation_amount_mean'])} mm i timmen.")
    else:
        lines.append("Nederbörd: inget regn eller snö de närmaste 12 timmarna.")
    return lines


def cancelled(d):
    """True for a departure that will not run. SL marks some only by a deviation (Joel, 2026-11-03: state EXPECTED
    with {"consequence": "CANCELLED", "message": "Inställd"} was shown as a train to leave for)."""
    return d.get("state") in ("CANCELLED", "INHIBITED") or any(
        isinstance(x, dict) and x.get("consequence") == "CANCELLED" for x in d.get("deviations") or [])


def metro_departures(departures, now):
    """Valid future departures in expected-time order, across both directions."""
    trains = []
    for d in departures.get("departures") or []:
        if not isinstance(d, dict) or d.get("direction_code") not in (1, 2) or cancelled(d):
            continue
        try:
            when = parse_time(d.get("expected") or d["scheduled"])
        except (ValueError, TypeError, KeyError, AttributeError):
            continue
        if when >= now:
            trains.append((when, d))
    return sorted(trains, key=lambda train: train[0])


def metro_lines(departures, now):
    """The next five departures from Stadshagen in chronological order, then notices."""
    lines = []
    for when, d in metro_departures(departures, now)[:5]:
        minutes = int((when - now).total_seconds() // 60)
        line = (d.get("line") or {}).get("designation", "")
        due = "nu" if not minutes else f"om {minutes} min"
        lines.append(f"Tunnelbanan från Stadshagen mot {d.get('destination', '')}: {clock(when)} (linje {line}, {due}).")
    for notice in departures.get("stop_deviations") or []:
        if notice.get("message"):
            lines.append(f"SL meddelar: {notice['message'].strip()}")
    return lines or ["Tunnelbanan från Stadshagen: inga avgångar den närmaste timmen."]


def outside_short(forecast, now):
    """"6°, regn från 16:00" or "6°, halvklart", for the panel."""
    hours = forecast_hours(forecast, now)
    if not hours or "air_temperature" not in hours[0][1]:
        return ""
    temp = num(hours[0][1]["air_temperature"], 0) + "°"
    if wet := first_wet(hours):
        start, kind, _ = wet
        return f"{temp}, {kind} {'nu' if start <= now else 'från ' + clock(start)}"
    return f"{temp}, {sky}" if (sky := sky_word(hours[0][1])) else temp


def weather_panel(forecast, now, sun_up=True):
    """The dashboard's weather card: w_temp "6°", w_line "Regn från 16:00" or "Halvklart", w_tip "Ta paraply"
    or "", and w_icon, one of sun, moon, partly, cloud, rain and snow (rain or snow when it starts within 3 hours)."""
    hours = forecast_hours(forecast, now)
    out = {"w_temp": "", "w_line": "", "w_tip": "", "w_icon": ""}
    if not hours:
        return out
    first = hours[0][1]
    if "air_temperature" in first:
        out["w_temp"] = num(first["air_temperature"], 0) + "°"
    symbol = first.get("symbol_code")
    if wet := first_wet(hours):
        start, kind, _ = wet
        out["w_line"] = f"{kind.capitalize()} {'nu' if start <= now else 'från ' + clock(start)}"
        out["w_tip"] = "Ta paraply" if kind == "regn" else "Ta varma kläder"
    else:
        out["w_line"] = (sky_word(first) or "").capitalize()
    if wet and wet[0] <= now + timedelta(hours=3):
        out["w_icon"] = "snow" if wet[1] == "snö" else "rain"
    elif symbol in (1, 2):
        out["w_icon"] = "sun" if sun_up else "moon"
    elif symbol in (3, 4):
        out["w_icon"] = "partly" if sun_up else "cloud"
    else:
        out["w_icon"] = "cloud"
    return out


def metro_panel(departures, now, walk=3):
    """The dashboard's next four trains, earliest first across both directions, as
    "line|destination|note|minutes|state", with walk minutes to the platform. state is "go" (leave now), "missed"
    (only just too late; anything later is left out) or ""; an empty row has no train."""
    rows = []
    for when, d in metro_departures(departures, now):
        minutes = int((when - now).total_seconds() // 60)
        leave = minutes - walk
        if leave < -1:
            continue
        state = "missed" if leave < 0 else "go" if leave <= 1 else ""
        note = {"missed": "hinner inte", "go": "gå nu"}.get(state, f"gå om {leave} min")
        rows.append("|".join(((d.get("line") or {}).get("designation", ""), d.get("destination", ""), note,
                              str(minutes), state)))
        if len(rows) == 4:
            break
    return {f"sl{i}": rows[i] if i < len(rows) else "" for i in range(4)}


def metro_note(departures, line_messages=(), now=None):
    """The metro card's disruption line (Joel, 2026-11-02): SL's notices for Stadshagen, the shown trains' own
    deviations and the blue line's messages (SL_LINES), the first one and "(+N)" for the rest; "" when none.
    Lifts, escalators and other stations' entrances are left out: they say nothing about the trains he takes.
    A train's deviation names the train ("Inställd: 10 mot Kungsträdgården 10:04", "10 mot Hjulsta 09:56: Försenad"):
    SL's bare "Inställd" said nothing on its own (Joel, 2026-11-03). Cancelled trains that have already left (before now) are dropped."""
    notes = []
    for notice in departures.get("stop_deviations") or []:
        text = re.sub(r"^Stadshagen:\s*", "", str(notice.get("message") or "").strip())
        if text and not SL_LIFT.search(text):
            notes.append(text)
    for d in departures.get("departures") or []:
        if not isinstance(d, dict) or str((d.get("line") or {}).get("designation", "")) not in BLUE_LINES:
            continue
        devs = [x for x in d.get("deviations") or [] if isinstance(x, dict) and x.get("message")]
        if not devs:
            continue
        line, dest = (d.get("line") or {}).get("designation", ""), d.get("destination")
        train = " ".join(str(part) for part in (line, dest and f"mot {dest}") if part)
        try:
            when = parse_time(d.get("scheduled") or d["expected"])
        except (ValueError, TypeError, KeyError, AttributeError):
            when = None
        if when is not None:
            if now is not None and when < now and cancelled(d):
                continue
            train += f" {clock(when)}"
        for x in devs:
            text = str(x["message"]).strip()
            if x.get("consequence") == "CANCELLED" or text.casefold() == "inställd":
                notes.append(f"Inställd: {train}")
            else:
                notes.append(f"{train}: {text}")
    for message in line_messages:
        if not SL_FACILITY.search(message):
            notes.append(message)
    notes = [n.split("\n")[0].strip() for n in dict.fromkeys(notes) if n.strip()]
    if not notes:
        return ""
    return notes[0] + (f" (+{len(notes) - 1})" if len(notes) > 1 else "")


def line_headers(messages):
    """The Swedish headers of SL's deviation messages (SL_LINES' answer)."""
    out = []
    for m in messages if isinstance(messages, list) else []:
        variants = m.get("message_variants") or []
        sv = next((v for v in variants if v.get("language") == "sv"), variants[0] if variants else {})
        if header := str(sv.get("header") or "").strip():
            out.append(header)
    return out


def calendar_panel(events, now, colors=None, later=None):
    """The dashboard's calendar card: cal_head "IDAG" or "I MORGON", and cal0..cal2 as "title|HH:MM|calendar|#RRGGBB"
    for the next events today (started at most 30 minutes ago), or tomorrow's when today has none left. A day with
    one or two events left fills the card with tomorrow's, their time "i morgon 09:30" (2026-11-02).
    events are (start, calendar, title, id) from the calendar MCP's agenda (server/watch.py reads it).
    Omit the fourth field only for legacy callers without a palette.
    later: the calendar page's seven days ((start, end or None, calendar, title), calendar_week.parse), or None
    before its first read. When today and tomorrow are empty the card shows the week's next events under
    "KOMMANDE", their time "tors 18:00" or "tors heldag", instead of an empty card (Joel, 2026-11-02)."""
    local = now.astimezone(TZ)
    today = [e for e in events if e[0].date() == local.date() and e[0] >= now - timedelta(minutes=30)]
    tomorrow = [e for e in events if e[0].astimezone(TZ).date() == local.date() + timedelta(days=1)]
    if not today and not tomorrow and later is not None:
        return calendar_later(later, now, colors)
    head = "IDAG"
    if not today and tomorrow:
        head, today, tomorrow = "I MORGON", tomorrow, []
    shown = [(e, "") for e in today] + [(e, "i morgon ") for e in tomorrow]
    out = {"cal_head": head}
    for i in range(3):
        out[f"cal{i}"] = ""
        if i < len(shown):
            (start, calendar, title, _), day = shown[i]
            fields = [title.replace('|', ' '), f'{day}{start:%H:%M}', calendar.replace('|', ' ')]
            if colors is not None:
                fields.append(colors.get(calendar, ''))
            out[f"cal{i}"] = '|'.join(fields)
    if not today:
        out["cal0"] = "Inget mer inlagt||"
    return out


def calendar_later(week, now, colors=None):
    """The calendar card's "KOMMANDE": the next three events from tomorrow on in the seven days that
    calendar_week reads, all-day ones included ("tors heldag"); "Inget inlagt på sju dagar" when there is none."""
    local = now.astimezone(TZ)
    first = local.date() + timedelta(days=1)
    ahead = sorted((e for e in week if e[0].astimezone(TZ).date() >= first),
                   key=lambda e: (e[0], e[1] is not None))
    out = {"cal_head": "KOMMANDE"}
    for i in range(3):
        out[f"cal{i}"] = ""
        if i < len(ahead):
            start, end, calendar, title = ahead[i]
            when = start.astimezone(TZ)
            day = WEEKDAYS_SHORT[when.weekday()]
            fields = [title.replace('|', ' '), f"{day} {clock(when)}" if end is not None else f"{day} heldag",
                      calendar.replace('|', ' ')]
            if colors is not None:
                fields.append(colors.get(calendar, ''))
            out[f"cal{i}"] = '|'.join(fields)
    if not ahead:
        out["cal0"] = "Inget inlagt på sju dagar||"
    return out


LAUNDRY_MONTHS = {m: i + 1 for i, m in enumerate(["jan", "feb", "mar", "apr", "maj", "jun", "jul", "aug", "sep",
                                                   "okt", "nov", "dec"])}


def laundry_slot(text, now):
    """(start, end) of "Torsdag 22 Okt 06:00–09:00: Gul + Röd" in Stockholm time, or None."""
    m = re.search(r"(\d{1,2}) (\w{3})\w* (\d\d):(\d\d)(?:\s*[–-]\s*(\d\d):(\d\d))?", text or "")
    if not m or m.group(2).lower() not in LAUNDRY_MONTHS:
        return None
    day, month = int(m.group(1)), LAUNDRY_MONTHS[m.group(2).lower()]
    year = now.year + (1 if month < now.month - 6 else 0)       # a slot in January seen in December
    start = datetime(year, month, day, int(m.group(3)), int(m.group(4)), tzinfo=TZ)
    end = start.replace(hour=int(m.group(5)), minute=int(m.group(6))) if m.group(5) else start + timedelta(hours=3)
    return start, end


WEEKDAYS = {"måndag": "mån", "tisdag": "tis", "onsdag": "ons", "torsdag": "tors", "fredag": "fre", "lördag": "lör",
            "söndag": "sön"}


MONTHS = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]


def moon_line(moon, now):
    """Hemmet's moon line (Joel, 2026-10-28): "Avtagande måne, 88 % · fullmåne 24 nov". At full moon the next new
    moon instead; "i kväll 21:14" or "i morgon 05:30" when it is that close."""
    a = moon.get("attributes") or {}
    line = moon["state"] + (f", {a['illumination']} %" if a.get("illumination") not in (None, "") else "")
    key, word = ("next_new", "nymåne") if moon["state"] == "Fullmåne" else ("next_full", "fullmåne")
    if not a.get(key):
        return line
    when = parse_time(a[key]).astimezone(TZ)
    days = (when.date() - now.astimezone(TZ).date()).days
    at = {0: f"i dag {clock(when)}", 1: f"i morgon {clock(when)}"}.get(days, f"{when.day} {MONTHS[when.month - 1]}")
    return f"{line} · {word} {at}"


def panel_fields(states, now):
    """The home for the panel's Hemmet page and its status line: short strings, "" when unknown.
    alert, smoke_on and water_on are "on" or "off"."""
    by_id = {s["entity_id"]: s for s in states}

    def state(key):
        return (by_id.get(ENTITIES[key]) or {}).get("state")

    f = {k: "" for k in ("door", "door_since", "inside", "pm25", "air", "purifier", "smoke", "water", "laundry",
                         "laundry_rooms", "sun", "moon", "summary")}
    f.update(alert="off", smoke_on="off", water_on="off")
    status, door_open_min = None, None
    if state("smoke") in ("on", "off"):
        f["smoke"], f["smoke_on"] = ("Rök!", "on") if state("smoke") == "on" else ("Ingen rök", "off")
    if state("water") in ("on", "off"):
        f["water"], f["water_on"] = ("Läcka!", "on") if state("water") == "on" else ("Ingen läcka", "off")
    if (door := by_id.get(ENTITIES["door"])) and door["state"] not in UNKNOWN:
        since = parse_time(door["last_changed"])
        f["door"] = "Öppen" if door["state"] == "on" else "Stängd"
        # The Larm card has 184 px for "Ytterdörren · sedan …" (gen.py dot_row): "28/10 20:43" did not fit and ran
        # into the card's right edge (Joel, 2026-10-29), so only today's change has its time.
        days = (now.astimezone(TZ).date() - since.astimezone(TZ).date()).days
        f["door_since"] = "sedan " + {0: clock(since), 1: "i går"}.get(days, since.astimezone(TZ).strftime("%-d/%-m"))
        if door["state"] == "on":
            door_open_min = int((now - since).total_seconds() // 60)
    if state("temperature") not in UNKNOWN:
        f["inside"] = num(state("temperature")) + "°"
    pm = None
    if state("pm25") not in UNKNOWN:
        pm = float(state("pm25"))
        f["pm25"] = f"{num(pm, 0)} µg/m³"
        f["air"] = "Bra luft" if pm <= 12 else "Okej luft" if pm <= 35 else "Dålig luft"
    if state("purifier") == "off":
        f["purifier"] = "Av"
    elif state("purifier") == "on":
        mode = {"Auto": "Auto", "Sleep": "Natt", "Manual": "Manuellt"}.get(state("mode"), "På")
        f["purifier"] = mode + (f" · filter {num(state('filter'), 0)} %" if state("filter") not in UNKNOWN else "")
    laundry = state("laundry")
    if laundry not in UNKNOWN and (slot := laundry_slot(laundry, now)) and slot[1] <= now:
        laundry = None                          # the booker has not found the next one yet; this one is over
    if laundry not in UNKNOWN:
        m = re.search(r"(\w+) (\d{1,2}) (\w+) (\d\d:\d\d\s*[–-]\s*\d\d:\d\d)", laundry)
        f["laundry"] = (f"{WEEKDAYS.get(m.group(1).lower(), m.group(1).lower())} {m.group(2)} {m.group(3).lower()} "
                        f"{m.group(4)}") if m else laundry
        if rooms := re.search(r"\d\d:\d\d\s*[–-]\s*\d\d:\d\d:?\s*(.+)$", laundry):
            f["laundry_rooms"] = rooms.group(1).strip()
    if sun := by_id.get(ENTITIES["sun"]):
        a = sun.get("attributes") or {}
        if a.get("next_rising") and a.get("next_setting"):
            when, verb = min((parse_time(a["next_rising"]), "upp"), (parse_time(a["next_setting"]), "ner"))
            f["sun"] = f"Solen går {verb} {day_clock(when, now)}"
    if (moon := by_id.get(ENTITIES["moon"])) and moon["state"] not in UNKNOWN:
        f["moon"] = moon_line(moon, now)
    if f["smoke_on"] == "on":
        status = "Brandvarnaren larmar!"
    elif f["water_on"] == "on":
        status = "Vattenlarmet har gått!"
    elif door_open_min is not None and door_open_min >= 10:
        status = f"Ytterdörren har stått öppen i {door_open_min} minuter."
    elif pm is not None and pm > 35:
        status = f"Dålig luft, PM2,5 är {num(pm, 0)}."
    if not status:
        monitored = [key for key in ("door", "pm25", "smoke", "water") if ENTITIES[key] in by_id]
        if not monitored:
            status = "Hemstatus saknas."
        elif any(state(key) in UNKNOWN for key in monitored):
            status = "Delar av hemstatus saknas."
    f["alert"] = "on" if status else "off"
    if not status:
        status = "Ytterdörren är öppen." if door_open_min is not None else "Allt lugnt hemma."
    f["status"] = status
    # One quiet line under the status: "Dörren stängd · 22,4° inne · bra luft"
    parts = [f"dörren {f['door'].lower()}" if f["door"] else "", f"{f['inside']} inne" if f["inside"] else "",
             f["air"].lower()]
    f["summary"] = " · ".join(p for p in parts if p)
    f["summary"] = f["summary"][:1].upper() + f["summary"][1:]
    return f


# Hemmet's house line (Joel, 2026-11-02): an outage or planned work from his own providers whose area covers home,
# from Följdfel's collector on Jarvis (its observation table, read-only). Only his providers and only an area over
# zone.home: Ellevio alone lists ~25 outages a day around Stockholm, and over Följdfel's first three weeks this rule
# would have shown 2 Ellevio outages where a 1 km radius around each outage's centre also caught other streets'.
# The BRF's news were there for an hour and went as bloat.
FOLJDFEL_DB = Path(os.environ.get("JARVIS_FOLJDFEL_DB", "~/foljdfel/data/foljdfel.sqlite")).expanduser()
HOUSE_AHEAD, HOUSE_SOON = timedelta(days=7), timedelta(hours=24)
HOUSE_CACHE_S = 60
# (Följdfel source, operator or None for any): the name on the line and its sector's words
PROVIDERS = {("stromkoll", "Ellevio AB"): ("Ellevio", "strömavbrott", "planerat elarbete"),
             ("exergi", None): ("Exergi", "fjärrvärmeavbrott", "planerat fjärrvärmearbete"),
             ("svoa", None): ("Stockholm Vatten", "vattenavbrott", "planerat vattenarbete")}


def local_time(text):
    """A Följdfel time: "2026-11-04T08:00:00.000Z" (UTC) or "2026-11-04 13:00" / "2026-11-04T13:00:00" (local)."""
    if not text:
        return None
    try:
        when = datetime.fromisoformat(str(text).replace("Z", "+00:00").replace(" ", "T"))
    except ValueError:
        return None
    return when.astimezone(TZ) if when.tzinfo else when.replace(tzinfo=TZ)


def in_ring(lng, lat, ring):
    """Ray casting: whether (lng, lat) lies inside a closed ring of [lng, lat] points."""
    inside = False
    for (x1, y1, *_), (x2, y2, *_) in zip(ring, ring[-1:] + ring[:-1]):
        if (y1 > lat) != (y2 > lat) and lng < (x2 - x1) * (lat - y1) / (y2 - y1) + x1:
            inside = not inside
    return inside


def covers(geometry, home):
    """Whether Följdfel's MultiPolygon (JSON, [lng, lat] rings, holes after the outer ring) covers home (lat, lng)."""
    try:
        polygons = json.loads(geometry) if geometry else []
        return any(in_ring(home[1], home[0], rings[0]) and not any(in_ring(home[1], home[0], h) for h in rings[1:])
                   for rings in polygons if rings)
    except (ValueError, TypeError, IndexError, ZeroDivisionError):
        return False


def own_disruptions(rows, home, now):
    """[(soon, text)] for his providers' disruptions still listed whose area covers home and that run now or start
    within HOUSE_AHEAD, the earliest first: "Ellevio: strömavbrott pågår, till ca 23:00". rows: (source, operator,
    kind, geometry, start_utc, claimed_end_estimate). soon: running, or starting within HOUSE_SOON (for Härnäst)."""
    found = []
    for source, operator, kind, geometry, start, end in rows:
        names = PROVIDERS.get((source, operator)) or PROVIDERS.get((source, None))
        if not names or not covers(geometry, home):
            continue
        start, end = local_time(start), local_time(end)
        if end and end < now:
            end = None                          # an estimate already passed says nothing
        if start and start - now > HOUSE_AHEAD:
            continue
        name, outage, work = names
        if not start or start <= now:
            when = "pågår" + (f", till ca {clock(end)}" if end else "")
        else:
            days = (start.date() - now.astimezone(TZ).date()).days
            day = {0: "idag", 1: "i morgon"}.get(days, WEEKDAYS_SHORT[start.weekday()])
            when = f"{day} {clock(start)}" + (f"–{clock(end)}" if end and end.date() == start.date() else "")
        found.append((not start or start - now <= HOUSE_SOON, start or now,
                      f"{name}: {work if kind == 'planned' else outage} {when}"))
    found.sort(key=lambda f: f[1])
    return [(soon, text) for soon, _, text in found]


def house_line(disruptions):
    """Hemmet's house line as "störning|…" (with "(+N)" for more), or "" (the panel hides the line)."""
    if not disruptions:
        return ""
    more = f" (+{len(disruptions) - 1})" if len(disruptions) > 1 else ""
    return f"störning|{disruptions[0][1]}{more}"


WAKEUP = "sensor.jarvis_nasta_vackning"   # its panel attribute: "Väckning 08:45" (jarvis_agent/wakeup.py panel_line)
# Statuses that say a source is missing, not something to do: the Jarvis card's Härnäst leaves them to Hemmet.
MISSING = ("Hemstatus saknas.", "Delar av hemstatus saknas.", "Hemstatus kunde inte läsas.")
NEXT_LINES, REMINDER_AHEAD, LAUNDRY_AHEAD = 5, timedelta(hours=12), timedelta(hours=36)


def next_up(now, alert="", reminders=(), laundry="", wake="", house=()):
    """The Jarvis card's Härnäst while its chat is empty (2026-11-02): at most NEXT_LINES lines, "\\n" between
    them. A problem at home first (Hemmet's alert status), then his power, heating or water provider's disruption over home
    that runs now or starts within 24 hours (house: own_disruptions), reminders due within 12 hours ([(due, line)] as
    reminders.lines shows them), the laundry slot when it starts within 36 hours or runs now, and from 18:00
    the next wakeup (wake: "Väckning 08:45"). The dashboard's own cards (calendar, weather, metro) are not
    repeated. "" when nothing is coming."""
    local = now.astimezone(TZ)
    lines = [alert] if alert and alert not in MISSING else []
    lines += [text for soon, text in house if soon][:1]
    lines += [line for due, line in reminders if due - now <= REMINDER_AHEAD][:2]
    if laundry not in UNKNOWN and (slot := laundry_slot(laundry, now)):
        start, end = slot
        if start <= now < end:
            lines.append(f"Tvättid nu, till {clock(end)}")
        elif now < start and start - now <= LAUNDRY_AHEAD:
            day = {0: "idag", 1: "i morgon"}.get((start.date() - local.date()).days, WEEKDAYS_SHORT[start.weekday()])
            lines.append(f"Tvättid {day} {clock(start)}–{clock(end)}")
    if wake.startswith(("Väckning", "Snooze")) and (local.hour >= 18 or wake.startswith("Snooze")):
        lines.append(wake)
    return "\n".join(lines[:NEXT_LINES])


WEEKDAYS_SHORT = ("mån", "tis", "ons", "tors", "fre", "lör", "sön")


def get_json(url, headers=None, timeout=3):
    req = urllib.request.Request(url, headers={"Accept": "application/json", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


class Home:
    _shared, _shared_lock = None, Lock()

    def __init__(self):
        self.cache = {}                         # url -> (time.monotonic(), json)
        self._sl_lock, self._sl_busy, self._sl_next = Lock(), False, 0
        self._sl_error = False
        self._sl_first_try = self._sl_empty_since = None   # monotonic times
        self._sl_done = Event()                 # set whenever no SL request is in flight
        self._sl_done.set()
        self._sl_lines, self._sl_lines_at, self._sl_lines_asked = [], None, 0.0   # metro_note's line messages
        self._sl_note = ""                      # the last disruption line shown, to log its changes
        self._house_cache = (0.0, None)         # (monotonic time, Följdfel's open rows), see disruptions()
        self._house_shown = None                # the disruptions last found, to log their changes
        # What SL answered and what the panel showed, for GET /sl/status and server/check_sl.py.
        self._sl_stats = {'started': time.monotonic(), 'ok': 0, 'empty_kept': 0, 'empty_accepted': 0,
                          'timeout': 0, 'other': 0, 'http': {}, 'last_ok': None, 'last_ok_trains': False,
                          'shown': None, 'shown_since': None, 'last_panel_read': None, 'longest_missing_s': 0.0}

    @classmethod
    def from_env(cls):
        """A Home if JARVIS_HOME_TOKEN_FILE is set, else None (no home status, no tool)."""
        if not os.environ.get('JARVIS_HOME_TOKEN_FILE'):
            return None
        with cls._shared_lock:
            if cls._shared is None:
                cls._shared = cls()
            return cls._shared

    def _cached(self, url, keep_s):
        if url == SL:
            return self._sl_snapshot()[0]
        hit = self.cache.get(url)
        if hit and time.monotonic() - hit[0] < keep_s:
            return hit[1]
        data = get_json(url)
        self.cache[url] = (time.monotonic(), data)
        return data

    def _refresh_sl(self, now=None):
        """One bounded worker for panel and voice; never wait for SL in GET /panel."""
        delay, answered = SL_REFRESH_S, False
        try:
            data = get_json(SL, timeout=SL_TIMEOUT_S)
            if not isinstance(data, dict) or not isinstance(data.get('departures'), list):
                raise ValueError('Invalid SL departures response')
            answered = True
            with self._sl_lock:
                old, at = self.cache.get(SL), time.monotonic()
                suspect = False
                if data['departures']:
                    self._sl_empty_since = None
                else:
                    # An empty board is true only when the trains we know of are gone (night), or when SL has
                    # said so for as long as a board may bridge. Until then keep what we have and ask again.
                    if self._sl_empty_since is None:
                        self._sl_empty_since = at
                    board_done = old is not None and not metro_departures(old[1], now or datetime.now(TZ))
                    suspect = not board_done and at - self._sl_empty_since + SL_RETRY_S < SL_MAX_AGE_S
                stats = self._sl_stats
                if suspect:
                    delay = SL_RETRY_S              # status follows the board's age, so no flicker
                    stats['empty_kept'] += 1
                else:
                    self.cache[SL] = (time.monotonic(), data)
                    self._sl_error = False
                    stats['ok' if data['departures'] else 'empty_accepted'] += 1
                    stats['last_ok'], stats['last_ok_trains'] = time.monotonic(), bool(data['departures'])
            if suspect:
                print('home status: SL returned no departures; not believed yet, asking again soon', file=sys.stderr)
        except Exception as error:
            with self._sl_lock:
                usable = SL in self.cache
            if not usable:
                delay = SL_RETRY_S                  # nothing to show yet: try again soon, unless SL says wait
            if isinstance(error, urllib.error.HTTPError) and error.code in (429, 503):
                delay = max(delay, SL_REFRESH_S)
                value = (error.headers or {}).get('Retry-After', '')
                try:
                    retry = int(value) if str(value).isdigit() else parsedate_to_datetime(value).timestamp() - time.time()
                    delay = min(86400, max(delay, retry))
                except (ValueError, TypeError, OverflowError):
                    pass
            timeout = isinstance(error, TimeoutError) or 'timed out' in str(getattr(error, 'reason', error))
            with self._sl_lock:
                self._sl_error = True
                stats = self._sl_stats
                if isinstance(error, urllib.error.HTTPError):
                    stats['http'][str(error.code)] = stats['http'].get(str(error.code), 0) + 1
                else:
                    stats['timeout' if timeout else 'other'] += 1
            detail = f'HTTP {error.code}' if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            print(f'home status: SL failed: {detail}', file=sys.stderr)
        finally:
            if answered:                        # never ask SL more while it fails or rate-limits
                self._refresh_sl_lines()
            with self._sl_lock:
                self._sl_next = time.monotonic() + delay
                self._sl_busy = False
            self._sl_done.set()

    def _refresh_sl_lines(self):
        """The blue line's deviation messages, at most every SL_LINES_S, on the SL worker; a failure keeps the
        last answer until it is SL_LINES_MAX_S old and is logged."""
        at = time.monotonic()
        if at - self._sl_lines_asked < SL_LINES_S and self._sl_lines_asked:
            return
        self._sl_lines_asked = at
        try:
            headers = line_headers(get_json(SL_LINES, timeout=SL_TIMEOUT_S))
            with self._sl_lock:
                if headers != self._sl_lines:
                    print(f'home status: SL blue line messages: {len(headers)}', file=sys.stderr)
                self._sl_lines, self._sl_lines_at = headers, time.monotonic()
        except Exception as error:
            detail = f'HTTP {error.code}' if isinstance(error, urllib.error.HTTPError) else type(error).__name__
            print(f'home status: SL deviations failed: {detail}', file=sys.stderr)

    def sl_lines(self):
        """The blue line's messages, [] when the last good answer is older than SL_LINES_MAX_S."""
        with self._sl_lock:
            fresh = self._sl_lines_at is not None and time.monotonic() - self._sl_lines_at <= SL_LINES_MAX_S
            return list(self._sl_lines) if fresh else []

    def _sl_snapshot(self, now=None):
        start = False
        with self._sl_lock:
            if not self._sl_busy and time.monotonic() >= self._sl_next:
                self._sl_busy = start = True
                self._sl_done.clear()
                if self._sl_first_try is None:
                    self._sl_first_try = time.monotonic()
        if start:
            try:
                Thread(target=lambda: self._refresh_sl(now), name='sl-departures', daemon=True).start()
            except Exception:
                with self._sl_lock:
                    self._sl_busy = False
                    self._sl_next = time.monotonic() + SL_REFRESH_S
                self._sl_done.set()
                raise
        with self._sl_lock:
            hit, error = self.cache.get(SL), self._sl_error
        age = time.monotonic() - hit[0] if hit else float('inf')
        if not 0 <= age <= SL_MAX_AGE_S:
            raise OSError('No recent SL departures')
        return hit[1], age >= SL_STALE_S

    def disruptions(self, states, now):
        """own_disruptions over zone.home from Följdfel's database, read at most every HOUSE_CACHE_S;
        [] without the database or zone.home. A failed read is logged and shows nothing."""
        zone = next((s for s in states or [] if s.get("entity_id") == "zone.home"), None)
        a = (zone or {}).get("attributes") or {}
        if a.get("latitude") is None or a.get("longitude") is None:
            return []
        at, rows = self._house_cache
        if rows is None or time.monotonic() - at >= HOUSE_CACHE_S:
            rows = []
            if FOLJDFEL_DB.exists():
                try:
                    with closing(sqlite3.connect(f"file:{FOLJDFEL_DB}?mode=ro", uri=True, timeout=1)) as db:
                        rows = db.execute(
                            "SELECT source, operator, kind, geometry, start_utc, claimed_end_estimate "
                            "FROM observation WHERE gone_at IS NULL AND source IN (%s)"
                            % ",".join("?" * len({k[0] for k in PROVIDERS})),
                            sorted({k[0] for k in PROVIDERS})).fetchall()
                except sqlite3.Error as e:
                    print(f"home status: Följdfel database failed: {e}", file=sys.stderr)
            self._house_cache = (time.monotonic(), rows)
        found = own_disruptions(rows, (float(a["latitude"]), float(a["longitude"])), now)
        if [t for _, t in found] != self._house_shown:
            self._house_shown = [t for _, t in found]
            print(f"home status: provider disruptions over home: {len(found)}", file=sys.stderr)
        return found

    def states(self):
        """Home Assistant's states (GET /api/states). Raises hass.Refused without a valid token."""
        return hass.get_json("/api/states", timeout=2)

    def _ha(self, now):
        if hass.refused():
            return ["Home Assistant: kan inte läsas (ingen giltig token)."]
        return ha_lines(self.states(), now)

    def weather(self, now, sun_up=True, sun=None):
        """weather_panel and the card's days and weather page (weather.panel) from SMHI (kept 15 minutes); empty
        strings when SMHI cannot be read. sun: sun.sun's attributes, for night icons on the page."""
        try:
            raw = self._cached(SMHI, 15 * 60)
            return {**weather_panel(raw, now, sun_up), **weather.panel(raw, now, SYMBOLS, sun)}
        except Exception as e:
            print(f"home status: SMHI failed: {e}", file=sys.stderr)
            return {**weather_panel({}, now), **weather.panel({}, now, SYMBOLS)}

    def forecast(self, **args):
        """The full available SMHI horizon, sharing the panel/warm-up's 15-minute cache."""
        try:
            return weather.summarize(self._cached(SMHI, 15 * 60), datetime.now(TZ), SYMBOLS, **args)
        except (OSError, ValueError, TypeError) as e:
            return True, f"SMHI-prognosen kunde inte hämtas: {type(e).__name__}. Använd inte dagens väder för en annan dag."

    def prompt_forecast(self, text, now=None):
        """Detailed local weather already in memory; never fetch or delay an unrelated question.

        The model still phrases the answer and handles compound requests. Dates outside today/tomorrow
        use the ordinary tool. Stale, absent or invalid forecasts are never substituted with today's weather.
        """
        query = text.casefold()
        if not re.search(r"\b(?:väder\w*|regn\w*|snö\w*|paraply\w*|weather|rain\w*|snow\w*|umbrella)\b", query):
            return None
        if re.search(r"\d|måndag|tisdag|onsdag|torsdag|fredag|lördag|söndag|helg|vecka|monday|tuesday|wednesday|thursday|friday|saturday|sunday|week", query):
            return None
        now = now or datetime.now(TZ)
        hit = self.cache.get(SMHI)
        if not hit or not 0 <= (age := time.monotonic() - hit[0]) < 15 * 60:
            return None
        tomorrow = bool(re.search(r"\b(?:imorgon|i morgon|tomorrow)\b", query))
        today = bool(re.search(r"\b(?:idag|i dag|today)\b", query))
        first = now.date() + timedelta(days=int(tomorrow and not today))
        last = now.date() + timedelta(days=int(tomorrow))
        failed, forecast = weather.summarize(hit[1], now, SYMBOLS,
            from_date=first.isoformat(), to_date=last.isoformat())
        return None if failed else (forecast, round(age))

    def metro(self, now, walk):
        """Recompute countdowns from absolute times; bridge only a short SL outage."""
        note = ""
        try:
            data, stale = self._sl_snapshot(now)
            rows = metro_panel(data, now, walk)
            status = ('SL uppdateras' if any(rows.values()) else SL_MISSING) if stale else (
                f'{walk} min att gå' if any(rows.values()) else 'Inga avgångar')
            note = metro_note(data, self.sl_lines(), now)
        except OSError:
            with self._sl_lock:
                first = self._sl_first_try
            # After a server start SL may need several tries; say what the panel's own boot text says until a
            # board would have been called stale anyway.
            waiting = first is None or time.monotonic() - first < SL_STALE_S
            rows, status = metro_panel({}, now, walk), 'Uppdaterar avgångar' if waiting else SL_MISSING
            note = metro_note({}, self.sl_lines(), now)
        if note:
            rows['sl3'] = ''                    # the disruption line takes the fourth row (gen.py sl_note)
        if note != self._sl_note:
            print(f'home status: SL disruption line {"on" if note else "off"}: {note[:80]!r}', file=sys.stderr)
            self._sl_note = note
        self._record_shown(status)
        return {**rows, 'sl_walk': status, 'sl_note': note}

    def _record_shown(self, status):
        """Log each change of the panel's SL status and keep the longest SL_MISSING stretch."""
        at = time.monotonic()
        with self._sl_lock:
            stats = self._sl_stats
            stats['last_panel_read'] = at
            if status == stats['shown']:
                return
            before, since = stats['shown'], stats['shown_since']
            if before == SL_MISSING and since is not None:
                stats['longest_missing_s'] = max(stats['longest_missing_s'], at - since)
            stats['shown'], stats['shown_since'] = status, at
        if before is not None and (before == SL_MISSING or status == SL_MISSING):
            print(f'home status: SL panel status {before!r} -> {status!r}', file=sys.stderr)

    def sl_status(self):
        """Counts and ages for GET /sl/status; no departure text."""
        at = time.monotonic()
        with self._sl_lock:
            stats, hit = dict(self._sl_stats), self.cache.get(SL)
            stats['http'] = dict(stats['http'])
        age = lambda t: None if t is None else round(at - t, 1)
        shown_for = age(stats['shown_since'])
        longest = stats['longest_missing_s']
        if stats['shown'] == SL_MISSING and shown_for is not None:
            longest = max(longest, shown_for)
        return {'uptime_s': age(stats['started']), 'board_age_s': age(hit[0]) if hit else None,
                'last_ok_age_s': age(stats['last_ok']), 'last_ok_trains': stats['last_ok_trains'],
                'shown': stats['shown'], 'shown_for_s': shown_for,
                'last_panel_read_age_s': age(stats['last_panel_read']),
                'longest_missing_s': round(longest, 1),
                'replies': {k: stats[k] for k in ('ok', 'empty_kept', 'empty_accepted', 'timeout', 'other')},
                'http': stats['http'],
                'limits': {'refresh_s': SL_REFRESH_S, 'retry_s': SL_RETRY_S, 'stale_s': SL_STALE_S,
                           'max_age_s': SL_MAX_AGE_S}}

    def prefetch_sl(self):
        """Ask SL at server start, so the first wake word after a restart already has the metro."""
        try:
            self._sl_snapshot()
        except OSError:
            pass

    def metro_read(self, now, wait_s=3):
        try:
            data, stale = self._sl_snapshot(now)
        except OSError:
            # Just after a start the first answer may be on its way: give it a moment (voice only, never
            # GET /panel), then say plainly that the departures are not known yet.
            if not self._sl_done.wait(wait_s):
                return ['Tunnelbanan: avgångarna hämtas från SL just nu och är inte kända än.']
            try:
                data, stale = self._sl_snapshot(now)
            except OSError:
                return ['Tunnelbanan: SL har inte gett några avgångar sedan Jarvis startade; säg att du inte vet.']
        lines = metro_lines(data, now) + [f"SL meddelar om blå linjen: {m}" for m in self.sl_lines()]
        return (['SL: uppdatering saknas; följande avgångar är senast hämtade, inte bekräftade just nu.'] if stale else []) + lines

    def outside(self, now=None):
        """"6°, regn från 16:00" from SMHI (kept 15 minutes), or "" when SMHI cannot be read."""
        now = now or datetime.now(TZ)
        try:
            return outside_short(self._cached(SMHI, 15 * 60), now)
        except Exception as e:
            print(f"home status: SMHI failed: {e}", file=sys.stderr)
            return ""

    def _apod(self):
        if background.current() != background.NASA:
            title, credit = background.art()
            return [f"Panelens bakgrund: {title} av {credit}, vald i Home Assistant."]
        try:
            a = json.loads(APOD_JSON.read_text())
        except (OSError, ValueError):
            return []
        return [f"Panelens bakgrund: NASA:s rymdbild för {a['date']}, \"{a['title']}\"" +
                (f" av {a['credit']}" if a.get("credit") else "") + "."]

    def read(self):
        """Everything as Swedish lines, one source at a time in parallel; a source that fails says so."""
        now = datetime.now(TZ)
        sources = [("Home Assistant", lambda: self._ha(now)),
                   ("Vädret", lambda: weather_lines(self._cached(SMHI, 15 * 60), now)),
                   ("Tunnelbanan", lambda: self.metro_read(now)),
                   ("Rymdbilden", self._apod)]
        lines = []
        with ThreadPoolExecutor(len(sources)) as pool:
            futures = [(name, pool.submit(fn)) for name, fn in sources]
            for name, future in futures:
                try:
                    lines += future.result(timeout=4)
                except Exception as e:          # one missing source must not hide the others
                    lines.append(f"{name}: kunde inte läsas just nu.")
                    print(f"home status: {name} failed: {e}", file=sys.stderr)
        return "\n".join(lines)


if __name__ == "__main__":
    # python home_status.py                          the home status, as hemmet gives it
    # python home_status.py vader 2026-11-07 17:00 19:00   the vader tool for a day and window (free, SMHI)
    home = Home()
    t0 = time.monotonic()
    if sys.argv[1:2] == ["vader"]:
        day, start, end = (sys.argv[2:] + [None, None, None])[:3]
        print(home.forecast(**{k: v for k, v in (("from_date", day), ("from_time", start), ("to_time", end)) if v})[1])
    else:
        print(home.read())
    print(f"({time.monotonic() - t0:.2f} s)")
