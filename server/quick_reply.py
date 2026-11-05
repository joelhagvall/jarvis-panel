"""Exact, read-only voice questions answered from the same sources as the agent.

Compound requests, follow-ups, changes and ambiguous wording always go to the agent. Missing,
stale or malformed data also falls through; this path never invents an empty calendar or dry weather.
"""
import json
import re
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import home_status
import silent_topics

TZ = ZoneInfo("Europe/Stockholm")
WEEKDAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")


def answer(text, home=None, agenda=None, language=None, now=None):
    if language not in (None, "sv"):
        return None
    query = re.sub(r"\s+", " ", text.casefold()).strip().rstrip("?.!").strip()
    now = now or datetime.now(TZ)
    try:
        if re.fullmatch(r"(?:vad är klockan|hur mycket är klockan)", query):
            return f"Klockan är {now:%H:%M}."
        if home and re.fullmatch(r"(?:när går|vilken tid går) nästa tunnelbana(?:n)?(?: från stadshagen)?", query):
            data = home._cached(home_status.SL, 20)
            trains = []
            for row in data.get("departures") or []:
                if home_status.cancelled(row):
                    continue
                at = home_status.parse_time(row.get("expected") or row["scheduled"])
                if at >= now and row.get("destination"):
                    trains.append((at, row))
            trains.sort(key=lambda row: row[0])
            chosen, directions = [], set()
            for at, row in trains:
                direction = row.get("direction_code")
                if direction in directions:
                    continue
                directions.add(direction)
                chosen.append(f"mot {row['destination']} klockan {home_status.clock(at)}")
                if len(chosen) == 2:
                    break
            if chosen and not data.get("stop_deviations"):
                return "Nästa tunnelbana från Stadshagen går " + ", och ".join(chosen) + "."
        weather = re.fullmatch(r"(?:vad blir|hur blir|vad är|hur är) vädret(?: (i dag|idag|i morgon|imorgon))?", query)
        if home and weather:
            tomorrow = weather[1] in ("i morgon", "imorgon")
            day = (now + timedelta(days=int(tomorrow))).date().isoformat()
            error, result = home.forecast(from_date=day)
            if error:
                return None
            data = json.loads(result)["days"][0]
            low, high = data["sampled_temperature_min_c"], data["sampled_temperature_max_c"]
            if not data["available"] or data["partial_coverage"] or low is None or high is None:
                return None
            temperature = (f"kring {home_status.num(low)}" if low == high else
                           f"mellan {home_status.num(low)} och {home_status.num(high)}")
            reply = f"SMHI:s prognos för {'i morgon' if tomorrow else 'resten av dagen'} visar {temperature} grader"
            if data["conditions"]:
                reply += ", " + ", ".join(data["conditions"][:3])
            reply += "."
            if (chance := data["precipitation_probability_max_percent"]) is not None:
                reply += f" Risken för nederbörd är som högst {home_status.num(chance, 0)} procent under perioden."
            return reply
        # "på kalendern" is how Joel says it: four times through the model on 2026-11-01, 1.8-3.1 s each
        calendar = re.fullmatch(r"vad har jag (?:i kalendern|på kalendern|inbokat) (idag|i dag|imorgon|i morgon|på "
                                + "|på ".join(WEEKDAYS) + r")", query)
        if agenda and 0 <= agenda[1] <= 30 and calendar:
            when = calendar[1]
            offset = 0 if when in ("idag", "i dag") else 1
            label = "i dag" if offset == 0 else "i morgon"
            if when.startswith("på "):
                offset = (WEEKDAYS.index(when[3:]) - now.weekday()) % 7
                if offset == 0:  # "på fredag" on a Friday could mean this Friday or next week.
                    return None
                label = when
            # The snapshot can have started just before midnight. Leave its final day to a fresh lookup.
            if type(agenda[2]) is not int or not offset < agenda[2] - 1:
                return None
            day = (now + timedelta(days=offset)).date().isoformat()
            lines = agenda[0].strip().splitlines()
            # Repeated occurrences may carry a date but no time. They cannot answer that day's question,
            # but a validated occurrence on another single date does not invalidate the whole week.
            pattern = (r"(\d{4}-\d\d-\d\d) (?:(\d\d:\d\d)-(\d\d:\d\d) )?\| ([^|]+) \| ([^|]+) \| id \S+"
                       r"(?: \| repeats, occurrence (\d{4}-\d\d-\d\d))?")
            rows = [re.fullmatch(pattern, line.strip()) for line in lines]
            if lines and all(rows) and all((not row[6] or row[6] == row[1])
                                          and (row[2] or row[6]) for row in rows):
                if any(row[1] == day and not row[2] for row in rows):
                    return None
                events = [row for row in rows if row[1] == day
                         and not silent_topics.silent(row[5])]
                if events:
                    return label.capitalize() + " har du " + "; ".join(f"{row[5]} klockan {row[2]}" for row in events) + "."
                return f"Inget att ta upp i kalendern {label}."
    except (OSError, ValueError, KeyError, TypeError, IndexError, AttributeError):
        return None
    return None
