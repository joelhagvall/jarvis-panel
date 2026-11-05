"""The calendar page (Joel, 2026-10-30): a tap on the dashboard's calendar card opens the next seven days.

The 10-second sync (server/calendar_sync.py) reads two days of timed events for the card and the notices. This
reads seven days with end times and all-day events, on its own MCP session, again when the sync's revision
changes and otherwise every five minutes, never on the request that asks for it. layout() packs the days into the
page's fixed rows (styles/glass-v2/calendar_page.py) as the attribute cal_week:

    rows separated by ";", fields by "|"
    h|Idag|fredag 30 okt          a day; the rows after it belong to it
    s|Kunde inte uppdatera · senast 14:05   first, only when the last read failed and older events are shown
    e|14:00–15:00|title|#RRGGBB|1|Jobb|place|repeat|alarms|notes|url
                                  an event with a time; "1" when it is over, then its calendar and what the
                                  tap shows from iCloud (details()): alarms and notes keep their line breaks
    a|heldag|title|#RRGGBB||Jobb|place|repeat|alarms|notes|url   an all-day event
    n|Inget inlagt                a day without events
    m|+2 till                     what did not fit

A tap on an event's row on the page shows it whole: its day, time, full title and calendar (2026-11-02), and since
2026-11-04 what iCloud holds about it: its alarms as clock times, place, how it repeats, notes and link (Joel: "vill
ju ha koll"). Those come from the MCP's show_event, one call per event after each agenda read, on the same
session: again for every event when the sync's revision or change tokens move (an edited alarm changes no agenda
line), otherwise at most every DETAIL_S. A failed show_event keeps that event's last details. Notes never reach
the journal.

Logged to the journal: a failed read with its reason, and the first good read after one.
"""
import re
import sys
import unicodedata
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Stockholm")
DAYS = 7
REFRESH_S = 5 * 60
# Slots per day on the page: today and tomorrow in the left column, the next five in the right one.
CAPS = (6, 6, 3, 3, 3, 3, 3)
RIGHT_SLOTS = 10            # the right column's height holds five days and ten rows
WEEKDAYS = ["måndag", "tisdag", "onsdag", "torsdag", "fredag", "lördag", "söndag"]
MONTHS = ["jan", "feb", "mars", "april", "maj", "juni", "juli", "aug", "sep", "okt", "nov", "dec"]
TIMED = re.compile(r"^(\d{4}-\d\d-\d\d) (\d\d:\d\d)-(\d\d:\d\d) \| ([^|]+?) \| (.+?) \| id (\S+)")
ALL_DAY = re.compile(r"^(\d{4}-\d\d-\d\d)(?: heldag)? \| ([^|]+?) \| (.+?) \| id (\S+)")
DETAIL_S = 30 * 60          # details re-read at least this often without a change
MAX_DETAILS = 40            # show_event calls per read; the page shows at most 22 events
ALARMS_SHOWN = 6
NOTES_LINES, NOTES_CHARS, LINE_CHARS = 5, 280, 66   # the card's notes: about 66 characters a line at 16 px
SHORT_DAYS = ["mån", "tis", "ons", "tors", "fre", "lör", "sön"]
SHOW_KEYS = ("id", "calendar", "title", "when", "location", "notes", "url", "repeats", "removed occurrences",
             "changed occurrence", "alarms", "invited")
TEST_EVENT = re.compile(r'(?:Paneltest [0-9a-f]{16}(?: uppdaterad)?|[TU] [0-9a-f]{16})')   # watch.py's probe


def parse(agenda, ids=None):
    """[(start, end or None, calendar, title)] from the calendar MCP's agenda text; end is None all day. ids, a
    dict, gets each event's iCloud id (what show_event takes)."""
    out = []
    for line in (agenda or "").splitlines():
        line = line.strip()
        if m := TIMED.match(line):
            day, start, end, calendar, title = m.group(1), m.group(2), m.group(3), m.group(4), m.group(5)
            begins = datetime.fromisoformat(f"{day}T{start}").replace(tzinfo=TZ)
            ends = datetime.fromisoformat(f"{day}T{end}").replace(tzinfo=TZ)
            event, uid = (begins, ends + timedelta(days=1) if ends < begins else ends, calendar.strip(),
                          title.strip()), m.group(6)
        elif m := ALL_DAY.match(line):
            begins = datetime.fromisoformat(m.group(1)).replace(tzinfo=TZ)
            event, uid = (begins, None, m.group(2).strip(), m.group(3).strip()), m.group(4)
        else:
            continue
        if TEST_EVENT.fullmatch(event[3]):
            continue
        out.append(event)
        if ids is not None:
            ids[event] = uid
    return out


