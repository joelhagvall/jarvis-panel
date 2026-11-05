"""The calendar page's seven days (server/agents/calendar_week.py) and the page's generated lambda. No calendar."""
import importlib.util
import sys
import threading
import unittest
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "server/agents"))
import calendar_week  # noqa: E402

TZ = calendar_week.TZ
NOW = datetime(2026, 10, 30, 16, 0, tzinfo=TZ)        # a Friday
AGENDA = """2026-10-30 14:00-15:00 | Annat | Tandläkare | id A
2026-10-30 18:00-18:30 | Att göra | Ring | Mamma | id B
2026-10-31 10:00-11:00 | Annat | Brunch; Pelle | id C
2026-11-01 | Firma | Moms | id D | repeats, occurrence 2026-11-01
2026-11-02 heldag | Att göra | Namnsdag | id E
2026-11-05 23:30-00:30 | Jobb | Natt | id F
2026-11-06 12:00-12:15 | Att göra | T 0123456789abcdef | id G
2026-11-07 09:00-10:00 | Annat | Utanför veckan | id H"""


def rows(fields):
    return [r.split("|") for r in fields["cal_week"].split(";")]


class CalendarWeekTest(unittest.TestCase):
    def test_parse_keeps_end_times_and_all_day_events_and_drops_probes(self):
        events = calendar_week.parse(AGENDA)
        self.assertEqual([e[3] for e in events],
                         ["Tandläkare", "Ring | Mamma", "Brunch; Pelle", "Moms", "Namnsdag", "Natt", "Utanför veckan"])
        self.assertIsNone(events[3][1])
        self.assertEqual(events[5][1], datetime(2026, 11, 6, 0, 30, tzinfo=TZ))   # past midnight

    def test_layout_has_seven_days_from_today(self):
        out = rows(calendar_week.layout(calendar_week.parse(AGENDA), NOW, {"Annat": "#FF9500"}))
        heads = [r[1:] for r in out if r[0] == "h"]
        self.assertEqual(heads, [["Idag", "fredag 30 okt"], ["I morgon", "lördag 31 okt"], ["Söndag", "1 nov"],
                                 ["Måndag", "2 nov"], ["Tisdag", "3 nov"], ["Onsdag", "4 nov"], ["Torsdag", "5 nov"]])
        no_details = [""] * 5                                         # before show_event: the tap shows the basics
        self.assertEqual(out[1], ["e", "14:00–15:00", "Tandläkare", "#FF9500", "1", "Annat", *no_details])  # over: dimmed
        self.assertEqual(out[2], ["e", "18:00–18:30", "Ring Mamma", "", "", "Att göra", *no_details])  # "|" never splits a row
        self.assertIn(["e", "10:00–11:00", "Brunch, Pelle", "#FF9500", "", "Annat", *no_details], out)  # nor ";"
        self.assertIn(["a", "heldag", "Moms", "", "", "Firma", *no_details], out)
        self.assertEqual(out.count(["n", "Inget inlagt"]), 2)
        self.assertNotIn("Utanför veckan", calendar_week.layout(calendar_week.parse(AGENDA), NOW)["cal_week"])

    def test_all_day_events_come_first_and_a_full_day_says_how_many_more(self):
        agenda = "\n".join(f"2026-10-31 {h:02d}:00-{h:02d}:30 | Annat | M{h} | id {h}" for h in range(8, 16))
        agenda += "\n2026-10-31 | Annat | Heldag | id X"
        out = rows(calendar_week.layout(calendar_week.parse(agenda), NOW))
        tomorrow = out[out.index(["h", "I morgon", "lördag 31 okt"]) + 1:]
        tomorrow = tomorrow[:next(i for i, r in enumerate(tomorrow) if r[0] == "h")]
        self.assertEqual(len(tomorrow), calendar_week.CAPS[1])
        self.assertEqual(tomorrow[0][:3], ["a", "heldag", "Heldag"])
        self.assertEqual(tomorrow[-1], ["m", "+4 till"])

    def test_the_right_column_never_holds_more_rows_than_fit(self):
        agenda = "\n".join(f"2026-11-0{d} {h:02d}:00-{h:02d}:30 | Annat | M | id {d}{h}" for d in range(1, 6) for h in range(8, 12))
        caps = calendar_week.caps_for([0, 0, 4, 4, 4, 4, 4])
        self.assertLessEqual(sum(caps[2:]), calendar_week.RIGHT_SLOTS)
        out = rows(calendar_week.layout(calendar_week.parse(agenda), NOW))
        right = out[out.index(["h", "Söndag", "1 nov"]):]
        self.assertLessEqual(len([r for r in right if r[0] != "h"]), calendar_week.RIGHT_SLOTS)

    def test_before_the_first_read_and_after_a_failed_one(self):
        self.assertEqual(rows(calendar_week.layout(None, NOW))[1], ["n", "Läser kalendern …"])
        self.assertEqual(rows(calendar_week.layout(None, NOW, failed=True))[1], ["n", "Kalendern kan inte läsas just nu"])

    def test_fields_never_wait_and_read_again_on_a_new_revision(self):
        started, release = [], threading.Event()

        class Reader:
            def start(self): pass

            def run(self, tool, args, timeout):
                if tool == "show_event":
                    return False, f"id: {args['id']}\nalarms: 15 min before"
                started.append(args)
                release.wait(2)
                return False, AGENDA

        week = calendar_week.Week(Reader)
        first = week.fields(NOW, revision=1, at=0.0)                   # returns at once, before the read
        self.assertIn("Läser kalendern", first["cal_week"])
        release.set()
        for t in [t for t in threading.enumerate() if t.name == "calendar-week"]:
            t.join(2)
        self.assertIn("Tandläkare", week.fields(NOW, revision=1, at=1.0)["cal_week"])
        self.assertEqual(len(started), 1)                              # fresh: no second read
        week.fields(NOW, revision=2, at=2.0)
        for t in [t for t in threading.enumerate() if t.name == "calendar-week"]:
            t.join(2)
        self.assertEqual(started, [{"days": 7}, {"days": 7}])

    def test_a_failed_read_keeps_the_last_good_week(self):
        answers = [(False, AGENDA), (True, "FAILED: offline")]

        class Reader:
            def start(self): pass

            def run(self, tool, *args, **kwargs):
                return (False, "id: X") if tool == "show_event" else answers.pop(0)

        week = calendar_week.Week(Reader)
        week.fields(NOW, at=0.0)
        week.refresh()
        week.refresh()
        self.assertTrue(week.failed)
        self.assertIn("Tandläkare", calendar_week.layout(week.events, NOW, failed=week.failed)["cal_week"])
        # The old week says so, with the time of the last good read (GPT review, 2026-11-02).
        week.good_at = NOW.replace(hour=14, minute=5)
        shown = rows(week.fields(NOW, at=1.0))
        self.assertEqual(shown[0], ["s", "Kunde inte uppdatera · senast 14:05"])
        self.assertEqual(shown[1][:2], ["h", "Idag"])
        self.assertEqual(calendar_week.stale_row(NOW.replace(day=29), NOW), "s|Kunde inte uppdatera · senast i går 16:00")
        self.assertEqual(rows(calendar_week.layout(week.events, NOW))[0][0], "h")        # a good read: no marker
        self.assertEqual(rows(calendar_week.layout(None, NOW, failed=True))[0][0], "h")  # nothing old to mark


