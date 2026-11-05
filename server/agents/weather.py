"""SMHI SNOW point forecast summaries for any available date/time window.

Uses the raw forecast already cached by Home. Times are Stockholm local time. Temperature and
wind are valid at `time`; precipitation amounts belong to intervalParametersStartTime..time.
Never extrapolate, interpolate coarse periods, or present missing values (9999) as observations.
"""
import json
import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")
SOURCE = "https://opendata.smhi.se/metfcst/snow1gv1"


def number(data, key, low=None, high=None):
    value = data.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value == 9999:
        return None
    if (low is not None and value < low) or (high is not None and value > high):
        return None
    return value


def iso(dt):
    return dt.astimezone(TZ).isoformat(timespec="seconds")


NEAREST_H = 6       # how far outside a short time window the closest forecast point may lie


def summarize(raw, now, symbols, from_date=None, to_date=None, from_time=None, to_time=None):
    """(is_error, JSON summary or short error). Default: all remaining dates in this forecast."""
    try:
        if not isinstance(raw, dict):
            raise ValueError("SMHI gav ett ogiltigt prognossvar")
        rows = []
        for point in raw.get("timeSeries") or []:
            if not isinstance(point, dict):
                continue
            try:
                end = datetime.fromisoformat(point["time"].replace("Z", "+00:00"))
                start = datetime.fromisoformat(point["intervalParametersStartTime"].replace("Z", "+00:00"))
                if start.tzinfo is None or end.tzinfo is None or end <= start:
                    continue
                rows.append((start.astimezone(TZ), end.astimezone(TZ), point.get("data") or {}))
            except (KeyError, ValueError, TypeError):
                continue
        if not rows:
            raise ValueError("SMHI saknar användbara prognostider")
        rows.sort(key=lambda r: r[1])
        available_start, available_end = min(r[0] for r in rows), max(r[1] for r in rows)
        first = date.fromisoformat(from_date) if from_date else now.astimezone(TZ).date()
        last = date.fromisoformat(to_date) if to_date else (first if from_date else available_end.date())
        if last < first or (last - first).days > 14:
            raise ValueError("Välj ett datumintervall på högst 15 dagar, i stigande ordning")
        if first < now.astimezone(TZ).date():
            raise ValueError("Verktyget ger prognoser, inte historiskt väder")
        start_clock = time.fromisoformat(from_time) if from_time else time.min
        end_clock = time.fromisoformat(to_time) if to_time else time.min
        if start_clock.tzinfo or end_clock.tzinfo or (to_time and end_clock <= start_clock):
            raise ValueError("Tidsfönstret ska vara i svensk tid, med sluttid efter starttid samma dag")
        days = []
        for n in range((last - first).days + 1):
            day = first + timedelta(days=n)
            finish = datetime.combine(day if to_time else day + timedelta(days=1), end_clock, TZ)
            begin = min(finish, max(datetime.combine(day, start_clock, TZ), now.astimezone(TZ)))
            instants = [(end, d) for start, end, d in rows if begin <= end < finish]
            nearest = False
            if not instants and (from_time or to_time):
                # Further ahead SMHI has a point every 6-12 h, so a short window ("lördag 17-19") can hold none:
                # take the closest point within NEAREST_H of it instead of no weather at all (Joel, 2026-11-01).
                middle = begin + (finish - begin) / 2
                close = [(abs(end - middle), end, d) for start, end, d in rows
                         if end >= now and begin - timedelta(hours=NEAREST_H) <= end <= finish + timedelta(hours=NEAREST_H)]
                if close:
                    _, end, d = min(close, key=lambda c: c[0])
                    instants, nearest = [(end, d)], True
            periods = [(start, end, d) for start, end, d in rows if begin < finish and start < finish and end > begin]
            temps = [v for end, d in instants if (v := number(d, "air_temperature", -100, 70)) is not None]
            wind = [v for end, d in instants if (v := number(d, "wind_speed", 0, 200)) is not None]
            gust = [v for end, d in instants if (v := number(d, "wind_speed_of_gust", 0, 200)) is not None]
            probability = [v for start, end, d in periods
                           if (v := number(d, "probability_of_precipitation", 0, 100)) is not None]
            conditions = list(dict.fromkeys(symbols[v - 1] for end, d in instants
                                            if isinstance(v := d.get("symbol_code"), int) and 1 <= v <= len(symbols)))
            precipitation = []
            for start, end, d in periods:
                amount = number(d, "precipitation_amount_mean", 0)
                chance = number(d, "probability_of_precipitation", 0, 100)
                if (amount is not None and amount >= 0.1) or (chance is not None and chance >= 20):
                    precipitation.append({"from": iso(start), "to": iso(end), "mean_mm_in_full_interval": amount,
                                          "probability_percent": chance,
                                          "frozen_share_percent": number(d, "precipitation_frozen_part", 0, 100)})
            summary = {"date": day.isoformat(), "window_from": iso(begin), "window_to": iso(finish),
                       "available": bool(temps),
                       **({"nearest_point_outside_window": True} if nearest else {}),
                       "partial_coverage": begin < available_start or finish > available_end,
                       "sampled_temperature_min_c": min(temps) if temps else None,
                       "sampled_temperature_max_c": max(temps) if temps else None,
                       "wind_max_ms": max(wind) if wind else None, "gust_max_ms": max(gust) if gust else None,
                       "precipitation_probability_max_percent": max(probability) if probability else None,
                       "conditions": conditions, "precipitation_intervals": precipitation}
            # On a day-specific question expose each valid time, but keep week answers compact.
            if (last - first).days <= 1:
                summary["time_points"] = [
                    {"time": iso(end), "temperature_c": number(d, "air_temperature", -100, 70),
                     "wind_ms": number(d, "wind_speed", 0, 200), "gust_ms": number(d, "wind_speed_of_gust", 0, 200),
                     "condition": symbols[v - 1] if isinstance(v := d.get("symbol_code"), int)
                     and 1 <= v <= len(symbols) else None} for end, d in instants]
            days.append(summary)
        if not any(d["available"] for d in days):
            raise ValueError(f"Ingen temperaturprognos för valt datum/tidsfönster. Prognosen täcker "
                             f"{iso(available_start)} till {iso(available_end)}; använd inte dagens väder i stället")
        result = {"source": "SMHI SNOW1g", "source_url": SOURCE, "area": "Stockholm, Kungsholmen",
                  "timezone": "Europe/Stockholm", "created_at": raw.get("createdTime"),
                  "reference_time": raw.get("referenceTime"), "available_from": iso(available_start),
                  "available_through": iso(available_end), "days": days,
                  "note": "Detta är prognoser. Temperaturspann gäller tillgängliga mätpunkter, inte garanterade "
                          "dygnsextremer. Tidsstegen blir glesare längre fram; utlova inte exakt regnstart där. "
                          "Nederbördsmängder gäller hela angivna intervallet, även om det korsar midnatt eller "
                          "tidsfönstret. Summera inte delintervall som hela dygn. Saknade värden är okända, inte noll. "
                          "Maximal regnsannolikhet är högsta intervallets chans, inte en beräknad dygnssannolikhet. "
                          "Dagar med available=false saknar temperaturunderlag. nearest_point_outside_window: fönstret hade ingen "
                          "prognostid, så närmaste (time_points) används; säg ungefär, inte exakt för fönstret."}
        return False, json.dumps(result, ensure_ascii=False, allow_nan=False)
    except (ValueError, TypeError, KeyError) as e:
        return True, f"Väderprognosen kunde inte verifieras: {e}."


