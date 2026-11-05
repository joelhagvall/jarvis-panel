"""Jarvis's laundry tools hand the right arguments to ~/tvatt-booker/tvatt_jarvis.py and turn its JSON into
answers. A stand-in script replaces the real one, so no network and nothing is booked."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "agents"))
import agent_core  # noqa: E402
import laundry  # noqa: E402
from spoken_change import Turn  # noqa: E402

ASK = Turn("boka tvätt fredag 18")      # the stand-in question: Joel asks for the booking

ECHO = """import json, sys
argv = sys.argv[1:]
print("stderr line", file=sys.stderr)
if "--fail" in argv:
    print(json.dumps({"fel": "Inloggningen misslyckades"}))
    sys.exit(1)
if "--book" in argv and "Röd" in argv:
    print(json.dumps({"bokat": [], "fel": "Röd är inte ledig."}))
    sys.exit(1)
print(json.dumps({"argv": argv, "obs": "kan då bara boka en färg, eller ingen",
                  **({"bokat": ["Blå"]} if "--book" in argv else {})}))
"""


class LaundryToolTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        script = Path(self.tmp.name) / "tvatt_jarvis.py"
        script.write_text(ECHO)
        book = laundry.book
        patches = [mock.patch.object(laundry, "SCRIPT", script), mock.patch.object(laundry, "PYTHON", sys.executable),
                   # unless a test passes its own spoken Turn, the booking was asked for
                   mock.patch.object(laundry, "book", lambda **kw: book(**{"spoken": ASK, **kw}))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def argv(self, result):
        error, text = result
        self.assertFalse(error, text)
        return json.loads(text)["argv"]

    def test_read_arguments(self):
        self.assertEqual(self.argv(laundry.read()), [])
        self.assertEqual(self.argv(laundry.read(from_date="2026-11-05", to_date="2026-11-08")),
                         ["--from", "2026-11-05", "--to", "2026-11-08"])
        self.assertEqual(self.argv(laundry.read(date="2026-11-11", time=6)), ["--date", "2026-11-11", "--time", "6"])
        self.assertEqual(self.argv(laundry.read(date="2026-11-11")), ["--from", "2026-11-11", "--to", "2026-11-11"])

    def test_book_arguments(self):
        self.assertEqual(self.argv(laundry.book(date="2026-11-06", time=18, count=2, colours=["Blå", "Gul"])),
                         ["--book", "--date", "2026-11-06", "--time", "18", "--count", "2",
                          "--colour", "Blå", "--colour", "Gul"])

    def test_a_done_booking_leaves_out_the_bookers_obs(self):
        # Joel, 2026-11-01: "bara få en färg eller ingen" was read out; nobody understands it. Reads keep obs.
        self.assertNotIn("obs", json.loads(laundry.book(date="2026-11-06", time=18)[1]))
        self.assertIn("obs", json.loads(laundry.read()[1]))

    def test_errors_are_short_and_marked(self):
        error, text = laundry.book(date="2026-11-06", time=18, colours=["Röd"])
        self.assertTrue(error)
        self.assertIn("Röd är inte ledig", text)
        error, text = laundry.read(date="6 november", time=6)
        self.assertTrue(error and text.startswith("Ogiltigt datum"))
        error, text = laundry.book(date="2026-11-06")
        self.assertTrue(error and text.startswith("Ange datum"))

    def test_a_booking_needs_joels_words(self):
        # 2026-11-02 review: the booking is real, so Joel's own words must ask for it (spoken_change.py, whose
        # own tests cover the wording); a read-only or unrelated question is answered instead of booked.
        error, text = laundry.book(date="2026-11-06", time=18, spoken=Turn("finns det lediga tider på torsdag?"))
        self.assertTrue(error)
        self.assertIn("Ska jag boka", text)        # the refusal tells the model how to get a yes
        self.assertTrue(laundry.book(date="2026-11-06", time=18, spoken=None)[0])
        self.assertEqual(self.argv(laundry.book(date="2026-11-06", time=18, spoken=Turn("boka tvätt fredag 18-21")))[:2],
                         ["--book", "--date"])
        offer = Turn("ja", "Onsdag 21-24 är ledigt i tvättstugan. Ska jag boka det?")
        self.assertEqual(self.argv(laundry.book(date="2026-11-04", time=21, spoken=offer))[:1], ["--book"])
        follow_up = Turn("21 till 24", "Vilket pass vill du ha?", "Boka tvätt på onsdag")
        self.assertEqual(self.argv(laundry.book(date="2026-11-04", time=21, spoken=follow_up))[:1], ["--book"])

    def test_booking_refusals_never_start_the_booker(self):
        for spoken in (Turn("boka inte tvätt på fredag"), Turn("avboka tvätten på fredag"),
                       Turn("ja, vilka tvättider kan jag boka?", "Ska jag boka tvätt på fredag?"),
                       Turn("ja", "Jag kollade tvätten. Ska jag boka ett möte?")):
            with self.subTest(spoken=spoken), mock.patch.object(laundry, "_run") as run:
                error, _ = laundry.book(date="2026-11-06", time=18, spoken=spoken)
                self.assertTrue(error)
                run.assert_not_called()

    def test_a_missing_script_means_no_tools(self):
        with mock.patch.object(laundry, "SCRIPT", Path(self.tmp.name) / "missing.py"):
            self.assertFalse(laundry.configured())
        self.assertTrue(laundry.configured())


class LaundryInAgentTest(unittest.TestCase):
    def test_the_agent_hands_the_booking_this_questions_words(self):
        # 2026-11-02 review: the words came through module globals, set only in-process and never cleared, so
        # a command-mode agent refused every booking. Now each call gets the agent's own Turn, which a
        # command-mode agent reads from JARVIS_SPOKEN.
        turn = Turn("boka tvätt fredag 18", "Hej.", "")
        with mock.patch.dict(os.environ, {"JARVIS_SPOKEN": turn.to_env()}), \
                mock.patch.object(laundry, "configured", return_value=True), \
                mock.patch.object(laundry, "book", return_value=(False, "{}")) as book:
            agent = agent_core.ToolAgent()
            agent._init_tools(False, None, None, None)
            agent.home = None
            self.assertEqual(agent.spoken, turn)
            agent._ensure_tools()
            # a "spoken" argument from the model never replaces the question's own words
            agent.local_tools["tvatt_boka"]({"date": "2026-11-06", "time": 18, "spoken": Turn("injected")})
            self.assertIs(book.call_args.kwargs["spoken"], agent.spoken)

    def test_read_starts_early_and_booking_waits_and_is_guarded(self):
        self.assertIn("tvatt", agent_core.EARLY_TOOLS)
        self.assertNotIn("tvatt_boka", agent_core.EARLY_TOOLS)
        with tempfile.TemporaryDirectory() as tmp, \
                mock.patch.object(agent_core, "GUARD_FILE", Path(tmp) / "guard.json"):
            agent = agent_core.ToolAgent.__new__(agent_core.ToolAgent)
            args = {"date": "2026-11-06", "time": 18}
            self.assertIsNone(agent.duplicate_create("tvatt_boka", args))
            self.assertEqual(agent.duplicate_create("tvatt_boka", args), 0)
            self.assertIsNone(agent.duplicate_create("tvatt", args))


CALENDAR = """import json, sys
from pathlib import Path
argv = sys.argv[1:]
log = Path(__file__).with_name("calls.log")
with log.open("a") as f:    # a prefetch and a read may run side by side
    f.write(json.dumps(argv) + "\\n")
