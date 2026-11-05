#!/usr/bin/env python3
"""Jarvis's tools for the two morning routines in Home Assistant (Joel, 2026-11-02): "sätt morgon-TV på
torsdag 06:30 med regnljud", "stäng av morgon-TV:n på lördag", "väck mig på fredag 07:00 med kontorsmorgon",
"lägg in en extra väckning i morgon 06:00, säg att vi ska till Demoparken", "ta bort extraväckningen".

Both routines keep their settings in native HA helpers, edited from their dashboards too; these tools read and
change the same helpers through HA's REST API (hass.py), so the dashboards always show what Jarvis set:
  Morgon-TV   package /config/morgon_tv.yaml on Jarvis: per weekday input_boolean.morgon_tv_<dag>, a time
              input_datetime.morgon_tv_tid_<dag> and a video input_text.morgon_tv_<dag> (a YouTube link or search
              words; empty plays input_text.morgon_tv_standard). Cron ~/bin/tv-morning-schedule.sh reads them every
              minute; log ~/.local/state/tv-morning.log.
  Väckning    package ha/jarvis_wakeup.yaml (docs/wakeups.md): per weekday input_boolean.jarvis_vackning_<dag>,
              its time and content (Vanlig morgon / Kontorsmorgon), and one extra wakeup (jarvis_vackning_engang:
              date and time, content, and the free wish input_text.jarvis_vackning_engang_text for Egen väckning).
              The controller in ha/custom_components/jarvis_agent/wakeup.py reschedules at every change and logs
              each one to /config/jarvis_logs/vackning.log with the HA user.
Days are weekly rows: changing Thursday changes every Thursday. There is one extra wakeup slot; a new one
replaces it, and the result says what it replaced. Every change is logged to the server's journal
(`mornings:`), and the result is read back from HA after the change, so Jarvis only confirms what HA holds.
The two change tools run only when Joel's words in the question asked for the change (spoken_change.py: a
direct request, or a yes or a day/time answer to Jarvis's question about it; a read is not gated), and they
answer instead without writing. The agent hands each call the question's Turn.

Free read on Jarvis (no model, nothing changes):
  cd ~/hallpanel-live/server/agents && ~/hallpanel-live/venv/bin/python mornings.py
"""
import json
import re
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import hass
import spoken_change

TZ = ZoneInfo("Europe/Stockholm")
DAYS = ("måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag")
TV_DAY = dict(zip(DAYS, ("man", "tis", "ons", "tor", "fre", "lor", "son")))
WAKE_DAY = dict(zip(DAYS, ("mandag", "tisdag", "onsdag", "torsdag", "fredag", "lordag", "sondag")))
CONTENTS = ("Vanlig morgon", "Kontorsmorgon")
CUSTOM = "Egen väckning"
TEXT_MAX = 255
TV_VOLUME_MAX = 30          # input_number.morgon_tv_volym's max
CLOCK = re.compile(r"^([01]?\d|2[0-3])[:.]([0-5]\d)$")
W = "jarvis_vackning_"

DAYS_ARG = {"type": "array", "items": {"type": "string", "enum": list(DAYS)},
            "description": "Veckodagarna det gäller, t.ex. [\"lördag\", \"söndag\"]; vardagar är måndag till fredag"}
TIME_ARG = {"type": "string", "description": "Klockslag HH:MM, t.ex. 06:30"}

TV_TOOL = {"type": "function", "name": "morgon_tv", "strict": False,
           "description": ("Morgon-TV:n: vilken YouTube-video Apple TV:n spelar på morgonen, per veckodag, med tid och "
                           "om dagen är på, plus standardvideon (spelas en påslagen dag utan egen video), volymen och "
                           "hur senaste morgonen gick. Läser bara."),
           "parameters": {"type": "object", "properties": {}}}
TV_CHANGE_TOOL = {"type": "function", "name": "morgon_tv_andra", "strict": False,
                  "description": ("Ändrar morgon-TV:n i Home Assistant. action \"sätt\" slår på dagarna och sätter tid "
                                  "och/eller video; det som inte anges behålls. action \"av\" stänger av dagarna (videon "
                                  "och tiden sparas). Dagarna gäller varje vecka: säger han \"i morgon\" eller ett datum, "
                                  "ändra den veckodagen och säg att det gäller varje sådan veckodag. video är en "
                                  "YouTube-länk eller sökord (översta träffen spelas), till exempel \"regnljud\"; "
                                  "\"standard\" tar bort dagens egna video så standardvideon spelas. Joel måste just nu "
                                  "ha bett om ändringen med sina egna ord, eller sagt ja när du erbjudit den, "
                                  "annars vägrar verktyget. Svara kort med "
                                  "dag, tid och video, till exempel \"Klart, torsdag 06:30 med regnljud.\""),
                  "parameters": {"type": "object", "required": ["action", "days"], "properties": {
                      "action": {"type": "string", "enum": ["sätt", "av"]},
                      "days": DAYS_ARG, "time": TIME_ARG,
                      "video": {"type": "string", "description": "YouTube-länk, sökord, eller \"standard\""},
                      "volume": {"type": "integer", "minimum": 0, "maximum": TV_VOLUME_MAX,
                                 "description": "TV:ns volym på morgonen, gemensam för alla dagar; bara om han ber"}}}}
