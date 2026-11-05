#!/usr/bin/env python3
"""Joel's AI subscriptions for Jarvis (Joel, 2026-11-01): "hur mycket AI har jag använt?" gets a short spoken
summary, and the panel opens its AI page once he has finished speaking (styles/glass-v2/ai_page.py).

The numbers are Home Assistant sensors written every 15 minutes by ~/jarvis-docs/scripts/bin/ai-usage.py
(sensor.ai_tokens, sensor.ai_claude_1, sensor.ai_claude_2, sensor.ai_codex; docs ~/jarvis-docs/services/ai-usage.md).
Asking opens the page by writing sensor.jarvis_open_page = "ai@<unix time>"; the panel only follows a request
from the last two minutes. Standard library only.

Free test of the jump without a model (the panel must be on a page with the tab bar, by day), on Jarvis:
  cd ~/hallpanel-live/server/agents && ~/hallpanel-live/venv/bin/python -c "import ai_usage; ai_usage.open_page()"
then `tools/panel ui` shows page_ai and `tools/panel logs` has "Jarvis asked for the AI page".
"""
import json
import sys
import time
import urllib.error

import hass

ENTITIES = {"claude_1": "sensor.ai_claude_1", "claude_2": "sensor.ai_claude_2", "codex": "sensor.ai_codex"}
TOKENS = "sensor.ai_tokens"
OPEN = "sensor.jarvis_open_page"
NAMES = {"claude_1": "Claude 1 (Macen)", "claude_2": "Claude 2 (Jarvis)", "codex": "Codex"}

TOOL = {"type": "function", "name": "ai_anvandning", "strict": False,
        "description": ("Joels AI-abonnemang: hur många tokens han använt (i dag, denna månad och förra månaden; "
                        "i miljarder, cacheläsningar inräknade) och hur många procent av 5-timmars- och "
                        "veckogränsen som är använda för Claude 1, Claude 2 och Codex, och när de nollställs. "
                        "Använd det när han frågar om AI-användning, tokens, Claude- eller Codex-gränser eller hur "
                        "mycket han har kvar. Panelen öppnar AI-sidan när du pratat klart, så svara kort: en eller "
                        "två meningar med månadens miljarder och veckoprocenten, och nämn en gräns bara om den är "
                        "över 70 procent. Säg \"miljarder\", inte \"B\"."),
        "parameters": {"type": "object", "properties": {}}}


def summary(tokens, limits, now=None):
    """What the model needs, from the sensors' states (dicts as /api/states returns them)."""
    a = tokens["attributes"]
    out = {"tokens_miljarder": {"i_dag": a.get("today_b"), "denna_manad": a.get("month_b"),
                                "forra_manaden": a.get("prev_month_b")},
           "tokens_i_dag_miljoner": a.get("today_m"),
           "obs": "Nästan allt är cacheläsningar; skriven text (output) är en liten del.",
           "konton": {}}
    for key, state in limits.items():
        if not state:
            continue
        la = state.get("attributes") or {}
        week = state.get("state")
        out["konton"][NAMES[key]] = {
            "vecka_procent": None if week in (None, "unknown", "unavailable") else float(week),
            "vecka_nollstalls": la.get("seven_day_resets"),
            "fem_timmar_procent": la.get("five_hour_pct"),
            "fem_timmar_nollstalls": la.get("five_hour_resets"),
            "manad_miljarder": la.get("month_b"),
        }
    updated = tokens.get("last_updated")
    if updated:
        out["uppdaterad"] = updated
    return out


def open_page(now=None):
    hass.request(f"/api/states/{OPEN}", {"state": f"ai@{int(now or time.time())}",
                                          "attributes": {"friendly_name": "Jarvis: öppna sida på panelen"}})


def usage():
    """(is_error, JSON text). Opens the panel's AI page as a side effect; a failed open does not fail the answer."""
    try:
        tokens = hass.get_json(f"/api/states/{TOKENS}")
        limits = {}
        for key, entity in ENTITIES.items():
            try:
                limits[key] = hass.get_json(f"/api/states/{entity}")
            except urllib.error.HTTPError:
                limits[key] = None
        result = summary(tokens, limits)
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return True, "AI-användningen finns inte i Home Assistant just nu (ai-usage.py har inte körts sedan HA startade om)."
        return True, f"AI-användningen gick inte att läsa just nu: {e}"
    except (OSError, urllib.error.URLError, ValueError, KeyError, TypeError) as e:
        return True, f"AI-användningen gick inte att läsa just nu: {e}"
    try:
        open_page()
    except (OSError, urllib.error.URLError) as e:
        print(f"ai_usage: could not open the panel's AI page: {e}", file=sys.stderr)
    return False, json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    error, text = usage()
    print(text)
    sys.exit(1 if error else 0)
