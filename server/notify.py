#!/usr/bin/env python3
"""How Jarvis reaches Joel without being asked: on the panel's speaker or on his phone. Standard library only.

  say(text, kind)        one of three kinds:
    reminder  what he asked for (server/reminders.py)    the panel when he is home, otherwise the phone; any hour
    urgent    smoke or a leak                              the panel at any hour (Home Assistant pushes those to
                                                           the phone itself, so Jarvis does not push them again)
    notice    the door, the air, the laundry, the calendar, the net
                                                           the panel only, when he is home and it is not night
  push(text, title, url) a notification on his iPhone through Home Assistant's notify.<JARVIS_PHONE>

The panel speaks through Home Assistant's assist_satellite.announce on JARVIS_PANEL_SATELLITE (its entity once
Home Assistant has adopted the panel, for example assist_satellite.hallpanel_assist_satellite), with the chime
first. Home Assistant reads the text with the pipeline's voice, Jarvis Mattias. Until that is set, a reminder
goes to the phone and a notice is only logged. Whether he is home is person.joel (JARVIS_PERSON); unknown counts
as home. Night is the panel's own night mode: its two time entities "Natt börjar" and "Natt slutar" in Home
Assistant (JARVIS_NIGHT_START, JARVIS_NIGHT_END), which Joel sets there; 22-6 when they cannot be read, and
JARVIS_QUIET_HOURS ("22-6", "22:30-6:15") overrides them.

Every announcement is also kept in the chat history as Jarvis's, so he can ask what Jarvis said.

The tool till_telefonen lets the agent send a longer answer or a link to the phone. Like the memory's tools it
runs only when what Joel just said asks for it, or when he says yes to Jarvis's offer to send it. With
from_calendar it sends the event the agent adds in the same step or added earlier in the question (agent_core
hands it over as "event"): its
title, time, place, description and link, so a plan is written once, in the calendar, and not twice.
"""
import os
import re
import sys
import threading
from urllib.error import URLError
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent / "agents"))
import hass  # noqa: E402

TZ = ZoneInfo("Europe/Stockholm")
# Words one of which Joel has to say for till_telefonen to run, or a yes to Jarvis's offer.
PHONE_WORDS = re.compile(r"telefon|mobil|iphone|luren|skicka|sms|notis|\bphone\b|\bnotify\b", re.I)
YES = re.compile(r"^\W*(ja|japp|jo|gärna|absolut|okej|ok|visst|gör det|skicka|ja tack|ja gärna|"
                 r"yes|yep|yeah|sure|please do|go ahead)\b", re.I)
MAX_PUSH = 1500


class DeliveryUnconfirmed(OSError):
    """A request may already have acted; do not retry it or send through another destination."""


def not_submitted(error):
    """Only failures that prove HA did not accept a service request are safe to retry."""
    return isinstance(error, hass.Refused) or (isinstance(error, URLError)
                                              and isinstance(error.reason, ConnectionRefusedError))

PHONE_TOOL = {"type": "function", "name": "till_telefonen", "strict": False,
              "description": ("Skickar en notis till Joels telefon: ett längre svar, en lista, ett recept eller en "
                              "länk som är bättre att läsa än att höra. Bara när Joel ber om det eller säger ja när "
                              "du har erbjudit det."),
              "parameters": {"type": "object", "properties": {
                  "from_calendar": {"type": "boolean", "description": (
                      "Skicka händelsen du lägger in med add_event, i samma steg eller tidigare: titel, tid, plats, beskrivning "
                      "och länk. Använd det i stället för text när planen redan står i kalenderhändelsen.")},
                  "text": {"type": "string", "description": f"Det som ska stå i notisen, högst {MAX_PUSH} tecken"},
                  "title": {"type": "string", "description": "En kort rubrik"},
                  "url": {"type": "string", "description": "En länk som öppnas när han trycker på notisen"}}}}


DAYS = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
MONTHS = ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober",
          "november", "december"]


def when(start, end=None):
    """ "lördag 24 oktober 11:18–14:40" from add_event's "YYYY-MM-DD HH:MM" (or all-day dates); as given if odd."""
    def parse(value):
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                return datetime.strptime(str(value).strip()[:16], fmt), fmt
            except ValueError:
                pass
        return None, None
    first, kind = parse(start)
    if first is None:
        return " till ".join(str(x) for x in (start, end) if x)
    text = f"{DAYS[first.weekday()]} {first.day} {MONTHS[first.month - 1]}"
    if kind == "%Y-%m-%d %H:%M":
        text += f" {first:%H:%M}"
    last, _ = parse(end) if end else (None, None)
    if last and kind == "%Y-%m-%d %H:%M":
        text += f"–{last:%H:%M}" if last.date() == first.date() else f" till {when(end)}"
    return text


NIGHT_ENTITIES = (os.environ.get("JARVIS_NIGHT_START", "time.hallpanel_natt_borjar"),
                  os.environ.get("JARVIS_NIGHT_END", "time.hallpanel_natt_slutar"))
DEFAULT_NIGHT = "22-6"


def minutes(text):
    """Minutes after midnight from "22", "22:30" or Home Assistant's "22:30:00"."""
    hour, _, rest = str(text).strip().partition(":")
    return int(hour) * 60 + int(rest.partition(":")[0] or 0)


