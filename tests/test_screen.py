#!/usr/bin/env python3
"""The cards Jarvis shows on the panel (server/agents/screen.py) and the question loop ending on them.

  python3 -m unittest tests/test_screen.py
"""
import sys
import tempfile
import unittest
from unittest import mock
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import agent_core  # noqa: E402
import screen  # noqa: E402
from agent_core import Call, Round  # noqa: E402

PLANE = {"rows": [{"icon": "airplane", "text": "SAS58E · Köpenhamn → Stockholm · 14 km söder"}]}
OUTING = {"title": "Moderna museet", "rows": [{"icon": "calendar", "text": "Söndag 25 oktober · 10:30–12:30"},
                                              {"icon": "subway-variant", "text": "Gå 09:58 · linje 10 avgår 10:03"}]}


class ScreenTest(unittest.TestCase):
    def test_two_cards_encoded_for_the_panel(self):
        s = screen.Screen()
        self.assertEqual(s.show({"summary": "SAS58E över dig nu · Moderna museet söndag 10:30 är bokat",
                                 "cards": [PLANE, OUTING]}),
                         (False, "Visas på panelen: sammanfattningen, 2 kort, 4 rader. Har du inte skrivit ditt "
                                 "talade svar än, skriv det nu."))
        lines = s.encoded().split("\n")
        self.assertTrue(lines[0].startswith("@"))                  # a new card always differs from the last
        self.assertEqual(lines[1:], [">SAS58E över dig nu · Moderna museet söndag 10:30 är bokat",
                                     "#",
                                     "\U000F001D||SAS58E · Köpenhamn → Stockholm · 14 km söder",
                                     "#Moderna museet",
                                     "\U000F00ED||Söndag 25 oktober · 10:30–12:30",
                                     "\U000F04DF|0A84FF|Gå 09:58 · linje 10 avgår 10:03"])

    def test_what_does_not_fit_is_cut_not_refused(self):
        s = screen.Screen()
        rows = [{"icon": "star", "text": f"Rad {n}"} for n in range(9)]
        error, text = s.show({"cards": [{"title": "Ett", "rows": rows}, PLANE, OUTING]})
        self.assertFalse(error)
        self.assertEqual([len(r) for _, r in s.cards], [4, 1])    # two cards at most; the second keeps its row
        four_planes = {"title": "Över dig nu", "rows": [{"icon": "airplane", "text": f"Plan {n}"} for n in range(4)]}
        s.show({"cards": [four_planes, OUTING]})
        self.assertEqual([(t, len(r)) for t, r in s.cards], [("Över dig nu", 3), ("Moderna museet", 1)])

    def test_unknown_icon_separators_and_long_text(self):
        s = screen.Screen()
        s.show({"cards": [{"rows": [{"icon": "unicorn", "text": "a|b\nc " + "x" * 80}]}]})
        icon, text = s.cards[0][1][0]
        self.assertEqual(icon, screen.FALLBACK)
        self.assertTrue(text.startswith("a/b c "))
        self.assertEqual(len(text), screen.MAX_TEXT)
        self.assertTrue(text.endswith("…"))

    def test_nothing_to_show(self):
        s = screen.Screen()
        self.assertTrue(s.show({"cards": [{"title": "Tomt", "rows": []}]})[0])
        self.assertEqual(s.encoded(), "")

    def test_a_summary_alone(self):
        s = screen.Screen()
        self.assertFalse(s.show({"summary": "Tre saker i morgon · första 09:30"})[0])
        self.assertEqual(s.encoded().split("\n")[1:], [">Tre saker i morgon · första 09:30"])

    def test_news_headlines_become_a_card_when_the_model_shows_none(self):
        # 2026-11-03: long news answers were rolling text; the model called skarm for 2 of 18 long answers
        s = screen.Screen()
        s.offer_news("SVT Nyheter", [f"Rubrik {n}" for n in range(8)])
        glyph = chr(int(screen.ICONS["newspaper-variant"], 16))
        self.assertEqual(s.encoded().split("\n")[1:],
                         ["#SVT Nyheter"] + [f"{glyph}||Rubrik {n}" for n in range(5)])   # title + 5 rows fit
        s.offer_news("SVT Sport", ["Mål i derbyt"])                                         # two sources share
        self.assertEqual([l for l in s.encoded().split("\n") if l.startswith("#")], ["#SVT Nyheter", "#SVT Sport"])
        s.clear()
        self.assertEqual(s.encoded(), "")                                                   # a new question

    def test_the_models_own_screen_wins_over_the_news_card(self):
        s = screen.Screen()
        s.offer_news("SVT Nyheter", ["Rubrik"])
        s.show({"summary": "Luften är bra · tre nyheter"})
        self.assertEqual(s.encoded().split("\n")[1:], [">Luften är bra · tre nyheter"])

    def test_the_news_tool_offers_its_headlines(self):
        import lookup
        items = [{"title": f"Rubrik {n}", "summary": "", "when": None} for n in range(4)]
        got = []
        with mock.patch.object(lookup, "_items", return_value=items):
            failed, text = lookup.news("svt", 3, shown=lambda name, titles: got.append((name, titles)))
        self.assertFalse(failed)
        self.assertEqual(got, [("SVT Nyheter", ["Rubrik 0", "Rubrik 1", "Rubrik 2"])])
        import openai_agent
        brain = openai_agent.LunaAgent(key="synthetic", mock_tools=True)
        brain._ensure_tools()
        with mock.patch.object(lookup, "_items", return_value=items):
            brain.local_tools["nyheter"]({"source": "svt", "count": 2})                   # the agent's own wiring
        self.assertIn("#SVT Nyheter", brain.screen.encoded())

    def test_every_icon_is_a_glyph_the_panel_can_compile(self):
        for name, code in screen.ICONS.items():
            self.assertTrue(0xF0000 <= int(code, 16) <= 0xFFFFF, name)
        self.assertIn(screen.FALLBACK, screen.ICONS)
        self.assertLessEqual(set(screen.COLORS), set(screen.ICONS))