WAKE_TOOL = {"type": "function", "name": "vackningar", "strict": False,
             "description": ("Jarvis väckningar på panelen: per veckodag om den är på, tid och innehåll (Vanlig morgon "
                             "eller Kontorsmorgon), den extra väckningen (datum, tid, innehåll och hans egna "
                             "instruktion), och nästa väckning. Läser bara."),
             "parameters": {"type": "object", "properties": {}}}
WAKE_CHANGE_TOOL = {"type": "function", "name": "vackning_andra", "strict": False,
                    "description": ("Ändrar Jarvis väckningar i Home Assistant. action \"veckodag\" slår på dagarna "
                                    "(varje vecka) och sätter tid och/eller content; det som inte anges behålls. "
                                    "\"veckodag_av\" stänger av dagarna. \"extra\" lägger in den extra väckningen en "
                                    "gång vid at (datum och tid); det finns bara en, så en ny ersätter den gamla. "
                                    "instruction är hans egen instruktion för den, med hans ord, till exempel \"väck mig "
                                    "och Alex, vi ska till Demoparken\": Jarvis skriver då hela väckningen efter den. Utan "
                                    "instruction blir det content (standard Vanlig morgon). Att ändra bara tid eller "
                                    "instruktion för en befintlig extra väckning: ange bara det som ändras. "
                                    "\"extra_av\" tar bort den extra väckningen. Joel måste just nu ha bett om "
                                    "ändringen med sina egna ord, eller sagt ja när du erbjudit den, annars "
                                    "vägrar verktyget. Svara kort med dag och tid, till exempel "
                                    "\"Klart, fredag 07:00.\" tidigare visar den extra väckningen före ändringen: var "
                                    "det en annan än den han ändrade, säg att den nya ersatte den."),
                    "parameters": {"type": "object", "required": ["action"], "properties": {
                        "action": {"type": "string", "enum": ["veckodag", "veckodag_av", "extra", "extra_av"]},
                        "days": DAYS_ARG, "time": TIME_ARG,
                        "content": {"type": "string", "enum": list(CONTENTS)},
                        "at": {"type": "string", "description": "Den extra väckningen: YYYY-MM-DD HH:MM, svensk tid"},
                        "instruction": {"type": "string",
                                        "description": f"Hans egen instruktion för den extra väckningen, högst {TEXT_MAX} tecken"}}}}


class Invalid(ValueError):
    """An argument the model can correct; its text goes back to the model."""


def _states():
    """{entity_id: state} for the helpers of both routines; unknown and unavailable read as ""."""
    found = {}
    for s in hass.get_json("/api/states", timeout=10) or []:
        e = s.get("entity_id", "")
        if "morgon_tv" in e or W in e or e == "sensor.jarvis_nasta_vackning":
            state = s.get("state")
            found[e] = "" if state in (None, "unknown", "unavailable") else str(state)
            if e == "sensor.jarvis_nasta_vackning":
                found[e + ".text"] = (s.get("attributes") or {}).get("text") or ""
                found[e + ".status"] = (s.get("attributes") or {}).get("status") or ""
    return found


def _clock(value):
    """"06:30" from "6:30", "06.30" or HA's "06:30:00"."""
    m = CLOCK.match(str(value or "").strip()[:5].rstrip(":")) or CLOCK.match(str(value or "").strip())
    if not m:
        raise Invalid(f"Ogiltig tid {value!r}: ange HH:MM, till exempel 06:30.")
    return f"{int(m.group(1)):02d}:{m.group(2)}"


def _days(days):
    if isinstance(days, str):
        days = [days]
    picked = [str(d).strip().lower() for d in days or []]
    unknown = [d for d in picked if d not in DAYS]
    if not picked or unknown:
        raise Invalid(f"Ange veckodagar bland {', '.join(DAYS)}" + (f"; okänd: {', '.join(unknown)}" if unknown else "."))
    return sorted(set(picked), key=DAYS.index)


def _call(domain, service, entity, **data):
    hass.call_service(domain, service, {"entity_id": entity, **data})


def _failed(e):
    print(f"mornings: Home Assistant call failed: {e}", file=sys.stderr)
    return True, f"Home Assistant svarade inte: {e}"