SHOW = """id: F
calendar: Jobb
title: Natt
when: 2026-11-05 23:30-00:30
location: Kontoret
Sveavägen 1, Stockholm
notes: Ta med nyckelkort 🔑️👍🏽 | och ; laddare.

Ring Ola innan.
url: https://www.example.com/mote/123
repeats: FREQ=WEEKLY;INTERVAL=2;BYDAY=TH
alarms: 0 min before, 1850 min before, 15 min before, 1976-04-01 00:55
invited: 2"""


class EventDetailsTest(unittest.TestCase):
    """What a tap on an event shows from iCloud (2026-11-04): alarms, place, repeat, notes, link."""

    def test_show_event_is_read_whole(self):
        info = calendar_week.show_details(SHOW)
        self.assertEqual(info["alarms"][:3], [0, 1850, 15])
        self.assertEqual(info["alarms"][3], datetime(1976, 4, 1, 0, 55, tzinfo=TZ))
        self.assertEqual(info["notes"], "Ta med nyckelkort 🔑️👍🏽 | och ; laddare.\n\nRing Ola innan.")
        self.assertEqual(info["repeats"], "FREQ=WEEKLY;INTERVAL=2;BYDAY=TH")
        self.assertEqual(info["url"], "https://www.example.com/mote/123")

    def test_the_row_carries_the_details_in_ringing_order(self):
        events = calendar_week.parse(AGENDA)
        natt = next(e for e in events if e[3] == "Natt")
        out = rows(calendar_week.layout(events, NOW, details={natt: calendar_week.show_details(SHOW)}))
        row = next(r for r in out if r[2:3] == ["Natt"])
        place, repeat, alarms, notes, url = row[6:]
        self.assertEqual(place, "Kontoret, Sveavägen 1, Stockholm")     # Apple's name, then the address
        self.assertEqual(repeat, "Varannan vecka")
        # Apple's 1976 "no default alarm" marker is not an alarm; the others in the order they ring.
        self.assertEqual(alarms.split("\n"), ["ons 16:40 · 1 dag 6 h 50 min före", "tors 23:15 · 15 min före",
                                              "tors 23:30 · vid start"])
        self.assertEqual(notes, "Ta med nyckelkort och , laddare.\nRing Ola innan.")   # no emoji box, no row split
        self.assertEqual(url, "example.com/mote/123")

    def test_an_all_day_event_rings_at_clock_times_and_none_says_so(self):
        day = datetime(2026, 10, 31, tzinfo=TZ)
        self.assertEqual(calendar_week.alarm_text([900], day, True, NOW), "idag 09:00")
        self.assertEqual(calendar_week.alarm_text([], day, True, NOW), "Inga påminnelser")
        self.assertEqual(calendar_week.alarm_text(None, day, True, NOW), "")

    def test_long_notes_are_cut_to_the_card(self):
        text = calendar_week.notes_text("ord " * 200)
        self.assertLessEqual(len(text), calendar_week.NOTES_CHARS)
        self.assertTrue(text.endswith("…"))
        self.assertEqual(calendar_week.notes_text("\n".join(f"rad {i}" for i in range(9))).count("\n"),
                         calendar_week.NOTES_LINES - 1)

    def test_repeat_words(self):
        self.assertEqual([calendar_week.repeat_text(r) for r in ("FREQ=DAILY", "FREQ=YEARLY;INTERVAL=2",
                                                                  "FREQ=MONTHLY;INTERVAL=3", "")],
                         ["Varje dag", "Vartannat år", "Var 3:e månad", ""])

    def test_details_are_read_after_the_agenda_and_again_after_a_change(self):
        calls = []

        class Reader:
            def start(self): pass

            def run(self, tool, args, timeout):
                calls.append((tool, args.get("id")))
                if tool == "show_event":
                    return False, f"id: {args['id']}\nalarms: 15 min before"
                return False, AGENDA

        week = calendar_week.Week(Reader)
        week.fields(NOW, revision=(1, 1), at=0.0)
        for t in [t for t in threading.enumerate() if t.name == "calendar-week"]:
            t.join(2)
        shown = [uid for tool, uid in calls if tool == "show_event"]
        self.assertEqual(shown, ["A", "B", "C", "D", "E", "F", "H"])          # every event once, not the probe G
        self.assertIn("15 min före", week.fields(NOW, revision=(1, 1), at=1.0)["cal_week"])
        calls.clear()
        week.fields(NOW, revision=(1, 2), at=2.0)       # an edited alarm moves only the change tokens
        for t in [t for t in threading.enumerate() if t.name == "calendar-week"]:
            t.join(2)
        self.assertEqual(len([c for c in calls if c[0] == "show_event"]), 7)

    def test_a_failed_show_event_keeps_the_old_details(self):
        week = calendar_week.Week(None)

        class Reader:
            answer = (False, "id: A\nalarms: 15 min before")

            def run(self, tool, args, timeout):
                return self.answer

        week.reader = Reader()
        week.read_details({"e": "A"}, at=0.0)
        Reader.answer = (True, "FAILED: offline")
        week.changed = True
        week.read_details({"e": "A"}, at=1.0)
        self.assertEqual(week.details["A"][1]["alarms"], [15])


class CalendarPageTest(unittest.TestCase):
    def test_the_page_and_the_server_agree_on_the_slots(self):
        spec = importlib.util.spec_from_file_location("calendar_page", ROOT / "styles/glass-v2/calendar_page.py")
        page = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(page)
        self.assertEqual(page.CAPS, calendar_week.CAPS)
        for d, cap in enumerate(page.CAPS):
            self.assertIn(f"id(cw{d}_{cap - 1}_title)", page.WEEK)
            self.assertNotIn(f"id(cw{d}_{cap}_title)", page.WEEK)

    def test_the_card_has_a_row_for_every_detail_field(self):
        spec = importlib.util.spec_from_file_location("calendar_page", ROOT / "styles/glass-v2/calendar_page.py")
        page = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(page)
        self.assertEqual(len(page.INFO), len(calendar_week.detail_fields((NOW, None, "", ""), None, NOW)))
        for key, _ in page.INFO:
            self.assertIn(f"id(cw_d_{key}_row)", page.DETAIL)


if __name__ == "__main__":
    unittest.main()
