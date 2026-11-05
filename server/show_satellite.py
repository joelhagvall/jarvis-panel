#!/usr/bin/env python3
"""The panel's voice satellite and the media players (MPD on the JBL, the panel), state by state in Swedish time, from Home Assistant's history
(read only). To see when Jarvis talked, next to show_turns.py and the server's log (Spotify through Soloist is no
Home Assistant player: its start is the log's `spotify: direct play started` line).

  python3 ~/hallpanel-live/server/show_satellite.py            the last 10 minutes
  python3 ~/hallpanel-live/server/show_satellite.py --minutes 60
"""
import argparse
import json
import os
import sys
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
TZ = ZoneInfo("Europe/Stockholm")
ENTITIES = (os.environ.get("JARVIS_PANEL_SATELLITE", "assist_satellite.hallpanel_assist_satellite"),
            "media_player.bluetooth_hogtalare", "media_player.hallpanel_hallpanel")   # MPD on the JBL, the panel


def lines(history):
    """'20:05:10.262  assist_satellite.hallpanel_assist_satellite  responding', oldest first, over all series."""
    rows = []
    for series in history or []:
        entity = (series[0] or {}).get("entity_id", "") if series else ""
        for s in series:
            when = datetime.fromisoformat(s["last_changed"]).astimezone(TZ)
            rows.append((when, s.get("entity_id") or entity, s["state"]))
    return [f"{w.strftime('%H:%M:%S.%f')[:-3]}  {e}  {st}" for w, e, st in sorted(rows)]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--minutes", type=int, default=10)
    args = parser.parse_args(argv)
    import hass
    start = (datetime.now(timezone.utc) - timedelta(minutes=args.minutes)).isoformat(timespec="seconds")
    path = (f"/api/history/period/{urllib.parse.quote(start)}?minimal_response&filter_entity_id="
            + ",".join(ENTITIES))
    print("\n".join(lines(hass.get_json(path, timeout=10))) or "inga ändringar")


if __name__ == "__main__":
    main()