def show_details(text):
    """What the card shows from the MCP's show_event text: {"alarms": [minutes before the start, or a datetime],
    "location", "notes", "url", "repeats"}; notes keep their lines."""
    fields, key = {}, None
    for line in (text or "").splitlines():
        head = next((k for k in SHOW_KEYS if line.startswith(k + ":") or line.startswith(k + " ")), None)
        if head:
            key = head
            fields[key] = line[len(head) + 1:].strip()
        elif key in ("location", "notes"):        # Apple's place is often a name, then the address
            fields[key] += "\n" + line
    alarms = []
    for part in filter(None, (p.strip() for p in fields.get("alarms", "").split(","))):
        if m := re.fullmatch(r"(-?\d+) min before", part):
            alarms.append(int(m.group(1)))
        else:
            try:
                alarms.append(datetime.fromisoformat(part).replace(tzinfo=TZ))
            except ValueError:
                continue
    return {"alarms": alarms, "location": fields.get("location", ""), "notes": fields.get("notes", ""),
            "url": fields.get("url", ""), "repeats": fields.get("repeats", "")}


def panel_text(text):
    """text the card's fonts draw: emoji and symbols go, a letter the fonts lack loses its accent (background.py)."""
    from background import plaque_text
    kept = "".join(c for c in str(text) if c == "\n" or not ("\ufe00" <= c <= "\ufe0f" or unicodedata.category(c)
                                                             in ("So", "Sk", "Cs", "Co", "Cn", "Cc", "Cf")))
    return "\n".join(plaque_text(line) for line in kept.split("\n"))


def span(minutes):
    days, rest = divmod(abs(minutes), 1440)
    hours, mins = divmod(rest, 60)
    parts = ([f"{days} {'dag' if days == 1 else 'dagar'}"] if days else []) + ([f"{hours} h"] if hours else []) \
        + ([f"{mins} min"] if mins else [])
    return " ".join(parts)


def clock_day(at, now):
    """"idag 20:00", "i morgon 9:00", "tors 20:00" within the week, else "12 nov 20:00"."""
    at, today = at.astimezone(TZ), now.astimezone(TZ).date()
    offset = (at.date() - today).days
    day = {0: "idag", 1: "i morgon", -1: "i går"}.get(offset)
    if day is None:
        day = SHORT_DAYS[at.weekday()] if 0 < offset < 7 else f"{at.day} {MONTHS[at.month - 1]}"
    return f"{day} {at:%H:%M}"


def alarm_text(alarms, start, all_day, now):
    """The card's alarms, one a line, in the order they ring: when, and for a timed event how long before."""
    if alarms is None:
        return ""
    rings = []
    for a in alarms:
        at = start - timedelta(minutes=a) if isinstance(a, int) else a
        if at < start - timedelta(days=60):
            continue              # Apple's "no default alarm" marker: an ACTION:NONE alarm in 1976
        rings.append((at, a))
    if not rings:
        return "Inga påminnelser"
    rings.sort(key=lambda r: r[0])
    lines = []
    for at, a in rings:
        line = clock_day(at, now)
        if isinstance(a, int) and not all_day:
            line += " · " + ("vid start" if a == 0 else f"{span(a)} före" if a > 0 else f"{span(a)} efter start")
        lines.append(line)
    if len(lines) > ALARMS_SHOWN:
        lines = lines[:ALARMS_SHOWN - 1] + [f"+{len(lines) - ALARMS_SHOWN + 1} till"]
    return "\n".join(lines)


def repeat_text(rule):
    """"Varje vecka", "Varannan månad", "Var 3:e dag" from an RRULE; "" for none."""
    if not rule:
        return ""
    parts = dict(p.split("=", 1) for p in rule.split(";") if "=" in p)
    unit = {"DAILY": "dag", "WEEKLY": "vecka", "MONTHLY": "månad", "YEARLY": "år"}.get(parts.get("FREQ"))
    if unit is None:
        return "Upprepas"
    n = int(parts.get("INTERVAL", "1") or 1)
    neuter = unit == "år"
    if n == 1:
        return f"Varje {unit}"
    if n == 2:
        return f"{'Vartannat' if neuter else 'Varannan'} {unit}"
    return f"{'Vart' if neuter else 'Var'} {n}:e {unit}"