class ScreenBrain(agent_core.ToolAgent):
    """Says what it checks, then answers and shows the cards in the same round."""
    label = "fake"

    def __init__(self):
        self._init_tools(False, None, None, None)

    def _ensure_tools(self):
        if self.tools is None:
            self.tools = [{"name": "flygplan"}, screen.TOOL]
            self.local_tools = {"flygplan": lambda args: (False, "SAS58E"), "skarm": self.screen.show}
            self.read_only = set(self.local_tools)

    def _begin(self, prompt, instructions):
        return {"rounds": 0}

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            on_text("Genast. Jag kollar flyget.")
            return Round("Genast. Jag kollar flyget.", [Call("1", "flygplan", {})])
        if convo["rounds"] == 2:
            on_text("Planet är SAS58E från Köpenhamn.")
            return Round("Planet är SAS58E från Köpenhamn.", [Call("2", "skarm", {"cards": [PLANE]})])
        raise AssertionError("asked the model again after the screen")

    def _add_results(self, convo, r, results):
        pass


class SilentScreenBrain(ScreenBrain):
    """Shows the cards first without a word, and answers once they are confirmed."""

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            on_text("Genast. Jag kollar flyget.")
            return Round("Genast. Jag kollar flyget.", [Call("1", "flygplan", {})])
        if convo["rounds"] == 2:
            return Round("", [Call("2", "skarm", {"summary": "SAS58E över dig", "cards": [PLANE]})])
        on_text("Planet är SAS58E från Köpenhamn.")
        return Round("Planet är SAS58E från Köpenhamn.", [])


class FailedBookingBrain(ScreenBrain):
    """Books, sends and shows the card in one step; the booking fails."""

    def _ensure_tools(self):
        if self.tools is None:
            self.tools = [{"name": "add_event"}, {"name": "till_telefonen"}, screen.TOOL]
            self.local_tools = {"add_event": lambda args: (True, "iCloud svarade inte."),
                                "till_telefonen": lambda args: (False, "Skickat."), "skarm": self.screen.show}
            self.read_only = set(self.local_tools)

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            return Round("", [Call("1", "add_event", {"title": "Utflykt"}), Call("2", "till_telefonen", {}),
                              Call("3", "skarm", {"summary": "Moderna museet är bokat", "cards": [OUTING]})])
        self.told = convo["results"]
        on_text("Det gick inte att boka.")
        return Round("Det gick inte att boka.", [])

    def _add_results(self, convo, r, results):
        convo["results"] = results