# -- Morgon-TV ----------------------------------------------------------------------------------
def _tv_rows(states, days=DAYS):
    rows = {}
    for day in days:
        d = TV_DAY[day]
        own = states.get(f"input_text.morgon_tv_{d}", "")
        rows[day] = {"på": states.get(f"input_boolean.morgon_tv_{d}") == "on",
                     "tid": states.get(f"input_datetime.morgon_tv_tid_{d}", "")[:5],
                     "video": (states.get(f"input_text.morgon_tv_{d}_titel") or own) if own else "standardvideon"}
    return rows


def tv_read(**_):
    try:
        states = _states()
    except OSError as e:
        return _failed(e)
    volume = states.get("input_number.morgon_tv_volym", "")
    result = {"dagar": _tv_rows(states),
              "standardvideo": states.get("input_text.morgon_tv_standard_titel") or states.get("input_text.morgon_tv_standard", ""),
              "volym": int(float(volume)) if volume else None,
              "senast": states.get("input_text.morgon_tv_senast", "")}
    return False, json.dumps(result, ensure_ascii=False)


def tv_change(action=None, days=None, time=None, video=None, volume=None, spoken=None, **_):
    """spoken: the question's spoken_change.Turn; the change runs only when Joel asked for it."""
    if not spoken_change.allowed(spoken, "morning_tv", "morgon_tv_andra"):
        return True, ("Inte ändrat ännu: Joels ord bad inte tydligt om ändringen. Fråga honom \"Ska jag ändra "
                      "morgon-TV ...?\" och ändra när han svarar ja.")
    try:
        picked = _days(days)
        if action not in ("sätt", "av"):
            raise Invalid("action är \"sätt\" eller \"av\".")
        at = _clock(time) if time not in (None, "") else None
        if video is not None:
            video = " ".join(str(video).split())
            if len(video) > TEXT_MAX:
                raise Invalid(f"Videon får vara högst {TEXT_MAX} tecken.")
        if volume is not None:
            volume = int(volume)
            if not 0 <= volume <= TV_VOLUME_MAX:
                raise Invalid(f"Volymen är 0 till {TV_VOLUME_MAX}.")
    except (Invalid, TypeError, ValueError) as e:
        return True, str(e)
    try:
        for day in picked:
            d = TV_DAY[day]
            if action == "av":
                _call("input_boolean", "turn_off", f"input_boolean.morgon_tv_{d}")
                continue
            if video:
                _call("input_text", "set_value", f"input_text.morgon_tv_{d}",
                      value="" if video.lower() == "standard" else video)
            if at:
                _call("input_datetime", "set_datetime", f"input_datetime.morgon_tv_tid_{d}", time=at + ":00")
            _call("input_boolean", "turn_on", f"input_boolean.morgon_tv_{d}")
        if volume is not None:
            _call("input_number", "set_value", "input_number.morgon_tv_volym", value=volume)
        states = _states()
    except OSError as e:
        return _failed(e)
    rows = _tv_rows(states, picked)
    # HA's automation fills in the new title a moment later (queued, via YouTube's oEmbed); until then the
    # title helper still holds the old video's, so the row shows what was asked for.
    if video and video.lower() != "standard":
        for row in rows.values():
            row["video"] = video
    print(f"mornings: morgon-TV {action} {','.join(picked)} time={at} video={video!r} volume={volume} -> "
          f"{json.dumps(rows, ensure_ascii=False)}", file=sys.stderr)
    return False, json.dumps({"ändrat": action, "dagar": rows}, ensure_ascii=False)


# -- Väckning -----------------------------------------------------------------------------------
def _extra(states):
    when = states.get(f"input_datetime.{W}engang", "")
    content = states.get(f"input_select.{W}engang_typ", "")
    wish = " ".join(states.get(f"input_text.{W}engang_text", "").split())
    row = {"på": states.get(f"input_boolean.{W}engang") == "on", "när": when[:16], "innehåll": content}
    if content == CUSTOM:
        row["instruktion"] = wish
    return row


def _wake_rows(states, days=DAYS):
    return {day: {"på": states.get(f"input_boolean.{W}{WAKE_DAY[day]}") == "on",
                  "tid": states.get(f"input_datetime.{W}{WAKE_DAY[day]}_tid", "")[:5],
                  "innehåll": states.get(f"input_select.{W}{WAKE_DAY[day]}_typ", "")} for day in days}


def wake_read(**_):
    try:
        states = _states()
    except OSError as e:
        return _failed(e)
    result = {"dagar": _wake_rows(states), "extra": _extra(states),
              "nästa": states.get("sensor.jarvis_nasta_vackning.text") or states.get("sensor.jarvis_nasta_vackning.status", ""),
              "radio_efter": states.get(f"input_select.{W}radio", "")}
    return False, json.dumps(result, ensure_ascii=False)


