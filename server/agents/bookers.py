#!/usr/bin/env python3
"""Jarvis's tool automatiska_bokningar (Joel, 2054-11-04): "när bokar den nästa tvättid automatiskt?",
"har Gympassen bokats?".

Two cron jobs on Jarvis book for Joel, and each keeps its status in ~/.cache/jarvis-alerts/booking-<job>.json
(their shared jarvis_status.py, which also publishes it to Home Assistant over MQTT):
  tvatt  ~/tvatt-booker/tvatt_book.py  Saturdays 10:15, books two colours 12-15 on the next Saturday of the
         fortnightly rhythm (anchor 2054-10-10); a dry run ("canary") Fridays 20:00
  sats   ~/sats-booker/sats_book.py    Thursdays 18:00, books a gym class one week ahead;
         a canary Wednesdays 20:00
These are fictional example schedules; configure your own bookers. Read only: this tool runs nothing.

Free read on Jarvis:
  cd ~/hallpanel-live/server/agents && ~/hallpanel-live/venv/bin/python bookers.py
"""
import json
import os
import sys
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

STATE_DIR = Path(os.environ.get("JARVIS_BOOKERS_DIR", "~/.cache/jarvis-alerts")).expanduser()
TZ = ZoneInfo("Europe/Stockholm")
LAUNDRY_ANCHOR, LAUNDRY_EVERY = date(2054, 10, 10), 14
FIELDS = {"last_run": "senaste_körning", "last_result": "senaste_resultat", "last_message": "senaste_besked",
          "next_booking": "nästa_kända_bokning", "next_booking_checked_at": "nästa_bokning_kontrollerad",
          "canary_last": "senaste_provkörning", "canary_result": "provkörningens_resultat",
          "canary_message": "provkörningens_besked"}

TOOL = {"type": "function", "name": "automatiska_bokningar", "strict": False,
        "description": ("Joels automatiska bokningar som körs av sig själva på Jarvis: tvättstugan (två färger "
                        "12-15 varannan lördag) och ett gympass varje vecka. Visar när de körs nästa gång, vad de "
                        "bokar då och hur senaste körningen och provkörningen gick. Läser bara. nästa_kända_bokning "
                        "kan vara inaktuell om han har avbokat själv; hans aktuella tvättbokningar ger verktyget "
                        "tvatt. Skriv tvättpassen som \"12-15\"."),
        "parameters": {"type": "object", "properties": {}}}


def _next_weekday_at(now, weekday, at):
    """The next moment on weekday (Thursday 0) at the time at, from now (now itself counts if not yet passed)."""
    day = now.date() + timedelta(days=(weekday - now.weekday()) % 7)
    when = datetime.combine(day, at, TZ)
    return when if when > now else when + timedelta(days=7)


def next_rhythm_saturday(day):
    """The first laundry Saturday after day, as tvatt_book.py's next_rhythm_saturday."""
    n = max(0, -(-((day - LAUNDRY_ANCHOR).days + 1) // LAUNDRY_EVERY))
    return LAUNDRY_ANCHOR + timedelta(days=n * LAUNDRY_EVERY)


def _status(job):
    try:
        data = json.loads((STATE_DIR / f"booking-{job}.json").read_text())
    except (OSError, ValueError) as e:
        print(f"bookers: no status for {job}: {e}", file=sys.stderr)
        return {"status": "saknas"}
    return {sv: data[en] for en, sv in FIELDS.items() if data.get(en) not in (None, "")}


def read(now=None, **_):
    now = (now or datetime.now(TZ)).astimezone(TZ)
    laundry_run = _next_weekday_at(now, 5, time(10, 15))
    sats_run = _next_weekday_at(now, 3, time(18, 0))
    result = {
        "nu": now.isoformat(timespec="minutes"),
        "tvättstugan": {
            "schema": "Körs varje lördag 10:15 och bokar två färger 12-15 nästa lördag i varannan-veckas-rytmen, "
                      "om han har plats (högst två bokningar). Provkörning fredagar 20:00.",
            "nästa_körning": laundry_run.isoformat(timespec="minutes"),
            "bokar_då": f"{next_rhythm_saturday(laundry_run.date()).isoformat()} 12-15",
            **_status("tvatt")},
        "Gympass": {
            "schema": "Körs varje torsdag 18:00 och bokar ett gympass en vecka fram, utan kölista. "
                      "Provkörning onsdagar 20:00.",
            "nästa_körning": sats_run.isoformat(timespec="minutes"),
            "bokar_då": f"{(sats_run.date() + timedelta(days=7)).isoformat()} 18:00",
            **_status("sats")},
    }
    return False, json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    print(json.dumps(json.loads(read()[1]), ensure_ascii=False, indent=1))