def quiet_hours(spec=None):
    """(start, end) of the night in minutes after midnight: from spec or JARVIS_QUIET_HOURS ("22-6"), else
    from the panel's two time entities in Home Assistant, else 22-6."""
    if spec := spec or os.environ.get("JARVIS_QUIET_HOURS"):
        start, _, end = spec.partition("-")
        return minutes(start), minutes(end)
    try:
        start, end = ((hass.get_json(f"/api/states/{e}", timeout=3) or {}).get("state") for e in NIGHT_ENTITIES)
        return minutes(start), minutes(end)
    except (OSError, ValueError, TypeError):    # not adopted yet, unavailable, or Home Assistant is down
        return quiet_hours(DEFAULT_NIGHT)


def is_night(now=None, spec=None):
    now = now or datetime.now(TZ)
    m, (start, end) = now.hour * 60 + now.minute, quiet_hours(spec)
    return m >= start or m < end if start > end else start <= m < end


class Notifier:
    def __init__(self, history=None):
        self.panel = os.environ.get("JARVIS_PANEL_SATELLITE")
        self.phone = os.environ.get("JARVIS_PHONE")              # e.g. mobile_app_demo_phone
        self.person = os.environ.get("JARVIS_PERSON", "person.joel")
        self.history = history
        self.lock = threading.Lock()        # one announcement at a time on the panel
        self.said, self.last_reply = "", ""  # the question being answered and Jarvis's reply before it
        self.log = []                       # (kind, where, text) of the last announcements, for tests

    # -- Home Assistant (hass.py) -------------------------------------------------------------------------
    def home(self):
        """True unless Home Assistant says Joel is away."""
        try:
            return (hass.get_json(f"/api/states/{self.person}", timeout=3) or {}).get("state") != "not_home"
        except (OSError, ValueError):
            return True

    def push(self, text, title=None, url=None):
        """(is_error, text) after a notification to the phone."""
        if not self.phone:
            return True, "Jarvis vet inte vilken telefon notisen ska till."
        data = {"message": text[:MAX_PUSH], "title": title or "Jarvis"}
        if url:
            data["data"] = {"url": url}
        try:
            hass.call_service("notify", self.phone, data)
        except (OSError, ValueError) as e:
            if not not_submitted(e):
                raise DeliveryUnconfirmed("Phone delivery could not be confirmed") from e
            return True, f"Notisen gick inte iväg ({type(e).__name__})."
        return False, "Notisen är skickad till telefonen."

    def announce(self, text):
        """Speak on the panel; (is_error, text)."""
        if not self.panel:
            return True, "Panelen är inte ansluten än."
        try:
            with self.lock:                 # Home Assistant answers once the announcement has played
                hass.call_service("assist_satellite", "announce",
                                  {"entity_id": self.panel, "message": text, "preannounce": True}, timeout=60)
        except (OSError, ValueError) as e:
            if not not_submitted(e):
                raise DeliveryUnconfirmed("Panel delivery could not be confirmed") from e
            return True, f"Panelen svarade inte ({type(e).__name__})."
        return False, ""

    # -- what goes where ---------------------------------------------------------------------------------
    def say(self, text, kind="notice", now=None):
        """Tell Joel something by kind (see the top of the file). Returns where it went: panel, phone or None."""
        where = None
        home = self.home()
        if kind == "urgent":
            where = "panel" if not self.announce(text)[0] else None
        elif kind == "reminder":
            if home and not self.announce(text)[0]:
                where = "panel"
            elif not self.push(text, "Påminnelse")[0]:
                where = "phone"
        elif home and not is_night(now):
            where = "panel" if not self.announce(text)[0] else None
        self.log.append((kind, where, text))
        print(f"notify: {kind} -> {where or 'nowhere'}: {text}", file=sys.stderr)
        if where and self.history is not None:
            self.history.add("notice", "jarvis", text, meta={"brain": f"notice:{kind}"})
        return where

    # -- the agent's tool ---------------------------------------------------------------------------------
    def tools(self):
        return [(PHONE_TOOL, self.phone_tool)] if self.phone else []

    def phone_tool(self, args):
        offered = bool(re.search(r"telefon|mobil|iphone|\bphone\b", self.last_reply, re.I)) and YES.search(self.said)
        if not (PHONE_WORDS.search(self.said) or offered):
            return True, "Inte skickad: Joel bad inte om det nu. Skicka bara när han ber om det."
        if args.get("from_calendar"):
            event = args.get("event")
            if not isinstance(event, dict):
                return True, "Inte skickad: ingen kalenderhändelse lades till. Skicka planen med text i stället."
            args = {"title": event.get("title"), "url": args.get("url") or event.get("url"),
                    "text": "\n".join(str(x) for x in (when(event.get("start"), event.get("end")),
                                                        event.get("location"), event.get("notes")) if x)}
        text = str(args.get("text") or "").strip()[:MAX_PUSH]
        if not text:
            return True, "Inte skickad: notisen var tom."
        url = str(args.get("url") or "").strip() or None
        if url and not url.startswith(("https://", "http://")):
            return True, "Inte skickad: länken ska börja med https://."
        try:
            return self.push(text, str(args.get("title") or "").strip() or None, url)
        except DeliveryUnconfirmed:
            return True, "Leveransen kunde inte bekräftas. Notisen kan ha skickats; skicka inte samma igen automatiskt."
