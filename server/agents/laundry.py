#!/usr/bin/env python3
"""Jarvis's laundry tools (Joel, 2026-11-01): "vilka tvättider har jag?", "finns det lediga tider på
torsdag?" and "boka tvätt fredag 18-21".

Both run ~/tvatt-booker/tvatt_jarvis.py (repo github.com/joelhagvall/tvatt-booker), which logs in to the
building's booking site (cshub.epr-apps.com, the housing association) with the booker's own .env and prints JSON. The
script needs `requests`, so it runs under the system python3, not the server's venv. tvatt reads only;
tvatt_boka clicks a colour, which books at once, and is never started before the model's answer is complete.
It runs only when Joel's words in the question asked for the booking (spoken_change.py: a direct request, or
a yes or a day/time answer to Jarvis's question about it); the agent hands each call the question's Turn.
The script logs to stderr (here: the server's journal) and writes every booking to ~/tvatt-booker/book.log.

Direct answer (2026-11-03): a round whose calls are all tvatt_boka and that booked everything asked is answered
with done_reply ("Klart, onsdag 21-24."), the answer Joel asked for, instead of one more model round (2-3 s of a
~20 s booking question, measured 2026-11-01). The model still answers a failure, a partial booking, and a question
that also asks for something after the booking (a calendar entry, a push). The journal says "agent: direct reply".

Prefetch (2026-11-02): every read logs in and walks the site, 2.5-3 s, and the model wrote its tvatt call 1.6-4.4 s
into the question, so a laundry question waited on both in turn. When the question names the laundry
(agent_core.ToolAgent.ask calls prefetch), the next 14 days are read at once, beside the model; a read the model
asks for within them is cut from that result. A pass's colours (date and time) always read the site. A booking
first waits for a running prefetch (one login at a time) and then drops it, and a prefetch older than
PREFETCH_FRESH_S is never used. The journal says "laundry: prefetch ..." for each start, use and miss.

Free read on Jarvis (no model):
  cd ~/hallpanel-live/server/agents && ~/hallpanel-live/venv/bin/python laundry.py
"""
import json
import os
import re
import subprocess
import sys
import threading
import time as clock
from datetime import date as Date, datetime, timedelta
from pathlib import Path

import spoken_change

SCRIPT = Path(os.environ.get("JARVIS_TVATT_SCRIPT", "~/tvatt-booker/tvatt_jarvis.py")).expanduser()
PYTHON = os.environ.get("JARVIS_TVATT_PYTHON", "/usr/bin/python3")
READ_TIMEOUT = 45      # a read takes 2.5-5 s (measured 2026-11-01); the site is sometimes slow
BOOK_TIMEOUT = 60
PREFETCH_DAYS = 14     # two calendar pages: 2.3 s against 1.9 s for one (measured 2026-11-02)
PREFETCH_FRESH_S = 90  # the question's own lookup, and a follow-up right after it
TOPIC = re.compile(r"tvätt|tvät|laundry", re.IGNORECASE)   # "tvätttider", "tvättstugan", Whisper's "tvätider"
# Words for a step after the booking ("... och lägg in det i kalendern", "pinga mig"): the model answers then, since a
# direct answer ends the question before that step.
MORE_ASKED = re.compile(r"kalender|påmin|ping|telefon|mobil|skicka|sms|notis|meddela|väck|\bsen\b|sedan|också|även|"
                        r"calendar|remind|phone|send|text me|also", re.IGNORECASE)
WEEKDAYS = {"sv": ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"],
            "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]}
MONTHS = {"sv": ["januari", "februari", "mars", "april", "maj", "juni", "juli", "augusti", "september", "oktober",
                 "november", "december"],
          "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
                 "November", "December"]}

DATE = {"type": "string", "description": "Datum YYYY-MM-DD"}
HOUR = {"type": "integer", "description": "Passets starttimme: 0, 3, 6, 9, 12, 15, 18 eller 21 (6 = 06-09)"}
TOOL = {"type": "function", "name": "tvatt", "strict": False,
        "description": ("Tvättstugan (föreningen, plan 1): Joels bokningar och lediga pass. Sajten är långsam (3-5 s per anrop, "
                        "en bokning tar 15-20 s): säg därför alltid först en kort mening om vad du gör, i samma "
                        "steg som anropet, till exempel \"Genast. Jag kollar tvättstugan.\" (Joel, 2026-11-01: "
                        "ingen dötid). Passen är tre timmar "
                        "(00-03, 03-06 ... 21-24) och har fyra färger: Blå, Grön, Gul och Röd. Ett pass är ledigt när "
                        "minst en färg är ledig. Han får ha högst två bokningar samtidigt. Utan datum visas de närmaste "
                        "sju dagarna; kalendern går ca 30 dagar framåt. Med date och time visas också vilka färger "
                        "som är lediga i just det passet. Svara kort: hans bokningar, eller de lediga passen som "
                        "passar frågan, inte hela listan. Räkna aldrig upp lediga pass som han inte bad om, och "
                        "ber han dig boka säger du bara vad som bokades (Joel, 2026-11-01). Skriv passen som på bokningssidan, \"06-09\", aldrig med ord; rösten säger \"nollsex till nollnio\"."),
        "parameters": {"type": "object", "properties": {
            "from_date": {**DATE, "description": "Första dagen att visa, YYYY-MM-DD"},
            "to_date": {**DATE, "description": "Sista dagen att visa, YYYY-MM-DD"},
            "date": {**DATE, "description": "Ett pass vars lediga färger ska visas (med time)"},
            "time": HOUR}}}