# ---------------------------------------------------------------------------------------------- the panel's weather
# The dashboard card's next three days and the weather page behind it (Joel, 2026-10-29; the design is
# sketch/weather_mock.py). Strings only: '|' between fields and ';' between rows, icons by name (weather_view.h
# draws them from Material Design Icons). Same SMHI rules as summarize(): temperature and wind at `time`,
# precipitation for its whole interval, 9999 missing.
WEEKDAYS = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]


def icon(symbol, night=False):
    """SMHI's symbol code as weather_view.h's icon name."""
    if symbol in (1, 2):
        return "moon" if night else "sun"
    if symbol in (3, 4):
        return "partly_night" if night else "partly"
    return {5: "cloud", 6: "cloud", 7: "fog", 10: "pour", 20: "pour", 11: "thunder", 21: "thunder"}.get(
        symbol, "rain" if symbol in (8, 9, 18, 19) else "sleet" if symbol in (12, 13, 14, 22, 23, 24)
        else "snow" if symbol in (15, 16, 17, 25, 26, 27) else "cloud")


def deg(value):
    return f"{round(value)}°"


def mm(value):
    return f"{value:.1f}".replace(".", ",") + " mm"


def daylight(sun):
    """(sunrise, sunset) as local times of day from Home Assistant's sun.sun attributes, or 07:00-17:00."""
    try:
        rise = datetime.fromisoformat(sun["next_rising"]).astimezone(TZ).time()
        setting = datetime.fromisoformat(sun["next_setting"]).astimezone(TZ).time()
        return rise, setting
    except (KeyError, TypeError, ValueError):
        return time(7), time(17)


def rows_of(raw):
    rows = []
    for point in (raw or {}).get("timeSeries") or []:
        try:
            end = datetime.fromisoformat(point["time"].replace("Z", "+00:00")).astimezone(TZ)
            start = datetime.fromisoformat(point.get("intervalParametersStartTime", point["time"]).replace(
                "Z", "+00:00")).astimezone(TZ)
        except (KeyError, ValueError, TypeError, AttributeError):
            continue
        rows.append((start, end, point.get("data") or {}))
    return sorted(rows, key=lambda r: r[1])


def day_rows(rows, day, now):
    begin = max(datetime.combine(day, time.min, TZ), now)
    finish = datetime.combine(day + timedelta(days=1), time.min, TZ)
    return [r for r in rows if begin <= r[1] < finish], [r for r in rows if begin < r[1] <= finish]


