#!/usr/bin/env python3
"""Jarvis's tool for the panel itself: which page it shows, its background and the screen light. Standard
library only.

  sida       show a page: Home Assistant's esphome.hallpanel_show_page (board_config/voice.py's show_page action).
             While Jarvis answers, the panel shows it when the voice page goes back; otherwise at once.
  bakgrund   the background, the same choices as Inställningar and select.jarvis_bakgrund (server/agents/background.py,
             in this process: a new picture is baked first, about 4.5 s on Jarvis)
  skarmljus  the day's screen light in percent: light.hallpanel_skarmljus (the panel keeps it as day_light)
  status     the background and the light now; the page is not known here

The audio output (the panel or the JBL) is the hogtalare tool, the volume is volym.

  python3 server/agents/panel.py sida hemmet
  python3 server/agents/panel.py bakgrund Stjärnenatten
"""
import os
import sys
import urllib.error

import background
import hass

TOOL = {"type": "function", "name": "panelen", "strict": False,
        "description": ("Panelen i hallen, skärmen Joel pratar med: byt sida, byt bakgrund eller ställ skärmljuset. "
                        "Sidor: hem (Gå hemifrån: klocka, väder, kalender, tunnelbanan), hemmet (läget hemma), "
                        "musik (Spelas nu), tavla (dagens bild), natt, vader, kalender, paminnelser, ai. "
                        "Bakgrunder: NASA, Växlande, Stjärnenatten, Mandelblom, Dagens målning, Dagens foto, Bing, "
                        "NASA Jordbild eller en uppladdad bild; status listar dem. Skärmljus 1-100 procent. "
                        "Ljudet (panelen eller JBL:en) byts med hogtalare, volymen med volym. Säg kort vad som ändrades."),
        "parameters": {"type": "object", "required": ["action"], "properties": {
            "action": {"type": "string", "enum": ["status", "sida", "bakgrund", "skarmljus"]},
            "sida": {"type": "string", "description": "Vid sida: hem, hemmet, musik, tavla, natt, vader, kalender, paminnelser eller ai"},
            "val": {"type": "string", "description": "Vid bakgrund: bakgrundens namn"},
            "procent": {"type": "integer", "minimum": 1, "maximum": 100, "description": "Vid skarmljus"}}}}

# spoken name -> (page id, what Jarvis calls it). Order matters: the first whose words are in what he said wins.
PAGES = [
    (("hemmet", "läget hemma", "home"), "page_hemmet", "Hemmet"),           # before "hem", which is in "hemmet"
    (("hemskärm", "startsid", "gå hemifrån", "hemifrån", "dashboard", "första sidan", "hem"), "page_glass_v2", "Gå hemifrån"),
    (("spelas nu", "musik", "låten", "skivan"), "page_musik", "Spelas nu"),
    (("tavla", "bild", "foto", "konst"), "page_tavla", "Tavla"),
    (("natt", "night"), "page_natt", "Natt"),
    (("väd", "vader", "prognos"), "page_vader", "Väder"),                  # väder, vädret, väderprognosen
    (("kalender", "veckan", "agenda"), "page_kalender", "Kalender"),
    (("påminnelse", "paminnelse", "timer"), "page_paminnelser", "Påminnelser"),
    (("ai", "användning", "tokens"), "page_ai", "AI-användning"),
]
LIGHT = os.environ.get("JARVIS_PANEL_LIGHT", "light.hallpanel_skarmljus")
DEVICE = os.environ.get("JARVIS_PANEL_DEVICE", "hallpanel")       # esphome.<device>_show_page


def page_for(spoken):
    """(page id, name) for what Joel said, or None."""
    said = " ".join(str(spoken or "").casefold().split())
    if not said:
        return None
    for words, pid, name in PAGES:
        if any(w in said for w in words):
            return pid, name
    return None


def background_for(spoken):
    """The background choice whose name Joel said, or None."""
    said = str(spoken or "").casefold().strip()
    options = background.choices()
    exact = [o for o in options if o.casefold() == said]
    if exact:
        return exact[0]
    loose = [o for o in options if said and (said in o.casefold() or o.casefold() in said)]
    return loose[0] if len(loose) == 1 else None


def _ha(call):
    """Run a Home Assistant call; the reason in Swedish when it fails."""
    try:
        return False, call()
    except hass.Refused as e:
        return True, f"Home Assistant släpper inte in Jarvis ({e})."
    except urllib.error.HTTPError as e:
        return True, f"Home Assistant svarade {e.code}; panelen kan behöva ny firmware (show_page saknas)." if e.code in (400, 404) \
            else f"Home Assistant svarade {e.code}."
    except OSError as e:
        return True, f"Home Assistant svarade inte ({type(e).__name__})."


def control(action="status", sida=None, val=None, procent=None, **_):
    action = str(action or "status").lower()
    if action == "sida":
        found = page_for(sida)
        if not found:
            return True, f"Ingen sida heter {sida!r}. Sidorna är hem, hemmet, musik, tavla, natt, vader, kalender, paminnelser och ai."
        pid, name = found
        error, text = _ha(lambda: hass.call_service("esphome", f"{DEVICE}_show_page", {"page": pid}, timeout=8))
        return (True, text) if error else (False, f"Panelen visar {name}.")
    if action == "bakgrund":
        choice = background_for(val)
        if not choice:
            return True, f"Ingen bakgrund heter {val!r}. Valen är {', '.join(background.choices())}."
        try:
            background.choose(choice)
        except (ValueError, OSError) as e:
            return True, f"Bakgrunden gick inte att byta: {e}"
        return False, f"Bakgrunden är nu {choice}."
    if action == "skarmljus":
        if procent is None:
            return True, "Hur ljus? Säg till exempel 60 procent."
        level = max(1, min(100, int(procent)))
        error, text = _ha(lambda: hass.call_service("light", "turn_on", {"entity_id": LIGHT, "brightness_pct": level}, timeout=8))
        return (True, text) if error else (False, f"Skärmljuset står på {level} procent.")
    # status
    lines = [f"Bakgrund: {background.current()} (valen: {', '.join(background.choices())})."]
    error, state = _ha(lambda: hass.get_json(f"/api/states/{LIGHT}", timeout=5) or {})
    if not error and isinstance(state, dict) and state.get("state"):
        raw = (state.get("attributes") or {}).get("brightness")
        lines.append(f"Skärmljus: {round(int(raw) / 255 * 100)} procent." if raw is not None else f"Skärmljus: {state['state']}.")
    return False, " ".join(lines)


if __name__ == "__main__":
    argv = sys.argv[1:]
    action = argv[0] if argv else "status"
    rest = " ".join(argv[1:])
    kwargs = {"sida": rest} if action == "sida" else {"val": rest} if action == "bakgrund" else \
             {"procent": int(rest)} if action == "skarmljus" and rest else {}
    error, text = control(action, **kwargs)
    print(("FEL: " if error else "") + text)
    sys.exit(1 if error else 0)