def notes_text(notes):
    """At most NOTES_LINES card lines and NOTES_CHARS characters, "…" where it was cut; blank lines go."""
    lines, used, chars = [], 0, 0
    for line in panel_text(notes).splitlines():
        line = " ".join(line.replace("|", " ").replace(";", ",").split())
        if not line:
            continue
        wraps = max(1, -(-len(line) // LINE_CHARS))
        room_lines, room_chars = NOTES_LINES - used, NOTES_CHARS - chars
        if wraps > room_lines or len(line) > room_chars:
            cut = min(room_chars, room_lines * LINE_CHARS) - 1
            if cut > 8:
                lines.append(line[:cut].rstrip() + "…")
            elif lines:
                lines[-1] = lines[-1].rstrip("…") + "…"
            break
        lines.append(line)
        used, chars = used + wraps, chars + len(line)
    return "\n".join(lines)


def short_url(url):
    url = re.sub(r"^https?://(www\.)?", "", url.strip())
    return clean(url[:80])


def detail_fields(event, info, now):
    """The tap's five fields for one event: place, repeat, alarms, notes, url; empty before show_event."""
    if info is None:
        return ["", "", "", "", ""]
    start, end = event[0], event[1]
    place = clean(panel_text(info["location"].replace("\n", ", ")))[:120]
    return [place, repeat_text(info["repeats"]), alarm_text(info["alarms"], start, end is None, now),
            notes_text(info["notes"]), short_url(info["url"])]


def clean(text):
    return " ".join(str(text).replace("|", " ").replace(";", ",").split())


def day_head(day, today):
    offset = (day - today).days
    if offset == 0:
        return "Idag", f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"
    if offset == 1:
        return "I morgon", f"{WEEKDAYS[day.weekday()]} {day.day} {MONTHS[day.month - 1]}"
    return WEEKDAYS[day.weekday()].capitalize(), f"{day.day} {MONTHS[day.month - 1]}"


def caps_for(counts):
    """Slots per day: CAPS, then the right column's days cut, the fullest first, to RIGHT_SLOTS rows."""
    caps = [min(c, max(n, 1)) for c, n in zip(CAPS, counts)]
    while sum(caps[2:]) > RIGHT_SLOTS:
        i = max(range(2, DAYS), key=lambda d: (caps[d], -d))
        caps[i] -= 1
    return caps


def stale_row(good_at, now):
    """The page's marker while it shows events from before a failed read (GPT review, 2026-11-02)."""
    if good_at is None:
        return "s|Kunde inte uppdatera"
    when = good_at.astimezone(TZ)
    days = (now.astimezone(TZ).date() - when.date()).days
    at = f"{when:%H:%M}" if days == 0 else f"i går {when:%H:%M}" if days == 1 else f"{when.day} {MONTHS[when.month - 1]}"
    return f"s|Kunde inte uppdatera · senast {at}"


def layout(events, now, colors=None, failed=False, good_at=None, details=None):
    """{"cal_week": rows} for the seven days from today; events from parse(), or None before the first good read
    (failed: that read failed). After a failure the last good events stay, under a marker with good_at, the time
    of the last good read. details: {event: show_details()} for the tap."""
    colors, details = colors or {}, details or {}
    today = now.astimezone(TZ).date()
    per_day = [[] for _ in range(DAYS)]
    for start, end, calendar, title in sorted(events or [], key=lambda e: (e[1] is not None, e[0])):
        offset = (start.date() - today).days
        if 0 <= offset < DAYS:
            per_day[offset].append((start, end, calendar, title))
    caps = caps_for([len(d) for d in per_day])
    rows = [stale_row(good_at, now)] if failed and events is not None else []
    for offset, (day_events, cap) in enumerate(zip(per_day, caps)):
        rows.append("h|%s|%s" % day_head(today + timedelta(days=offset), today))
        if events is None:
            if offset == 0:
                rows.append("n|Kalendern kan inte läsas just nu" if failed else "n|Läser kalendern …")
            continue
        if not day_events:
            rows.append("n|Inget inlagt")
            continue
        shown = day_events if len(day_events) <= cap else day_events[:cap - 1]
        for event in shown:
            start, end, calendar, title = event
            colour = colors.get(calendar, "")
            more = "|".join(detail_fields(event, details.get(event), now))
            if end is None:
                rows.append(f"a|heldag|{clean(title)}|{colour}||{clean(calendar)}|{more}")
            else:
                over = "1" if end <= now else ""
                rows.append(f"e|{start:%H:%M}–{end:%H:%M}|{clean(title)}|{colour}|{over}|{clean(calendar)}|{more}")
        if len(shown) < len(day_events):
            rows.append(f"m|+{len(day_events) - len(shown)} till")
    return {"cal_week": ";".join(rows)}


class Week:
    """The seven days, read in the background. fields() never waits for the calendar."""

    def __init__(self, reader_factory, refresh_s=REFRESH_S):
        self.reader_factory, self.refresh_s = reader_factory, refresh_s
        self.reader, self.events, self.failed = None, None, False
        self.ids, self.details = {}, {}         # event -> iCloud id; id -> (monotonic read time, show_details())
        self.good_at = None                     # wall-clock time of the last good read
        self.read_at, self.revision, self.busy = None, None, False
        self.changed = False                    # this read follows a new revision: every event's details again
        self.lock = threading.Lock()

    def due(self, revision, at):
        return self.read_at is None or revision != self.revision or at - self.read_at >= self.refresh_s

    def fields(self, now, colors=None, revision=None, at=None):
        at = time.monotonic() if at is None else at
        with self.lock:
            start = not self.busy and self.due(revision, at)
            if start:
                self.changed = self.read_at is not None and revision != self.revision
                self.busy, self.revision, self.read_at = True, revision, at
            events, failed, good_at = self.events, self.failed, self.good_at
            details = {e: self.details[uid][1] for e, uid in self.ids.items() if uid in self.details}
        if start:
            threading.Thread(target=self.refresh, name="calendar-week", daemon=True).start()
        return layout(events, now, colors, failed, good_at, details)

    def refresh(self):
        try:
            if self.reader is None:
                self.reader = self.reader_factory()
                self.reader.start()
            is_error, text = self.reader.run("agenda", {"days": DAYS}, timeout=15)
            if is_error or text.startswith(("FAILED:", "REFUSED:", "STILL WORKING")):
                raise RuntimeError(f"agenda refused: {text[:80]}")
            ids = {}
            events = parse(text, ids)
            with self.lock:
                if self.failed:
                    print("calendar week: read again", file=sys.stderr)
                self.events, self.ids, self.failed, self.good_at = events, ids, False, datetime.now(TZ)
            self.read_details(ids)
        except Exception as e:
            self.reader = None
            with self.lock:
                self.read_at -= max(0, self.refresh_s - 60)     # try again in a minute, not five
                if not self.failed:
                    print(f"calendar week: read failed: {type(e).__name__}: {e}", file=sys.stderr)
                self.failed = True
        finally:
            with self.lock:
                self.busy = False

    def read_details(self, ids, at=None):
        """show_event for the week's events whose details are missing, old, or from before a change; the rest
        keep theirs. One failure is logged (never the text) and keeps that event's old details."""
        at = time.monotonic() if at is None else at
        wanted = list(dict.fromkeys(ids.values()))[:MAX_DETAILS]
        with self.lock:
            changed, self.changed = self.changed, False
            self.details = {uid: v for uid, v in self.details.items() if uid in wanted}
            due = [uid for uid in wanted if changed or uid not in self.details or at - self.details[uid][0] >= DETAIL_S]
        failed = 0
        for uid in due:
            try:
                is_error, text = self.reader.run("show_event", {"id": uid}, timeout=10)
                if is_error or text.startswith(("FAILED:", "REFUSED:", "STILL WORKING")) or not text.startswith("id:"):
                    raise RuntimeError("show_event refused")
                info = show_details(text)
            except Exception as e:
                failed += 1
                if failed == 1:
                    print(f"calendar week: details failed: {type(e).__name__}", file=sys.stderr)
                continue
            with self.lock:
                self.details[uid] = (at, info)
        if failed:
            print(f"calendar week: details for {len(due) - failed} of {len(due)} events", file=sys.stderr)