def one_day(rows, day, now):
    """(name, icon, high, low, rain mm, gust) of one day, or None without temperatures."""
    instants, periods = day_rows(rows, day, now)
    temps = [v for _, _, d in instants if (v := number(d, "air_temperature", -100, 70)) is not None]
    if not temps:
        return None
    rain = sum(v for _, _, d in periods if (v := number(d, "precipitation_amount_mean", 0)) is not None)
    gust = max((v for _, _, d in instants if (v := number(d, "wind_speed_of_gust", 0, 200)) is not None), default=0)
    wettest = max(periods, key=lambda r: number(r[2], "precipitation_amount_mean", 0) or 0, default=None)
    if wettest and (number(wettest[2], "precipitation_amount_mean", 0) or 0) >= 0.3:
        symbol = wettest[2].get("symbol_code")
    else:   # the sky at midday (today's is set to the page's "nu" in panel())
        noon = datetime.combine(day, time(13), TZ)
        symbol = min(instants, key=lambda r: abs((r[1] - noon).total_seconds()))[2].get("symbol_code")
    name = "idag" if day == now.date() else WEEKDAYS[day.weekday()]
    return name, icon(symbol, night=False), max(temps), min(temps), rain, gust


def panel(raw, now, symbols, sun=None):
    """The card's w_days and the weather page's wx_* fields; "" each when SMHI has nothing."""
    out = dict.fromkeys(("w_days", "wx_sky", "wx_sub", "wx_wet", "wx_facts", "wx_hours", "wx_week"), "")
    now = now.astimezone(TZ)
    rows = [r for r in rows_of(raw) if r[1] > now - timedelta(minutes=30)]
    if not rows:
        return out
    rise, setting = daylight(sun)

    def night(when):
        return not rise <= when.time() < setting
    week = [d for n in range(8) if (d := one_day(rows, now.date() + timedelta(days=n), now))][:7]
    if week and week[0][0] == "idag" and not (week[0][4] >= 0.3):
        week[0] = (week[0][0], icon(rows[0][2].get("symbol_code"), night(rows[0][1])), *week[0][2:])
    out["w_days"] = ";".join(["|".join((n, i, deg(hi), deg(lo))) for n, i, hi, lo, _, _ in week if n != "idag"][:3])
    out["wx_week"] = ";".join("|".join((n, i, deg(hi), deg(lo), mm(r) if r >= 0.1 else "uppehåll", f"byar {round(g)}"))
                              for n, i, hi, lo, r, g in week)
    hourly = [r for r in rows if r[1] - r[0] == timedelta(hours=1)][:24]
    out["wx_hours"] = ";".join(
        "|".join(("nu" if k == 0 else f"{end:%H}", icon(d.get("symbol_code"), night(end)),
                  f"{number(d, 'air_temperature', -100, 70) or 0:.1f}",
                  f"{number(d, 'precipitation_amount_mean', 0) or 0:.1f}"))
        for k, (_, end, d) in enumerate(hourly))
    # One hour for the header's sky, the "nu" hour and today's icon, so the page never shows two skies at once.
    first = rows[0][2]
    symbol = first.get("symbol_code")
    out["wx_sky"] = symbols[symbol - 1].capitalize() if isinstance(symbol, int) and 1 <= symbol <= len(symbols) else ""
    wet = next(((s, d) for s, e, d in rows if s < now + timedelta(hours=12)
                and (number(d, "precipitation_amount_mean", 0) or 0) >= 0.1), None)
    if wet:
        snow = (number(wet[1], "probability_of_frozen_precipitation", 0, 100) or 0) > 50
        out["wx_sub"] = (f"{'Snö' if snow else 'Regn'} {'nu' if wet[0] <= now else 'från ' + f'{wet[0]:%H:%M}'}"
                         f" · {'ta varma kläder' if snow else 'ta paraply'}")
        out["wx_wet"] = "on"
    else:
        tonight = [v for _, e, d in rows if e < datetime.combine(now.date() + timedelta(days=1), time(8), TZ)
                   and (v := number(d, "air_temperature", -100, 70)) is not None]
        out["wx_sub"] = f"Högst {deg(week[0][2])}, i natt ner mot {deg(min(tonight))}" if week and tonight else ""
        out["wx_wet"] = "off"
    chance = max((v for s, e, d in rows if s < now + timedelta(hours=12)
                  and (v := number(d, "probability_of_precipitation", 0, 100)) is not None), default=None)
    facts = [(number(first, "wind_speed", 0, 200), number(first, "wind_speed_of_gust", 0, 200)),
             number(first, "relative_humidity", 0, 100), chance, number(first, "air_pressure_at_mean_sea_level", 800, 1200)]
    wind, gust = facts[0]
    out["wx_facts"] = "|".join((
        f"{round(wind)} m/s" + (f", byar {round(gust)}" if gust is not None else "") if wind is not None else "–",
        f"{round(facts[1])} %" if facts[1] is not None else "–",
        f"{round(facts[2])} %" if facts[2] is not None else "–",
        f"{round(facts[3])} hPa" if facts[3] is not None else "–"))
    return out