BOOK_TOOL = {"type": "function", "name": "tvatt_boka", "strict": False,
             "description": ("Bokar tvättstugan direkt (ingen bekräftelse på sajten). Joel måste ha bett om bokningen "
                             "just nu, eller sagt ja när du erbjudit den; verktyget vägrar annars. "
                             "Använd bara när Joel ber dig "
                             "boka ett visst pass; säger han inte dag och tid, fråga först. count är antalet färger "
                             "(1 eller 2, standard 1); colours är önskade färger. Max två bokningar samtidigt: är det "
                             "fullt säger verktyget det. Svara med en kort mening om dag och pass, som \"Klart, onsdag 21-24.\" Färg, gränsen på två bokningar och tvättbokaren nämner du bara om han frågar eller om det inte gick att boka (Joel, 2026-11-01). Nämn aldrig en bokning som inte står i mina_bokningar, och räkna inte upp andra lediga pass."),
             "parameters": {"type": "object", "properties": {
                 "date": DATE, "time": HOUR,
                 "count": {"type": "integer", "description": "Antal färger att boka, 1 eller 2"},
                 "colours": {"type": "array", "items": {"type": "string", "enum": ["Blå", "Grön", "Gul", "Röd"]},
                             "description": "Önskade färger, om han nämner några"}},
                 "required": ["date", "time"]}}


def configured():
    return SCRIPT.is_file()


def _run(argv, timeout):
    """(is_error, JSON text) from the script; its stderr goes to the journal."""
    try:
        done = subprocess.run([PYTHON, str(SCRIPT), *argv], capture_output=True, text=True, timeout=timeout,
                              cwd=SCRIPT.parent)
    except subprocess.TimeoutExpired:
        print(f"laundry: {argv} timed out after {timeout}s", file=sys.stderr)
        return True, "Tvättstugans bokningssajt svarade inte i tid."
    except OSError as e:
        print(f"laundry: could not start {SCRIPT}: {e}", file=sys.stderr)
        return True, f"Tvättbokningen gick inte att starta: {e}"
    for line in done.stderr.strip().splitlines()[-5:]:
        print(f"laundry: {line}", file=sys.stderr)
    try:
        result = json.loads(done.stdout)
    except ValueError:
        return True, f"Tvättbokningen gav inget svar (kod {done.returncode})."
    if "fel" in result and not result.get("bokat"):
        return True, json.dumps(result, ensure_ascii=False)
    return False, json.dumps(result, ensure_ascii=False)


def _date(value):
    return datetime.strptime(str(value), "%Y-%m-%d").date().isoformat()


_prefetch_lock = threading.Lock()
_prefetch = None       # the last prefetch: started (monotonic), today, end, done (Event), result (is_error, text)


def prefetch(said, today=None):
    """Start reading the next PREFETCH_DAYS when Joel's question names the laundry. True when one started."""
    global _prefetch
    if not configured() or not TOPIC.search(said or ""):
        return False
    today = today or Date.today()
    with _prefetch_lock:
        job = _prefetch
        if job and job["today"] == today and clock.monotonic() - job["started"] < PREFETCH_FRESH_S \
                and not (job["done"].is_set() and job["result"][0]):
            return False                # one is running or fresh; a failed one may be tried again
        job = {"started": clock.monotonic(), "today": today, "end": today + timedelta(days=PREFETCH_DAYS - 1),
               "done": threading.Event(), "result": None}
        _prefetch = job

    def run():
        try:
            job["result"] = _run(["--from", today.isoformat(), "--to", job["end"].isoformat()], READ_TIMEOUT)
        finally:
            job["result"] = job["result"] or (True, "")
            job["done"].set()
        print(f"laundry: prefetch {'failed' if job['result'][0] else 'read'} in "
              f"{clock.monotonic() - job['started']:.1f}s", file=sys.stderr)

    print(f"laundry: prefetch started for {today}..{job['end']}", file=sys.stderr)
    threading.Thread(target=run, name="laundry-prefetch", daemon=True).start()
    return True


def _wait_prefetch():
    """Let a running prefetch finish (a booking logs in after it), bounded by its own timeout."""
    job = _prefetch
    if job:
        job["done"].wait(READ_TIMEOUT + 5)


def _drop_prefetch():
    global _prefetch
    with _prefetch_lock:
        _prefetch = None


