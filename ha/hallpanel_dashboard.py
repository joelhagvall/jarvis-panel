#!/usr/bin/env python3
"""The Home Assistant dashboard "Hallpanel" (/jarvis-hallpanel): the background, the link, the panel's own
settings (night times, Nattljus, Skärmljus, Mikrofon) and Rensa in one place. Made 2026-10-22; run it again
after changing it. It runs inside the Home Assistant container, with the token as the first line of stdin:

  ssh user@jarvis.local '(cat ~/.config/homeassistant/token; cat) | docker exec -i homeassistant python3 -c
  "import sys; TOKEN = sys.stdin.readline().strip(); exec(sys.stdin.read())"' < ha/hallpanel_dashboard.py

(one line; it is split here only to fit).
The panel's entities show as unavailable until Home Assistant has adopted it (ids as ESPHome names them).
"""
import asyncio
import json
import time

import aiohttp

URL_PATH = "jarvis-hallpanel"
MEDIA = "/media-browser/browser/app%2Cmedia-source%3A%2F%2Fmedia_source%2Fhallpanel%2F."
CONFIG = {"views": [{"title": "Hallpanel", "path": "hallpanel", "cards": [
    {"type": "entities", "title": "Bakgrund", "entities": [
        {"entity": "select.jarvis_bakgrund", "name": "Bakgrund"},
        {"entity": "text.jarvis_bakgrund_lank", "name": "Länk till en bild"}]},
    {"type": "markdown", "content":
        f"**Egen bild:** ladda upp under [Media → hallpanel-bilder]({MEDIA}). Den blir ett val i listan ovan "
        "inom 10 sekunder, med filnamnet som namn. jpg, png eller webp, inte iPhonens HEIC.\n\n"
        "**Länk:** klistra in bildens adress ovan, så blir den *Egen bild* efter cirka 8 sekunder."},
    {"type": "entities", "title": "Panelen", "entities": [
        {"entity": "time.hallpanel_natt_borjar", "name": "Natt börjar"},
        {"entity": "time.hallpanel_natt_slutar", "name": "Natt slutar"},
        {"entity": "number.hallpanel_nattljus", "name": "Nattljus"},
        {"entity": "light.hallpanel_skarmljus", "name": "Skärmljus dagtid"},
        {"entity": "switch.hallpanel_mikrofon", "name": "Mikrofon"}],
     "footer": None},
    {"type": "entities", "title": "Jarvis", "entities": [
        {"entity": "sensor.jarvis_hemmet", "name": "Hemma"},
        {"type": "button", "name": "Glöm samtalet (Rensa)", "icon": "mdi:broom", "action_name": "Rensa",
         "tap_action": {"action": "perform-action", "perform_action": "jarvis_agent.clear"}}]},
    # The moon (Joel, 2026-10-28; ha/custom_components/jarvis_agent/moon.py): phase, light, the next full and new moon.
    {"type": "entities", "title": "Månen", "entities": [
        {"entity": "sensor.jarvis_manen", "name": "Fas"},
        {"type": "attribute", "entity": "sensor.jarvis_manen", "attribute": "illumination", "name": "Belyst",
         "suffix": "%", "icon": "mdi:brightness-6"},
        {"entity": "sensor.jarvis_nasta_fullmane", "name": "Nästa fullmåne", "format": "datetime"},
        {"entity": "sensor.jarvis_nasta_nymane", "name": "Nästa nymåne", "format": "datetime"}]},
]}]}
CONFIG["views"][0]["cards"][2].pop("footer")


def lost_cards(current, new):
    """Cards (by title, or type and first entity) on the live dashboard that the new config does not have."""
    def key(card):
        return card.get("title") or f"{card.get('type')}:{(card.get('entities') or [{}])[0]}"
    have = {key(c) for v in new.get("views", []) for c in v.get("cards", [])}
    return sorted({key(c) for v in current.get("views", []) for c in v.get("cards", [])} - have)


async def main(token):
    async with aiohttp.ClientSession() as s, s.ws_connect("http://127.0.0.1:8123/api/websocket") as ws:
        n = 0

        async def call(msg):
            nonlocal n
            n += 1
            await ws.send_json({"id": n, **msg})
            while True:
                r = await ws.receive_json()
                if r.get("id") == n:
                    if not r.get("success", True):
                        raise RuntimeError(json.dumps(r.get("error")))
                    return r.get("result")

        await ws.receive_json()
        await ws.send_json({"type": "auth", "access_token": token})
        assert (await ws.receive_json())["type"] == "auth_ok"
        dashboards = await call({"type": "lovelace/dashboards/list"})
        if not any(d["url_path"] == URL_PATH for d in dashboards):
            await call({"type": "lovelace/dashboards/create", "url_path": URL_PATH, "title": "Hallpanel",
                        "icon": "mdi:tablet-dashboard", "show_in_sidebar": True, "require_admin": False,
                        "mode": "storage"})
            print("made the dashboard")
        # The script replaces the whole dashboard: keep the old one, and stop before a card added by hand is lost.
        try:
            current = await call({"type": "lovelace/config", "url_path": URL_PATH})
        except RuntimeError:
            current = None
        if current:
            backup = f"/config/.storage/lovelace-hallpanel-backup-{time.strftime('%Y%m%d-%H%M%S')}.json"
            with open(backup, "w") as f:
                json.dump(current, f, ensure_ascii=False, indent=1)
            print(f"kept the old dashboard: {backup}")
            lost = lost_cards(current, CONFIG)
            if lost:
                raise SystemExit(f"not saved: these cards are not in the script and would be lost: {lost}; add them to "
                                 "CONFIG first (the old dashboard is in the backup)")
        await call({"type": "lovelace/config/save", "url_path": URL_PATH, "config": CONFIG})
        print(f"saved: http://jarvis.local:8123/{URL_PATH}/hallpanel")


if __name__ == "__main__" or "TOKEN" in globals():
    asyncio.run(main(TOKEN))
