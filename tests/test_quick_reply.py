"""Direct replies: compound requests and unverified data must still reach the agent."""
import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "server"), str(ROOT / "server/agents")]
import quick_reply

NOW = datetime(2054, 10, 23, 12, tzinfo=ZoneInfo("Europe/Stockholm"))


class QuickReplyTest(unittest.TestCase):
    def test_clock_and_language(self):
        self.assertEqual(quick_reply.answer("Vad är klockan?", now=NOW), "Klockan är 12:00.")
        self.assertIsNone(quick_reply.answer("Vad är klockan?", language="en", now=NOW))

    def test_compound_followup_and_changes_reach_agent(self):
        for text in ("Och imorgon?", "Vad blir vädret imorgon och boka en resa", "Ändra min kalender idag",
                     "Vad har jag i kalendern idag och imorgon?", "När går nästa tunnelbana till Akalla?",
                     "Vad blir vädret på lördag?", "Vad är klockan? Sätt en timer."):
            self.assertIsNone(quick_reply.answer(text, now=NOW), text)

    def test_metro_uses_future_uncancelled_departures_in_time_order(self):
        def train(minutes, direction, state="EXPECTED"):
            return {"expected": (NOW + timedelta(minutes=minutes)).isoformat(), "state": state,
                    "direction_code": direction, "destination": str(direction)}
        home = SimpleNamespace(_cached=Mock(return_value={"departures": [train(8, 1), train(-1, 1),
              train(1, 2, "CANCELLED"), train(3, 2), train(2, 1)]}))
        answer = quick_reply.answer("När går nästa tunnelbana?", home=home, now=NOW)
        self.assertIn("mot 1 klockan 12:02, och mot 2 klockan 12:03", answer)
        home._cached.assert_called_once_with(quick_reply.home_status.SL, 20)

    def test_metro_disruptions_and_missing_data_reach_agent(self):
        for data in ({}, {"stop_deviations": [{"message": "Stopp"}]}):
            home = SimpleNamespace(_cached=Mock(return_value=data))
            self.assertIsNone(quick_reply.answer("När går nästa tunnelbana?", home=home, now=NOW))

    def test_tomorrow_is_explicit_and_failed_partial_forecasts_are_not_claimed(self):
        day = {"available": True, "partial_coverage": False, "sampled_temperature_min_c": 7,
               "sampled_temperature_max_c": 12, "conditions": ["halvklart"],
               "precipitation_probability_max_percent": 40}
        home = SimpleNamespace(forecast=Mock(return_value=(False, json.dumps({"days": [day]}))))
        reply = quick_reply.answer("Vad blir vädret imorgon?", home=home, now=NOW)
        self.assertIn("i morgon", reply)
        self.assertIn("mellan 7 och 12 grader", reply)
        home.forecast.assert_called_once_with(from_date="2054-10-24")
        day["partial_coverage"] = True
        home.forecast.return_value = False, json.dumps({"days": [day]})
        self.assertIsNone(quick_reply.answer("Vad blir vädret imorgon?", home=home, now=NOW))
        home.forecast.return_value = True, "offline"
        self.assertIsNone(quick_reply.answer("Vad blir vädret imorgon?", home=home, now=NOW))

    def test_calendar_only_accepts_fresh_fully_parsed_snapshot(self):
        row = "2054-10-23 13:00-14:00 | Jobb | Möte | id 1"
        self.assertEqual(quick_reply.answer("Vad har jag i kalendern idag?", agenda=(row, 4, 8), now=NOW),
                         "I dag har du Möte klockan 13:00.")
        for agenda in ((row, 31, 8), (row + "\n2054-10-23 heldag | Födelsedag", 0, 8), ("", 0, 8)):
            self.assertIsNone(quick_reply.answer("Vad har jag i kalendern idag?", agenda=agenda, now=NOW))

    def test_calendar_tomorrow_and_future_weekday_use_the_requested_date(self):
        agenda = ("2054-10-23 13:00-14:00 | Jobb | Dagens möte | id 1\n"
                  "2054-10-24 10:00-11:00 | Annat | Lördagsmöte | id 2\n"
                  "2054-10-26 09:00-10:00 | Jobb | Måndagsmöte | id 3")
        for question, expected in (
            ("Vad har jag i kalendern imorgon?", "I morgon har du Lördagsmöte klockan 10:00."),
            ("Vad har jag inbokat i morgon?", "I morgon har du Lördagsmöte klockan 10:00."),
            ("Vad har jag i kalendern på måndag?", "På måndag har du Måndagsmöte klockan 09:00."),
            # how Joel says it (2054-11-01: four times through the model, 1.8-3.1 s)
            ("Vad har jag på kalendern imorgon?", "I morgon har du Lördagsmöte klockan 10:00."),
            ("Vad har jag på kalendern på måndag?", "På måndag har du Måndagsmöte klockan 09:00."),
        ):
            with self.subTest(question=question):
                self.assertEqual(quick_reply.answer(question, agenda=(agenda, 4, 8), now=NOW), expected)

    def test_calendar_date_ambiguity_coverage_and_unknown_rows_reach_agent(self):
        agenda = "2054-10-24 10:00-11:00 | Annat | Möte | id 2"
        for question, snapshot in (
            ("Vad har jag i kalendern på fredag?", (agenda, 4, 8)),  # today: this or next Friday?
            ("Vad har jag i kalendern nästa måndag?", (agenda, 4, 8)),
            ("Vad har jag i kalendern på måndag och tisdag?", (agenda, 4, 8)),
            ("Vad har jag i kalendern imorgon?", (agenda, 4, 1)),
            ("Vad har jag i kalendern på måndag?", (agenda, 4, 3)),
            ("Vad har jag i kalendern imorgon?", (agenda, 31, 8)),
            ("Vad har jag i kalendern imorgon?", (agenda + "\n2054-10-24 heldag | Resa", 0, 8)),
        ):
            with self.subTest(question=question, snapshot=snapshot):
                self.assertIsNone(quick_reply.answer(question, agenda=snapshot, now=NOW))

    def test_calendar_empty_requested_day_is_named_without_losing_other_dates(self):
        agenda = "2054-10-23 13:00-14:00 | Jobb | Dagens möte | id 1"
        self.assertEqual(quick_reply.answer("Vad har jag i kalendern imorgon?", agenda=(agenda, 4, 8), now=NOW),
                         "Inget att ta upp i kalendern i morgon.")

    def test_direct_calendar_leaves_out_silent_topics(self):
        agenda = "2054-10-23 13:00-14:00 | Jobb | Möte | id 1\n2054-10-23 08:00-08:01 | Hem | Dammsuga | id 2"
        with patch.dict(os.environ, {"JARVIS_SILENT_TOPICS": r"dammsug\w*|vacuum\w*"}):
            reply = quick_reply.answer("Vad har jag i kalendern idag?", agenda=(agenda, 0, 8), now=NOW)
        self.assertEqual(reply, "I dag har du Möte klockan 13:00.")

    def test_recurring_date_only_rows_on_other_days_do_not_block_friday(self):
        now = datetime(2054, 10, 25, 13, tzinfo=NOW.tzinfo)
        agenda = ("2054-10-30 18:00-18:30 | Att göra | Utvärdering | id a\n"
                  "2054-11-01 | Demokalender | Demohändelse | id b | repeats, occurrence 2054-11-01\n"
                  "2054-11-01 | Demokalender | Annan träning | id c | repeats, occurrence 2054-11-01")
        self.assertEqual(quick_reply.answer("Vad har jag i kalendern på fredag?", agenda=(agenda, 1, 8), now=now),
                         "På fredag har du Utvärdering klockan 18:00.")

    def test_recurring_timed_rows_keep_their_time_and_validate_occurrence_date(self):
        row = "2054-10-23 13:00-14:00 | Jobb | Möte | id a | repeats, occurrence 2054-10-23"
        self.assertEqual(quick_reply.answer("Vad har jag i kalendern idag?", agenda=(row, 1, 8), now=NOW),
                         "I dag har du Möte klockan 13:00.")
        mismatch = row.replace("occurrence 2054-10-23", "occurrence 2054-10-24")
        self.assertIsNone(quick_reply.answer("Vad har jag i kalendern idag?", agenda=(mismatch, 1, 8), now=NOW))

    def test_unknown_times_on_requested_day_and_unscoped_or_multiday_rows_still_reach_agent(self):
        timed = "2054-10-23 13:00-14:00 | Jobb | Möte | id a"
        for other in (
            "2054-10-23 | Demokalender | Demohändelse | id b | repeats, occurrence 2054-10-23",
            "2054-10-23 heldag | Jobb | Resa | id b",
            "2054-10-22 13:00 .. 2054-10-24 14:00 | Jobb | Resa | id b",
            "2054-10-24 | Demokalender | Demohändelse | id b | repeats, occurrence 2054-10-23",
            "Oklart kalenderformat",
        ):
            with self.subTest(other=other):
                self.assertIsNone(quick_reply.answer("Vad har jag i kalendern idag?", agenda=(timed+'\n'+other, 1, 8), now=NOW))


if __name__ == "__main__":
    unittest.main()