def _from_prefetch(start, end, today):
    """The read start..end cut from a fresh prefetch, like tvatt_jarvis.py's own, or None."""
    job = _prefetch
    if not job or job["today"] != today or not today <= start <= end <= job["end"]:
        return None
    waited = clock.monotonic()
    if not job["done"].wait(READ_TIMEOUT + 5) or job is not _prefetch:
        return None
    age = clock.monotonic() - job["started"]
    is_error, text = job["result"]
    if is_error or age > PREFETCH_FRESH_S:
        print(f"laundry: prefetch not used ({'failed' if is_error else f'{age:.0f}s old'})", file=sys.stderr)
        return None
    try:
        result = json.loads(text)
        inside = lambda days: {d: v for d, v in days.items() if start.isoformat() <= d <= end.isoformat()}
        result["lediga_pass"] = inside(result.get("lediga_pass") or {})
        result["ej_bokningsbara"] = inside(result.get("ej_bokningsbara") or {})
        result.pop("visat_till", None)
    except (ValueError, AttributeError) as e:
        print(f"laundry: prefetch not used (unreadable: {e})", file=sys.stderr)
        return None
    print(f"laundry: prefetch used for {start}..{end} (waited {(clock.monotonic() - waited) * 1000:.0f} ms, "
          f"{age:.1f}s old)", file=sys.stderr)
    return False, json.dumps(result, ensure_ascii=False)


def read(from_date=None, to_date=None, date=None, time=None, today=None, **_):
    today = today or Date.today()
    try:
        argv = []
        if from_date:
            argv += ["--from", _date(from_date)]
        if to_date:
            argv += ["--to", _date(to_date)]
        if date and time is not None:
            argv += ["--date", _date(date), "--time", str(int(time))]
        elif date and not (from_date or to_date):
            argv += ["--from", _date(date), "--to", _date(date)]
        if not (date and time is not None):
            # tvatt_jarvis.main's range: from today (or later), seven days unless "to" says otherwise
            start = max(today, Date.fromisoformat(_date(from_date or date)) if (from_date or date) else today)
            end = max(start, Date.fromisoformat(_date(to_date)) if to_date else
                      Date.fromisoformat(_date(date)) if date and not from_date else start + timedelta(days=6))
            if (cut := _from_prefetch(start, end, today)) is not None:
                return cut
    except (TypeError, ValueError) as e:
        return True, f"Ogiltigt datum eller tid: {e}"
    return _run(argv, READ_TIMEOUT)


def book(date=None, time=None, count=None, colours=None, spoken=None, **_):
    """spoken: the question's spoken_change.Turn; tvatt_boka books a real pass only when Joel asked for it."""
    if not spoken_change.allowed(spoken, "laundry", "tvatt_boka"):
        return True, ("Inte bokad ännu: Joels ord bad inte tydligt om bokningen. Fråga honom \"Ska jag boka "
                      "tvätt <dag> <pass>?\" och boka när han svarar ja.")
    try:
        argv = ["--book", "--date", _date(date), "--time", str(int(time))]
        if count:
            argv += ["--count", str(int(count))]
        for colour in colours or []:
            argv += ["--colour", str(colour)]
    except (TypeError, ValueError) as e:
        return True, f"Ange datum (YYYY-MM-DD) och passets starttimme: {e}"
    _wait_prefetch()
    try:
        error, text = _run(argv, BOOK_TIMEOUT)
    finally:
        _drop_prefetch()                # the bookings changed: the next read asks the site
    if not error:
        # The booker's "obs" (it may then get one colour or none) was read out even when the description said
        # not to (Joel, 2026-11-01: "ingen kommer fatta"). A done booking reaches the model without it.
        result = json.loads(text)
        if result.pop("obs", None) is not None:
            print("laundry: obs left out of a done booking", file=sys.stderr)
        text = json.dumps(result, ensure_ascii=False)
    return error, text


def _day_words(day, today, lang):
    if day == today:
        return "i dag" if lang == "sv" else "today"
    if day == today + timedelta(days=1):
        return "i morgon" if lang == "sv" else "tomorrow"
    name = WEEKDAYS[lang][day.weekday()]
    if day - today < timedelta(days=7):
        return name
    return f"{name} {day.day} {MONTHS[lang][day.month - 1]}"


def done_reply(results, said="", today=None, language=None):
    """The spoken answer for a round of tvatt_boka calls that all booked what they asked ("Klart, onsdag 21-24.",
    "Klart, lördag 09-12 och 12-15."), or None when the model should answer: a failure, a partial booking (the
    booker then writes "fel"), or a question that also asks for something after the booking (MORE_ASKED)."""
    if not results or MORE_ASKED.search(said or ""):
        return None
    lang = "en" if language == "en" else "sv"
    today = today or Date.today()
    days = {}
    for is_error, text in results:
        try:
            result = json.loads(text)
            if is_error or result.get("fel") or not result.get("bokat"):
                return None
            day = Date.fromisoformat(result["datum"])
            days.setdefault(day, []).append(str(result["pass"]))
        except (ValueError, TypeError, KeyError, AttributeError):
            return None
    joiner = " och " if lang == "sv" else " and "
    when = joiner.join(f"{_day_words(day, today, lang)} {joiner.join(sorted(passes))}"
                       for day, passes in sorted(days.items()))
    return f"{'Klart' if lang == 'sv' else 'Done'}, {when}."


if __name__ == "__main__":
    error, text = read(*sys.argv[1:3])
    print(text)
    sys.exit(1 if error else 0)