if (Path(__file__).with_name("fail")).exists():
    print(json.dumps({"fel": "Inloggningen misslyckades"}))
    sys.exit(1)
days = {f"2026-11-{d:02d}": {"veckodag": "x", "pass": ["06-09"]} for d in range(2, 16)}
print(json.dumps({"i_dag": "2026-11-02", "mina_bokningar": [{"datum": "2026-11-04", "pass": "21-24"}],
                  "lediga_pass": days, "ej_bokningsbara": {"2026-11-03": {"00-03": "Ej bokningsbar"}},
                  "obs": "ledigt", **({"bokat": ["Blå"]} if "--book" in argv else {})}))
"""
TODAY = laundry.Date(2026, 11, 2)


class LaundryPrefetchTest(unittest.TestCase):
    """2026-11-02: a laundry question waited on the model's first round (1.6-4.4 s) and then on the site's read
    (2.5-3 s), one after the other. The read now starts with the question; the stand-in logs each start."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        (self.dir / "tvatt_jarvis.py").write_text(CALENDAR)
        for p in (mock.patch.object(laundry, "SCRIPT", self.dir / "tvatt_jarvis.py"),
                  mock.patch.object(laundry, "PYTHON", sys.executable), mock.patch.object(laundry, "_prefetch", None)):
            p.start()
            self.addCleanup(p.stop)
        self.addCleanup(self.tmp.cleanup)

    def calls(self):
        log = self.dir / "calls.log"
        return [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []

    def test_a_laundry_question_reads_once_and_the_tool_cuts_its_days(self):
        self.assertTrue(laundry.prefetch("Vilka tvätttider finns det på onsdag?", today=TODAY))
        self.assertFalse(laundry.prefetch("Och på torsdag i tvättstugan?", today=TODAY))   # one is running
        error, text = laundry.read(from_date="2026-11-04", to_date="2026-11-04", today=TODAY)
        self.assertFalse(error, text)
        result = json.loads(text)
        self.assertEqual(list(result["lediga_pass"]), ["2026-11-04"])
        self.assertEqual(result["ej_bokningsbara"], {})
        self.assertEqual(result["mina_bokningar"], [{"datum": "2026-11-04", "pass": "21-24"}])   # all, as the script
        self.assertEqual(len(json.loads(laundry.read(today=TODAY)[1])["lediga_pass"]), 7)    # its default week
        self.assertEqual(self.calls(), [["--from", "2026-11-02", "--to", "2026-11-15"]])

    def test_what_the_prefetch_cannot_answer_reads_the_site(self):
        laundry.prefetch("tvättstugan imorgon", today=TODAY)
        laundry._prefetch["done"].wait(10)                                  # its log line first
        laundry.read(date="2026-11-04", time=6, today=TODAY)                # a pass's colours
        laundry.read(from_date="2026-11-10", to_date="2026-11-20", today=TODAY)   # past the 14 days
        self.assertEqual(self.calls()[1:], [["--date", "2026-11-04", "--time", "6"],
                                            ["--from", "2026-11-10", "--to", "2026-11-20"]])

    def test_other_questions_read_nothing(self):
        self.assertFalse(laundry.prefetch("Spela Coldplay på Spotify", today=TODAY))
        self.assertFalse(laundry.prefetch("", today=TODAY))
        self.assertEqual(self.calls(), [])

    def test_a_failed_stale_or_other_days_prefetch_is_not_used(self):
        (self.dir / "fail").write_text("")
        laundry.prefetch("tvätt", today=TODAY)
        self.assertTrue(laundry.read(today=TODAY)[0])                       # fell back and read again
        self.assertEqual(len(self.calls()), 2)
        (self.dir / "fail").unlink()
        self.assertTrue(laundry.prefetch("tvätt", today=TODAY))             # a failed one may be retried
        laundry._prefetch["done"].wait(10)
        with mock.patch.object(laundry, "PREFETCH_FRESH_S", -1):
            laundry.read(today=TODAY)
        laundry.read(today=laundry.Date(2026, 11, 3))                       # past midnight
        self.assertEqual(len(self.calls()), 5)

    def test_a_booking_waits_for_the_prefetch_and_drops_it(self):
        laundry.prefetch("boka tvätt fredag 18", today=TODAY)
        self.assertFalse(laundry.book(date="2026-11-06", time=18, spoken=ASK)[0])
        self.assertIsNone(laundry._prefetch)
        laundry.read(today=TODAY)
        calls = self.calls()
        self.assertEqual([c[:1] for c in calls], [["--from"], ["--book"], []])   # the read went to the site

    def test_the_agent_starts_it_with_the_question(self):
        with mock.patch.object(laundry, "prefetch") as prefetch:
            agent = agent_core.ToolAgent.__new__(agent_core.ToolAgent)
            agent._ensure_tools = lambda: None
            agent.local_tools = {"tvatt": None}
            agent.spoken = Turn("Hur är mina tvätttider?")
            agent._begin = mock.Mock(side_effect=RuntimeError("stop"))
            agent.screen = mock.Mock()
            with self.assertRaises(RuntimeError):
                agent.ask("prompt")
            prefetch.assert_called_once_with("Hur är mina tvätttider?")


class LaundryDirectReplyTest(unittest.TestCase):
    """2026-11-03: the round after a booking cost 2-3 s of a ~20 s question to say "Klart, onsdag 21-24."."""
    TODAY = laundry.Date(2026, 11, 3)        # a Tuesday

    @staticmethod
    def booked(day, slot, colours=("Blå",), **extra):
        return False, json.dumps({"datum": day, "pass": slot, "bokat": list(colours), **extra}, ensure_ascii=False)

    def say(self, results, said="boka tvätt", language=None):
        return laundry.done_reply(results, said, today=self.TODAY, language=language)

    def test_the_booked_days_and_passes(self):
        self.assertEqual(self.say([self.booked("2026-11-04", "21-24", ("Grön", "Gul"))]), "Klart, i morgon 21-24.")
        self.assertEqual(self.say([self.booked("2026-11-09", "21-24")]), "Klart, måndag 21-24.")
        self.assertEqual(self.say([self.booked("2026-11-03", "18-21")]), "Klart, i dag 18-21.")
        self.assertEqual(self.say([self.booked("2026-11-04", "21-24")], language="en"), "Done, tomorrow 21-24.")
        self.assertEqual(self.say([self.booked("2026-11-07", "12-15"), self.booked("2026-11-07", "09-12")]),
                         "Klart, lördag 09-12 och 12-15.")
        self.assertEqual(self.say([self.booked("2026-11-18", "06-09"), self.booked("2026-11-05", "18-21")]),
                         "Klart, torsdag 18-21 och onsdag 18 november 06-09.")

    def test_the_model_answers_a_failure_a_partial_booking_or_a_step_after_it(self):
        refused = (True, "Inte bokad ännu: Joels ord bad inte tydligt om bokningen.")
        partial = self.booked("2026-11-04", "21-24", ("Grön",), fel="Alla färger blev tagna under tiden.")
        for results in ([refused], [partial], [self.booked("2026-11-04", "21-24"), refused],
                        [(False, json.dumps({"bokat": [], "fel": "Passet går inte att boka"}))], [(False, "inte json")], []):
            with self.subTest(results=results):
                self.assertIsNone(self.say(results))
        for said in ("boka tvätt på onsdag och lägg in det i kalendern", "boka tvätt i morgon och pinga mig",
                     "boka tvätten och påminn mig en kvart innan", "book the laundry and send it to my phone"):
            with self.subTest(said=said):
                self.assertIsNone(self.say([self.booked("2026-11-04", "21-24")], said))

    def test_the_agent_says_it_without_another_round(self):
        class BookingBrain(agent_core.ToolAgent):
            label = "fake"

            def __init__(self, result, said):
                self._init_tools(False, None, None, None)
                self.spoken, self.result, self.rounds = Turn(said), result, 0

            def _ensure_tools(self):
                if self.tools is None:
                    self.tools = [{"name": "tvatt_boka"}]
                    self.local_tools = {"tvatt_boka": lambda args: self.result}
                    self.read_only = set()

            def _begin(self, prompt, instructions):
                return {}

            def _round(self, convo, on_text):
                self.rounds += 1
                if self.rounds == 1:
                    return agent_core.Round("", [agent_core.Call("1", "tvatt_boka", {"date": "2026-11-04", "time": 21})])
                if on_text:
                    on_text("Modellen svarar.")
                return agent_core.Round("Modellen svarar.", [])

            def _add_results(self, convo, r, results):
                pass

        done = self.booked("2026-11-04", "21-24")
        with mock.patch.object(laundry, "Date", mock.Mock(today=lambda: self.TODAY, fromisoformat=laundry.Date.fromisoformat)), \
                mock.patch.object(agent_core.ToolAgent, "duplicate_create", lambda *a: None):
            stream = []
            reply, _, used = BookingBrain(done, "boka tvätt onsdag 21").ask("q", on_text=stream.append)
            # the slow tool's sentence first, then the booking's answer, in one model round
            self.assertEqual(stream, ["Självklart, jag tittar på tvättstugan.", "\n", "Klart, i morgon 21-24."])
            self.assertEqual((reply, used["steps"]), ("Klart, i morgon 21-24.", 1))
            reply, _, used = BookingBrain(done, "boka tvätt onsdag 21 och lägg in i kalendern").ask("q", on_text=[].append)
            self.assertEqual((reply, used["steps"]), ("Modellen svarar.", 2))
            with mock.patch.object(agent_core, "DIRECT_REPLIES", False):
                self.assertEqual(BookingBrain(done, "boka tvätt").ask("q")[2]["steps"], 2)


class LaundryWordingTest(unittest.TestCase):
    def test_booking_answer_stays_short(self):
        # Joel, 2026-11-01: the reply about colours, the two-booking limit and the booker was internal noise.
        text = laundry.BOOK_TOOL["description"]
        self.assertIn("kort mening", text)
        self.assertNotIn("läs upp obs", text)
        # and no list of other free passes when he asked to book (Joel, 2026-11-01)
        self.assertIn("räkna inte upp andra lediga pass", text)
        self.assertIn("Räkna aldrig upp lediga pass som han inte bad om", laundry.TOOL["description"])
        # a sentence first, so the 15-20 s of a booking are not silent (Joel, 2026-11-01)
        self.assertIn("säg därför alltid först en kort mening", laundry.TOOL["description"])


if __name__ == "__main__":
    unittest.main()