def _moment(value, now):
    try:
        when = datetime.strptime(str(value).strip().replace("T", " ")[:16], "%Y-%m-%d %H:%M").replace(tzinfo=TZ)
    except ValueError:
        raise Invalid(f"Ogiltig tidpunkt {value!r}: ange YYYY-MM-DD HH:MM.") from None
    if when <= now:
        raise Invalid(f"{when:%Y-%m-%d %H:%M} har redan varit.")
    return when


def wake_change(action=None, days=None, time=None, content=None, at=None, instruction=None, _now=None,
                spoken=None, **_):
    """spoken: the question's spoken_change.Turn; the change runs only when Joel asked for it."""
    if not spoken_change.allowed(spoken, "wake", "vackning_andra"):
        return True, ("Inte ändrat ännu: Joels ord bad inte tydligt om ändringen. Fråga honom \"Ska jag ändra "
                      "väckningen ...?\" och ändra när han svarar ja.")
    now = (_now or datetime.now(TZ)).astimezone(TZ)
    try:
        if action not in ("veckodag", "veckodag_av", "extra", "extra_av"):
            raise Invalid("action är veckodag, veckodag_av, extra eller extra_av.")
        if content not in (None, "") and content not in CONTENTS:
            raise Invalid(f"content är {' eller '.join(CONTENTS)}.")
        picked = _days(days) if action.startswith("veckodag") else []
        clock = _clock(time) if action == "veckodag" and time not in (None, "") else None
        when = _moment(at, now) if action == "extra" and at not in (None, "") else None
        if instruction is not None:
            instruction = " ".join(str(instruction).split())
            if len(instruction) > TEXT_MAX:
                raise Invalid(f"Instruktionen får vara högst {TEXT_MAX} tecken; korta den.")
    except Invalid as e:
        return True, str(e)
    try:
        if action == "veckodag_av":
            for day in picked:
                _call("input_boolean", "turn_off", f"input_boolean.{W}{WAKE_DAY[day]}")
        elif action == "veckodag":
            for day in picked:
                d = WAKE_DAY[day]
                if clock:
                    _call("input_datetime", "set_datetime", f"input_datetime.{W}{d}_tid", time=clock + ":00")
                if content:
                    _call("input_select", "select_option", f"input_select.{W}{d}_typ", option=content)
                _call("input_boolean", "turn_on", f"input_boolean.{W}{d}")
        elif action == "extra_av":
            before = _extra(_states())
            _call("input_boolean", "turn_off", f"input_boolean.{W}engang")
        else:
            before = _extra(_states())
            # Editing the extra wakeup that is on keeps what is not given; a new one needs its moment.
            if when is None and not before["på"]:
                return True, "Ange när den extra väckningen ska vara (at, YYYY-MM-DD HH:MM)."
            if when is None:
                try:
                    _moment(before["när"], now)
                except Invalid:
                    return True, "Den extra väckningen har redan varit; ange en ny tid (at, YYYY-MM-DD HH:MM)."
            if when is not None:
                _call("input_datetime", "set_datetime", f"input_datetime.{W}engang",
                      datetime=when.strftime("%Y-%m-%d %H:%M:00"))
            if instruction:
                _call("input_text", "set_value", f"input_text.{W}engang_text", value=instruction)
                _call("input_select", "select_option", f"input_select.{W}engang_typ", option=CUSTOM)
            elif content:
                _call("input_select", "select_option", f"input_select.{W}engang_typ", option=content)
            elif not before["på"]:
                # A new extra wakeup without an instruction is a plain morning, not the last one's Egen väckning.
                _call("input_select", "select_option", f"input_select.{W}engang_typ", option=CONTENTS[0])
            _call("input_boolean", "turn_on", f"input_boolean.{W}engang")
        states = _states()
    except OSError as e:
        return _failed(e)
    result = {"ändrat": action}
    if picked:
        result["dagar"] = _wake_rows(states, picked)
    else:
        result["extra"] = _extra(states)
        result["tidigare"] = before if before["på"] else "ingen extra väckning var på"
    # No "nästa" here: the controller reschedules after the change, so the sensor read at once may be the old one.
    print(f"mornings: väckning {action} days={','.join(picked)} time={clock} content={content} at={at} "
          f"instruction={'yes' if instruction else 'no'} -> {json.dumps(result, ensure_ascii=False)}", file=sys.stderr)
    return False, json.dumps(result, ensure_ascii=False)


if __name__ == "__main__":
    for read in (tv_read, wake_read):
        error, text = read()
        print(json.dumps(json.loads(text), ensure_ascii=False, indent=1) if not error else text)
