#!/usr/bin/env python3
"""Jarvis's moon tool (Joel, 2026-10-28): the moon's phase now, how much is lit, and the next full and new moon, read
from Home Assistant's sensor.jarvis_manen (ha/custom_components/jarvis_agent/moon.py computes it; no service).

  python3 server/agents/moon_phase.py
"""
import json
import sys
import urllib.error
from datetime import datetime
from zoneinfo import ZoneInfo

import hass

TZ = ZoneInfo("Europe/Stockholm")
ENTITY = "sensor.jarvis_manen"
TOOL = {"type": "function", "name": "manen", "strict": False,
        "description": ("Månen just nu: fas (t.ex. Fullmåne, Tilltagande månskära), hur många procent som är belyst, och "
                        "när nästa fullmåne och nymåne är (svensk tid, dagar kvar). Använd för frågor som \"är det "
                        "fullmåne i kväll?\" eller \"när är nästa fullmåne?\". Svara kort med fas och datum."),
        "parameters": {"type": "object", "properties": {}}}


def _local(iso, now):
    when = datetime.fromisoformat(iso).astimezone(TZ)
    return {"time": when.strftime("%Y-%m-%d %H:%M"), "weekday": ["måndag", "tisdag", "onsdag", "torsdag", "fredag",
            "lördag", "söndag"][when.weekday()], "days_left": round((when - now).total_seconds() / 86400, 1)}


def summary(state, now=None):
    """The tool's answer from the sensor's state: {phase, illumination_percent, next_full, next_new}."""
    now = (now or datetime.now(TZ)).astimezone(TZ)
    attrs = state.get("attributes") or {}
    return {"phase": state.get("state"), "illumination_percent": attrs.get("illumination"),
            "age_days": attrs.get("age_days"), "next_full": _local(attrs["next_full"], now),
            "next_new": _local(attrs["next_new"], now)}


def moon():
    """(is_error, JSON text)."""
    try:
        state = hass.get_json(f"/api/states/{ENTITY}")
        return False, json.dumps(summary(state), ensure_ascii=False)
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError) as e:
        return True, f"Månen gick inte att läsa just nu: {e}"


if __name__ == "__main__":
    error, text = moon()
    print(text)
    sys.exit(1 if error else 0)