class PlanOnceBrain(FailedBookingBrain):
    """Books and sends the event to the phone without writing the plan twice."""

    def _ensure_tools(self):
        if self.tools is None:
            self.sent = []
            self.tools = [{"name": "add_event"}, {"name": "till_telefonen"}]
            self.local_tools = {"add_event": lambda args: (False, "Lagt till."),
                                "till_telefonen": lambda args: (self.sent.append(args), (False, "Skickat."))[1]}
            self.read_only = set()

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            return Round("", [Call("1", "add_event", {"title": "Utflykt", "notes": "• Besök 12:00"}),
                              Call("2", "till_telefonen", {"from_calendar": True})])
        return Round("Klart.", [])


class BookThenSendBrain(PlanOnceBrain):
    """Books in one round and sends the event to the phone in the next, as Luna did."""

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            return Round("", [Call("1", "add_event", {"title": "Utflykt", "notes": "🏛 Besök 12:00"})])
        if convo["rounds"] == 2:
            return Round("", [Call("2", "till_telefonen", {"from_calendar": True})])
        return Round("Klart.", [])


class LoopTest(unittest.TestCase):
    def test_the_phone_gets_an_event_booked_in_an_earlier_round(self):
        brain = BookThenSendBrain()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(agent_core, "GUARD_FILE", Path(tmp) / "g.json"):
            brain.ask("Boka och skicka")
            self.assertEqual(brain.sent[0]["event"], {"title": "Utflykt", "notes": "🏛 Besök 12:00"})
            def send_only(convo, on_text):                 # the next question only sends
                convo["rounds"] += 1
                calls = [Call("3", "till_telefonen", {"from_calendar": True})] if convo["rounds"] == 1 else []
                return Round("" if calls else "Klart.", calls)
            brain._round = send_only
            brain.ask("Skicka det igen")                # a new question starts without the old event
            self.assertNotIn("event", brain.sent[-1])

    def test_the_phone_gets_the_event_added_in_the_same_step(self):
        brain = PlanOnceBrain()
        brain.ask("Boka och skicka")
        self.assertEqual(brain.sent, [{"from_calendar": True, "event": {"title": "Utflykt", "notes": "• Besök 12:00"}}])

    def test_a_card_shown_with_a_failed_booking_is_taken_back(self):
        brain = FailedBookingBrain()
        reply, _, used = brain.ask("Boka", on_text=[].append)
        self.assertEqual(reply, "Det gick inte att boka.")
        self.assertEqual(brain.screen.encoded(), "")                        # "bokat" never reaches the panel
        self.assertTrue(brain.told[2][0])
        self.assertIn("add_event misslyckades", brain.told[2][1])
        self.assertEqual(brain.told[1], (False, "Skickat."))

    def test_the_screen_without_an_answer_does_not_end_the_question(self):
        brain, stream = SilentScreenBrain(), []
        reply, _, used = brain.ask("Vilket plan?", on_text=stream.append)
        self.assertEqual(reply, "Planet är SAS58E från Köpenhamn.")
        self.assertIn("Planet är SAS58E från Köpenhamn.", "".join(stream))
        self.assertEqual(used["steps"], 3)
        self.assertIn("SAS58E över dig", brain.screen.encoded())

    def test_the_screen_with_the_answer_ends_the_question(self):
        brain, stream = ScreenBrain(), []
        reply, _, used = brain.ask("Vilket plan?", on_text=stream.append)
        self.assertEqual("".join(stream), "Genast. Jag kollar flyget.\nPlanet är SAS58E från Köpenhamn.")
        self.assertEqual(reply, "Planet är SAS58E från Köpenhamn.")
        self.assertEqual(used["steps"], 2)
        self.assertIn("SAS58E · Köpenhamn", brain.screen.encoded())
        self.assertEqual([t["name"] for t in brain.last_tools], ["flygplan", "skarm"])
        brain.ask("Vilket plan?", on_text=[].append)
        brain._round = lambda convo, on_text: Round("Hej.", [])
        brain.ask("Hej")
        self.assertEqual(brain.screen.encoded(), "")                # each question starts without cards


if __name__ == "__main__":
    unittest.main()
