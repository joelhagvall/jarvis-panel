"""The spoken gate for tvatt_boka, morgon_tv_andra and vackning_andra (server/agents/spoken_change.py).

The 2026-11-02 review ran the first gate against ordinary sentences: it refused a plain "Ja" to "Onsdag 21-24
är ledigt i tvättstugan. Ska jag boka det?", "Ja, boka den", "21 till 24" after "Vilket pass?", every English
request, "Ändra larmet till sju" and the polite "Kan du inte boka ...", and it let "Kan man boka tvätt på
söndagar?" through. Each case here is (Joel's words, Jarvis's last reply, Joel's turn before it). Free and
offline: nothing books or writes.
Run: .venv/bin/python -m unittest tests.test_spoken_change"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "agents"))
from spoken_change import Turn, allowed, may_write_calendar  # noqa: E402

ALLOWED = {
    "laundry": [
        ("Boka tvätt på fredag 18", "", ""),
        ("Kan du boka tvätt på fredag 18-21?", "", ""),
        ("Kan du inte boka tvätt på fredag?", "", ""),
        ("Boka tvätt fredag 18 om det inte är fullt", "", ""),
        ("Reservera tvättstugan fredag 18", "", ""),
        ("Book the laundry room on Friday at 18", "", ""),
        ("Ja", "Onsdag 21-24 är ledigt i tvättstugan. Ska jag boka det?", ""),
        ("Ja, boka den", "Ska jag boka tvätt på fredag?", ""),
        ("Javisst", "Ska jag boka tvätt på fredag?", ""),
        ("Ja, kör", "Ska jag boka tvätt på fredag?", ""),
        ("Ja tack!", "Vill du att jag bokar tvätt på fredag 18-21?", ""),
        ("Yes please", "Shall I book the laundry on Friday?", ""),
        ("21 till 24", "Vilket pass vill du ha?", "Boka tvätt på onsdag"),
        ("21", "Det är ledigt 18-21 och 21-24. Vill du boka något av dem?", "Finns det lediga tvättider på onsdag?"),
    ],
    "wake": [
        ("Väck mig sju på lördag", "", ""),
        ("Väck mig sju på lördag och säg att vi inte får glömma matsäcken", "", ""),
        ("Ändra larmet till sju", "", ""),
        ("Jag vill vakna halv sju i morgon", "", ""),
        ("Ingen väckning på lördag", "", ""),
        ("Kan du väcka mig på fredag klockan sju?", "", ""),
        ("Wake me up at seven tomorrow", "", ""),
        ("Ja", "Vill du att jag flyttar väckningen till 07.30?", ""),
        ("Sju", "Till vilken tid?", "Ändra väckningen på fredag"),
    ],
    "morning_tv": [
        ("Aktivera morgon-tv på fredag", "", ""),
        ("Stäng av morgon-TV på lördag", "", ""),
        ("Turn off the morning TV on Saturday", "", ""),
        ("Ja gärna", "Ska jag ändra morgon-TV:n till 07:30?", ""),
    ],
}

REFUSED = {
    "laundry": [
        ("", "", ""),
        ("Kan man boka tvätt på söndagar?", "", ""),
        ("Om jag ber dig boka tvätt, vad händer?", "", ""),
        ("Boka inte tvätt på fredag", "", ""),
        ("Jag vill inte boka tvätt", "", ""),
        ("Avboka tvätten på fredag", "", ""),
        ("Nej, boka tvätt på fredag var fel", "", ""),
        ("Finns det lediga tider på torsdag?", "", ""),
        ("Ja", "", ""),
        ("Nej", "Ska jag boka tvätt på fredag?", ""),
        ("Nej tack", "Ska jag boka tvätt på fredag?", ""),
        ("Inte nu", "Ska jag boka tvätt på fredag?", ""),
        ("Kanske", "Ska jag boka tvätt på fredag?", ""),
        ("Ja, vad kostar det?", "Ska jag boka tvätt på fredag?", ""),
        ("Ja, vilka tvättider kan jag boka?", "Ska jag boka tvätt på fredag?", ""),
        ("Ja, men ändra inget", "Ska jag boka tvätt på fredag?", ""),
        ("Jag ska tänka på det", "Ska jag boka tvätt på fredag?", ""),
        ("Ja", "Ska jag inte boka tvätt på fredag?", ""),
        ("Ja", "Jag kollade tvätten. Ska jag boka ett möte?", ""),
        ("Ja", "Vill du att jag kollar tvättstugan?", ""),
        ("Ja, vad blir vädret?", "Du har tvätt på onsdag. Något mer?", ""),
        ("Ja", "Tvättstugan är bokad. Ska jag ändra väckningen?", ""),
        ("21", "Vilket pass vill du ha?", "Vad har jag för tvättider?"),
    ],
    "wake": [
        ("Vad har jag för väckning i morgon?", "", ""),
        ("Ändra inte väckningen på fredag", "", ""),
        ("Väck mig inte på lördag", "", ""),
        ("Vacker dag idag, ändra inget", "", ""),
        ("När ska jag vakna i morgon?", "", ""),
        ("Ja tack", "Väckningen är 06:30 i morgon. Något mer?", ""),
        ("Ja", "Väckningen är 06:30. Ska jag ändra morgon-TV?", ""),
        ("Ändra morgon-TV. Vad är min väckning?", "", ""),
        ("Stäng av morgon-tv och läs upp min väckning", "", ""),
        ("Hur ändrar jag väckningen?", "", ""),
    ],
    "morning_tv": [
        ("Stäng av tv:n", "", ""),
        ("Stäng av väckningen på fredag", "", ""),
        ("Ja", "Morgon-TV är på. Ska jag ändra väckningen?", ""),
        ("Ställ inte morgon-TV på fredag", "", ""),
    ],
}


def check(routine, said, reply, before):
    with contextlib.redirect_stderr(io.StringIO()) as err:
        result = allowed(Turn(said, reply, before), routine, "test_tool")
    return result, err.getvalue()


class SpokenChangeTest(unittest.TestCase):
    def test_ordinary_requests_and_answers_pass(self):
        for routine, cases in ALLOWED.items():
            for said, reply, before in cases:
                with self.subTest(routine=routine, said=said, reply=reply, before=before):
                    self.assertTrue(check(routine, said, reply, before)[0])

    def test_declines_questions_and_other_routines_are_refused(self):
        for routine, cases in REFUSED.items():
            for said, reply, before in cases:
                with self.subTest(routine=routine, said=said, reply=reply, before=before):
                    self.assertFalse(check(routine, said, reply, before)[0])

    def test_a_refusal_logs_the_tool_and_reason_but_not_the_words(self):
        result, log = check("laundry", "Boka inte tvätt på fredag", "", "")
        self.assertFalse(result)
        self.assertIn("spoken_change: test_tool refused:", log)
        self.assertNotIn("fredag", log)

    def test_the_turn_comes_from_history_and_the_environment(self):
        recent = [{"role": "user", "text": "Boka tvätt på onsdag"}, {"role": "jarvis", "text": "Vilket pass?"}]
        turn = Turn.from_recent("21 till 24", recent)
        self.assertEqual(turn, Turn("21 till 24", "Vilket pass?", "Boka tvätt på onsdag"))
        self.assertEqual(Turn.from_recent("hej", []), Turn("hej"))
        import os
        from unittest import mock
        with mock.patch.dict(os.environ, {"JARVIS_SPOKEN": turn.to_env()}):
            self.assertEqual(Turn.from_env(), turn)
        with mock.patch.dict(os.environ, {"JARVIS_SPOKEN": "not json"}):
            self.assertEqual(Turn.from_env(), Turn())


class MayWriteCalendarTest(unittest.TestCase):
    """Which questions stream their answer live (False) and which wait for the round's end (True)."""

    def test_questions_that_cannot_write_the_calendar_stream(self):
        for said, last in [("Har du några känslor? Vad tycker du om att du är en ESP32-panel?", ""),
                           ("Vad blir det för väder i morgon?", "Det är sju grader nu."),
                           ("Vem vann Allsvenskan 2024?", ""), ("What's the weather tomorrow?", ""),
                           ("Ja", "Det var allt för idag.")]:
            with self.subTest(said=said):
                self.assertFalse(may_write_calendar(Turn(said, last)))

    def test_questions_that_may_write_the_calendar_wait(self):
        for said, last in [("", ""), ("Lägg in tandläkaren på fredag klockan tio", ""),
                           ("Flytta mötet till tre", ""), ("Ta bort padel på lördag", ""),
                           ("Vad har jag i kalendern i morgon?", ""), ("Avboka middagen", ""),
                           ("Add lunch with Casey on Friday", ""), ("Ja", "Ska jag lägga in det?"),
                           ("Gärna", "Vill du att jag flyttar den till fyra?")]:
            with self.subTest(said=said):
                self.assertTrue(may_write_calendar(Turn(said, last)))


if __name__ == "__main__":
    unittest.main()
