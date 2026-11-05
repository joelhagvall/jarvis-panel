#!/usr/bin/env python3
"""Jarvis's volume tool: the panel's speaker, or the Bluetooth speaker while one is connected. Standard library only.

  status            the volume where the sound plays now
  set (percent)     to that many percent
  up / down (step)  by step percentage points, 10 by default

"Where" is auto by default: the Bluetooth speaker while one is connected (speaker.output), else the panel.
The panel's volume is Home Assistant's media_player.volume_set on JARVIS_RADIO_PLAYER; the panel keeps it as
Joel's day volume (bench/volume_state.h), as if he had moved Volym in Inställningar. The Bluetooth speaker's
goes through the integration's jarvis_agent.speaker_volume: while Jarvis listens and answers the music is
turned down (speaker_duck.py), and a plain MPD setvol would be undone when he has answered.

  python3 server/agents/volume.py status
  python3 server/agents/volume.py set 40
"""
import json
import os
import sys
import urllib.error

import hass
import speaker

TOOL = {"type": "function", "name": "volym", "strict": False,
        "description": ("Volymen där ljudet spelas: på Bluetooth-högtalaren (t.ex. JBL:en) när en är kopplad, annars "
                        "panelens egen högtalare. where styr det om Joel säger var (panelen eller högtalaren). Säg "
                        "efteråt kort var och vilken nivå det blev, t.ex. \"Högtalaren står på 40 procent.\""),
        "parameters": {"type": "object", "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["status", "set", "up", "down"]},
            "percent": {"type": "integer", "minimum": 0, "maximum": 100, "description": "Nivån vid set"},
            "step": {"type": "integer", "minimum": 1, "maximum": 100,
                     "description": "Hur många procentenheter vid up/down, standard 10"},
            "where": {"type": "string", "enum": ["auto", "panel", "hogtalare"], "description": "Standard auto"}}}}


def _where(where):
    """(entity or None for the Bluetooth speaker, spoken name)."""
    where = str(where or "auto").lower()
    if where != "panel":
        connected = None
        if speaker.configured():
            try:
                connected = speaker.connected()
            except speaker.SpeakerError:
                pass
        if connected:
            return None, connected["name"]
        if where == "hogtalare":
            raise LookupError("Ingen Bluetooth-högtalare är kopplad.")
    panel = os.environ.get("JARVIS_RADIO_PLAYER")
    if not panel:
        raise LookupError("Panelens högtalare är inte ansluten till Home Assistant.")
    return panel, "Panelen"


def _speaker_volume(level=None, step=None):
    data = {k: v for k, v in (("level", level), ("step", step)) if v is not None}
    body = json.loads(hass.request("/api/services/jarvis_agent/speaker_volume?return_response", data, timeout=8))
    return int(body["service_response"]["volume"])


def _panel_volume(entity, level=None, step=None):
    state = hass.get_json(f"/api/states/{entity}", timeout=5) or {}
    raw = (state.get("attributes") or {}).get("volume_level")
    if raw is None:
        raise LookupError("Panelen säger inte sin volym just nu.")
    current = round(float(raw) * 100)
    if level is None and step is None:
        return current
    new = max(0, min(100, level if level is not None else current + step))
    hass.call_service("media_player", "volume_set", {"entity_id": entity, "volume_level": new / 100}, timeout=8)
    return new


def control(action="status", percent=None, step=None, where="auto"):
    action = str(action or "status").lower()
    if action == "set" and percent is None:
        return True, "Vilken nivå? Säg till exempel 40 procent."
    level = int(percent) if action == "set" else None
    move = None
    if action in ("up", "down"):
        move = int(step or 10) * (1 if action == "up" else -1)
    try:
        entity, name = _where(where)
        new = _speaker_volume(level, move) if entity is None else _panel_volume(entity, level, move)
    except LookupError as e:
        return True, str(e)
    except hass.Refused:
        return True, "Jarvis har ingen giltig token till Home Assistant."
    except urllib.error.HTTPError as e:
        return True, f"Home Assistant svarade {e.code} på volymen."
    except (OSError, ValueError, KeyError, urllib.error.URLError) as e:
        return True, f"Volymen gick inte att nå ({type(e).__name__})."
    return False, f"{name}: {new} %."


if __name__ == "__main__":
    action, *rest = sys.argv[1:] or ["status"]
    number = int(rest[0]) if rest and rest[0].lstrip("-").isdigit() else None
    error, text = control(action, percent=number if action == "set" else None,
                          step=number if action in ("up", "down") else None)
    print(("FEL: " if error else "") + text)
