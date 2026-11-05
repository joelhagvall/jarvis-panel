#!/usr/bin/env python3
"""The question loop every brain shares (server/agents/agent_core.py), with a fake brain: the tools of
one round run at the same time, a call with unusable arguments gets its problem back without running,
and the rounds are counted. A fake calendar-mcp: without an allow-list voice gets only CALENDAR_TOOLS.

  python3 -m unittest tests/test_agent_core.py
"""
import os
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock
from pathlib import Path
from types import SimpleNamespace as NS
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import agent_core  # noqa: E402
from agent_core import Call, Round  # noqa: E402


class FakeBrain(agent_core.ToolAgent):
    """Round 1 asks for three tools, one with bad arguments; round 2 answers with what it got back."""
    label = "fake"

    def __init__(self):
        self._init_tools(False, None, None, None)
        self.ran, self.lock = [], threading.Lock()

    def _ensure_tools(self):
        if self.tools is None:
            slow = lambda name: (lambda args: (time.sleep(0.3), self.ran.append(name), (False, f"{name} ok"))[2])
            self.tools = [{"name": n} for n in ("flygplan", "iss", "wikipedia")]
            self.local_tools = {n: slow(n) for n in ("flygplan", "iss", "wikipedia")}
            self.read_only = set(self.local_tools)

    def _begin(self, prompt, instructions):
        return {"rounds": 0, "results": None}

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            return Round("", [Call("1", "flygplan", {}), Call("2", "iss", {}),
                              Call("3", "wikipedia", None, "Ogiltiga argument till wikipedia")],
                         cost=0.001, usage={"input_tokens": 100, "output_tokens": 10})
        answer = "Klart. " + ", ".join(text for _, text in convo["results"])
        on_text(answer)
        return Round(answer, [], cost=0.002, usage={"input_tokens": 200, "output_tokens": 20, "cached_tokens": 50})

    def _add_results(self, convo, r, results):
        convo["results"] = results


class PreambleBrain(FakeBrain):
    """Says what it will look up in the round that asks for the tools, then answers."""

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            on_text("Jag kollar flyget och ISS.")
            return Round("Jag kollar flyget och ISS.", [Call("1", "flygplan", {}), Call("2", "iss", {}),
                                                         Call("3", "wikipedia", None, "Ogiltiga argument")])
        on_text("Planet är ett SAS-plan.")
        return Round("Planet är ett SAS-plan.", [])


class LaundryBrain(FakeBrain):
    """Calls the slow laundry tool without a sentence first (Joel, 2026-11-01: 17 s of silence), then answers.
    early: the brain reports the call as written (_ready), as the real brains do, so it starts at once."""

    def __init__(self, early=False):
        super().__init__()
        self.early_call = early

    def _ensure_tools(self):
        if self.tools is None:
            self.tools = [{"name": "tvatt"}]
            self.local_tools = {"tvatt": lambda args: (False, "lediga pass")}
            self.read_only = set(self.local_tools)

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            call = Call("1", "tvatt", {})
            if self.early_call:
                self._ready(call)
            return Round("", [call])
        on_text("Klart, onsdag 21-24.")
        return Round("Klart, onsdag 21-24.", [])


class NoteBrain(FakeBrain):
    """Says what it checks, writes a note before its next tools, then answers (or, with answer "", nothing)."""

    def __init__(self, answer="Moderna museet ligger i iCloud-kalendern."):
        super().__init__()
        self.answer = answer

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        text, calls = {1: ("Genast. Jag kollar vädret.", [Call("1", "flygplan", {})]),
                       2: ("Besök 10:30–12:30, innan regnet.", [Call("2", "iss", {})]),
                       3: (self.answer, [])}[convo["rounds"]]
        for i in range(0, len(text), 5) if on_text else ():
            on_text(text[i:i + 5])
        return Round(text, calls)


class EarlyBrain(FakeBrain):
    """Round 1 says what it checks and writes a slow lookup, a write and another lookup, taking 0.3 s to write
    the rest after the first; a call the answer then drops is reported too. Round 2 answers."""

    def __init__(self):
        super().__init__()
        self.events = []

    def _ensure_tools(self):
        if self.tools is None:
            def tool(name, seconds):
                def run(args):
                    self.events.append(("start", name, round(time.monotonic() - self.t0, 1)))
                    time.sleep(seconds)
                    return False, f"{name} ok"
                return run
            self.local_tools = {"webbsok": tool("webbsok", 0.5), "flygplan": tool("flygplan", 0.1),
                                "till_telefonen": tool("till_telefonen", 0.1), "iss": tool("iss", 0.1)}
            self.tools = [{"name": n} for n in self.local_tools]
            self.read_only = set(self.local_tools)

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            on_text("Genast. Jag kollar.")
            self._ready(Call("1", "webbsok", {}))
            self._ready(Call("2", "till_telefonen", {}))     # sends: waits for the whole answer
            self._ready(Call("x", "iss", {}))                # written, then dropped from the answer
            time.sleep(0.3)
            self.events.append(("round end", round(time.monotonic() - self.t0, 1)))
            return Round("Genast. Jag kollar.", [Call("1", "webbsok", {}), Call("2", "till_telefonen", {}),
                                                 Call("3", "flygplan", {})])
        on_text("Klart.")
        return Round("Klart.", [])


class RadioBrain(FakeBrain):
    """Asks for the radio, with or without a sentence first; round 2 (if any) phrases what came back."""

    def __init__(self, args, result=(False, "Home Assistant har startat P4 Stockholm på panelens högtalare."), text=""):
        super().__init__()
        self.args, self.result, self.text = args, result, text

    def _ensure_tools(self):
        if self.tools is None:
            self.tools = [{"name": "radio"}]
            self.local_tools = {"radio": lambda args: self.result}
            self.read_only = {"radio"}

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo["rounds"] == 1:
            if self.text and on_text:
                on_text(self.text)
            return Round(self.text, [Call("1", "radio", self.args)], usage={"output_tokens": 20})
        answer = "Modellen svarar: " + convo["results"][0][1]
        if on_text:
            on_text(answer)
        return Round(answer, [], usage={"output_tokens": 10})


class SearchForeverBrain(FakeBrain):
    """Keeps searching while it may; answers only when the last round allows no tools."""

    def _round(self, convo, on_text):
        convo["rounds"] += 1
        if convo.get("no_tools"):
            self.told = convo["results"]
            on_text("Det här hittade jag.")
            return Round("Det här hittade jag.", [])
        return Round("", [Call(str(convo["rounds"]), "wikipedia", {})])


class AgentCoreTest(unittest.TestCase):
    def test_the_last_round_answers_without_tools(self):
        brain, stream = SearchForeverBrain(), []
        reply, _, used = brain.ask("Hitta något oväntat", on_text=stream.append)
        self.assertEqual(reply, "Det här hittade jag.")
        self.assertEqual(used["steps"], agent_core.MAX_STEPS)
        self.assertEqual(len(brain.last_tools), agent_core.MAX_STEPS - 1)
        self.assertTrue(brain.told[-1][1].endswith(agent_core.LAST_ROUND))   # told before its last round


    def test_lookups_start_as_soon_as_their_call_is_written(self):
        brain, stream = EarlyBrain(), []
        brain.t0 = time.monotonic()
        reply, _, used = brain.ask("Kolla", on_text=stream.append, on_tools=lambda names: stream.append(names))
        took = time.monotonic() - brain.t0
        self.assertEqual(reply, "Klart.")
        self.assertLess(took, 0.75)                     # 0.5 s search from the start, not 0.3 + 0.5 s
        self.assertIn(("start", "webbsok", 0.0), brain.events)
        self.assertIn(("start", "till_telefonen", 0.3), brain.events)               # only after the answer
        self.assertIn(("start", "flygplan", 0.3), brain.events)                     # never reported early
        # Lookups start early, but speech waits until the round has declared all tools.
        self.assertEqual(stream, ["Genast. Jag kollar.", "\n", ["webbsok", "till_telefonen", "flygplan"], "Klart."])
        self.assertEqual(used["steps"], 2)
        # the history keeps how much of each lookup ran while the model still wrote (docs/voice-latency.md)
        tools = {t["name"]: t for t in brain.last_tools}
        self.assertTrue(250 <= tools["webbsok"]["early_ms"] <= 400, tools["webbsok"])
        self.assertLess(tools["webbsok"]["at_ms"], 100)
        self.assertNotIn("early_ms", tools["till_telefonen"])
        self.assertNotIn("early_ms", tools["flygplan"])
        self.assertEqual(tools["webbsok"]["result"], "webbsok ok")     # what the model was told, for the history


    def test_outing_weather_is_bound_to_visit_not_travel_or_current_day(self):
        plan = {"visit": {"from": "2026-10-24T11:00:00+02:00", "to": "2026-10-24T14:00:00+02:00"},
                "leave_origin_at": "2026-10-24T10:13:00+02:00", "back_at_origin": "2026-10-24T14:35:00+02:00"}
        home = NS(forecast=mock.Mock(return_value=(False, '{"days":[{"date":"2026-10-24"}]}')))
        with mock.patch.object(agent_core.journey, "plan", return_value=(False, agent_core.json.dumps(plan))):
            error, text = FakeBrain()._plan_journey({}, home)
        self.assertFalse(error)
        home.forecast.assert_called_once_with(from_date="2026-10-24", to_date="2026-10-24",
                                              from_time="11:00", to_time="14:00")
        weather = agent_core.json.loads(text)["visit_weather"]
        self.assertTrue(weather["verified"])
        self.assertEqual(weather["visit_from"], plan["visit"]["from"])

    def test_missing_visit_weather_is_not_reported_as_verified(self):
        plan = {"visit": {"from": "2026-10-24T11:00:00+02:00", "to": "2026-10-24T14:00:00+02:00"}}
        home = NS(forecast=mock.Mock(return_value=(True, "SMHI svarade inte")))
        with mock.patch.object(agent_core.journey, "plan", return_value=(False, agent_core.json.dumps(plan))):
            error, text = FakeBrain()._plan_journey({}, home)
        self.assertTrue(error)
        self.assertFalse(agent_core.json.loads(text)["visit_weather"]["verified"])

    def test_loop(self):
        brain, said = FakeBrain(), []
        t0 = time.monotonic()
        reply, cost, used = brain.ask("Vilket plan och var är ISS?", on_text=said.append)
        took = time.monotonic() - t0
        self.assertLess(took, 0.55)                                   # two 0.3 s tools at the same time
        self.assertEqual(sorted(brain.ran), ["flygplan", "iss"])      # the bad call never ran
        # The server's sentence as the tools start (TOOL_NOTICES), then the answer; the model's own text: none
        self.assertEqual(said, ["Jag kollar himlen.", "\n", "Klart. flygplan ok, iss ok, Ogiltiga argument till wikipedia"])
        self.assertEqual(reply, "Klart. flygplan ok, iss ok, Ogiltiga argument till wikipedia")
        self.assertEqual(cost, 0.003)
        self.assertEqual(used, {"input_tokens": 300, "output_tokens": 30, "cached_tokens": 50,
                                "cache_write_tokens": 0, "steps": 2})
        self.assertEqual(sorted(t["name"] for t in brain.last_tools), ["flygplan", "iss"])

    def test_sentence_before_the_tools_ends_its_line_and_tools_are_announced(self):
        brain, stream = PreambleBrain(), []
        reply, _, used = brain.ask("Vilket plan och var är ISS?", on_text=stream.append,
                                   on_tools=lambda names: stream.append(("tools", names)))
        self.assertEqual(stream, ["Jag kollar flyget och ISS.", "\n", ("tools", ["flygplan", "iss"]),
                                  "Planet är ett SAS-plan."])     # the bad call is not announced
        self.assertEqual(reply, "Planet är ett SAS-plan.")        # the history keeps only the answer
        self.assertEqual(used["steps"], 2)

    def test_calendar_write_drops_draft_even_after_an_early_lookup(self):
        for name in agent_core.CALENDAR_WRITE_TOOLS:
            for error in (False, True):
                with self.subTest(tool=name, error=error):
                    brain, spoken = FakeBrain(), []
                    brain._ensure_tools()
                    brain.local_tools["iss"] = lambda args: (False, "lookup")

                    notice = agent_core.TOOL_NOTICES[name][0]
                    def write(args):
                        # Before the write only the server's fixed sentence, never the model's draft or a "Klart"
                        self.assertEqual(spoken, [notice, "\n"])
                        return error, "calendar result"

                    brain.local_tools[name] = write
                    answer = "Ändringen misslyckades." if error else "Klart."

                    def model_round(convo, on_text):
                        if convo["results"] is None:
                            draft = "Jag behöver först välja en kalender."
                            on_text(draft)
                            lookup = Call("1", "iss", {})
                            brain._ready(lookup)
                            return Round(draft, [lookup, Call("2", name, {})])
                        on_text(answer)
                        return Round(answer, [])

                    brain._round = model_round
                    reply, _, _ = brain.ask("Ändra kalendern", on_text=spoken.append)
                    self.assertEqual(spoken, [notice, "\n", answer])
                    self.assertEqual(reply, answer)

    def test_an_answer_streams_as_it_is_written_when_no_calendar_write_is_in_sight(self):
        """2026-11-04: a 118-token answer waited 1.5 s for its last word before the voice could start."""
        brain, spoken = FakeBrain(), []
        brain.spoken = agent_core.spoken_change.Turn("Har du några känslor?")

        def model_round(convo, on_text):
            on_text("Inte som du. ")
            spoken.append("model still writing")        # the first sentence was handed on before this
            on_text("Men jag gillar bokhyllan.")
            return Round("Inte som du. Men jag gillar bokhyllan.", [])

        brain._round = model_round
        reply, _, _ = brain.ask("prompt", on_text=spoken.append)
        self.assertEqual(spoken, ["Inte som du. ", "model still writing", "Men jag gillar bokhyllan."])
        self.assertEqual(reply, "Inte som du. Men jag gillar bokhyllan.")

    def test_a_streamed_sentence_before_tools_is_said_once(self):
        brain, stream = PreambleBrain(), []
        brain.spoken = agent_core.spoken_change.Turn("Vilket plan och var är ISS?")
        reply, _, _ = brain.ask("prompt", on_text=stream.append, on_tools=lambda names: stream.append(("tools", names)))
        self.assertEqual(stream, ["Jag kollar flyget och ISS.", "\n", ("tools", ["flygplan", "iss"]),
                                  "Planet är ett SAS-plan."])
        self.assertEqual(reply, "Planet är ett SAS-plan.")

    def test_a_calendar_question_still_waits_and_drops_the_draft(self):
        brain, spoken = FakeBrain(), []
        brain._ensure_tools()
        brain.spoken = agent_core.spoken_change.Turn("Lägg in tandläkaren på fredag klockan tio")
        brain.local_tools["add_event"] = lambda args: (False, "added")

        def model_round(convo, on_text):
            if convo["results"] is None:
                on_text("Jag lägger in tandläkaren fredag tio.")
                return Round("Jag lägger in tandläkaren fredag tio.", [Call("1", "add_event", {})])
            on_text("Klart.")
            return Round("Klart.", [])

        brain._round = model_round
        brain.ask("prompt", on_text=spoken.append)
        self.assertEqual(spoken, [agent_core.TOOL_NOTICES["add_event"][0], "\n", "Klart."])

    def test_a_tool_round_says_one_sentence_and_never_claims_it_is_done(self):
        Call_ = agent_core.Call
        notice = agent_core.ToolAgent._notice if hasattr(agent_core, "ToolAgent") else FakeBrain._notice
        self.assertEqual(notice([Call_("1", "resa", {})]), ("Jag kollar resan.", "Let me check the trip."))
        self.assertEqual(notice([Call_("1", "agenda", {}), Call_("2", "show_event", {})])[0], "Jag kollar kalendern.")
        self.assertEqual(notice([Call_("1", "resa", {}), Call_("2", "vader", {})]), agent_core.NOTICE)
        self.assertEqual(notice([Call_("1", "paminnelse", {})]), agent_core.NOTICE)
        self.assertEqual(notice([Call_("1", "power", {})])[0], "Jag fixar det på tv:n.")   # the tv skill's names
        self.assertTrue({n for n in agent_core.TV_TOOLS.split(",")} <= set(agent_core.TOOL_NOTICES))
        self.assertEqual(notice([Call_("1", "agenda", {}), Call_("2", "add_event", {})])[0], "Jag lägger in det.")
        self.assertEqual(notice([Call_("1", "vader", {}), Call_("2", "tvatt", {})]), agent_core.SLOW_NOTICES["tvatt"])
        for direct in agent_core.DIRECT_TOOLS:                   # their result is the answer, said at once
            self.assertIsNone(notice([Call_("1", direct, {})]))
        self.assertIsNone(notice([Call_("1", next(iter(agent_core.FINAL_TOOLS)), {})]))
        for sv, en in [*agent_core.TOOL_NOTICES.values(), agent_core.NOTICE]:
            self.assertFalse(any(w in (sv + " " + en).lower() for w in ("klart", "done", "bokat", "inlagt", "added")))

    def test_slow_laundry_tool_gets_a_sentence_when_the_model_writes_none(self):
        for early in (False, True):
            brain, stream = LaundryBrain(early), []
            reply, _, _ = brain.ask("Boka tvätt på onsdag", on_text=stream.append, on_tools=lambda n: stream.append(n))
            self.assertEqual(stream, ["Självklart, jag tittar på tvättstugan.", "\n", ["tvatt"], "Klart, onsdag 21-24."])
            self.assertEqual(reply, "Klart, onsdag 21-24.")
            self.assertEqual(brain.last_tools[0]["args"], "{}")     # history.db keeps what each tool was asked
        brain, stream = LaundryBrain(), []
        brain.ask("Book the laundry", on_text=stream.append, language="en")
        self.assertEqual(stream[0], "Of course, I'm checking the laundry room.")

    def test_a_note_before_later_tools_is_not_spoken(self):
        stream = []
        reply, _, used = NoteBrain().ask("Boka något på söndag", on_text=stream.append)
        self.assertEqual("".join(stream), "Genast. Jag kollar vädret.\nModerna museet ligger i iCloud-kalendern.")
        self.assertEqual(reply, "Moderna museet ligger i iCloud-kalendern.")
        self.assertEqual(used["steps"], 3)

    def test_the_note_is_spoken_when_nothing_follows_the_last_tools(self):
        stream = []
        reply, _, _ = NoteBrain(answer="").ask("Boka något på söndag", on_text=stream.append)
        self.assertEqual("".join(stream), "Genast. Jag kollar vädret.\nBesök 10:30–12:30, innan regnet.")
        self.assertEqual(reply, "Besök 10:30–12:30, innan regnet.")

    def test_the_radios_result_is_spoken_without_another_round(self):
        said, tools = [], []
        reply, _, used = RadioBrain({"action": "play", "channel": "P4"}).ask(
            "Sätt på P4", on_text=said.append, on_tools=tools.append)
        self.assertEqual((reply, used["steps"], said, tools), ("Spelar P4 Stockholm.", 1, ["Spelar P4 Stockholm."], [["radio"]]))
        reply, _, used = RadioBrain({"action": "stop"}, (False, "Radion på panelen är stoppad.")).ask(
            "Stäng av radion", on_text=[].append, language="en")
        self.assertEqual((reply, used["steps"]), ("The radio is off.", 1))
        reply, _, _ = RadioBrain({"action": "stop"}, (False, "Radion på panelen är stoppad.")).ask("Stäng av radion")
        self.assertEqual(reply, "Radion är av.")

    def test_the_model_phrases_a_radio_failure_what_is_on_and_a_round_with_text(self):
        failed = (True, "Home Assistant svarade inte (URLError).")
        for brain in (RadioBrain({"action": "play"}, failed),
                      RadioBrain({"action": "now"}, (False, "P3: Morgonpasset. Låt: ...")),
                      RadioBrain({"action": "play"}, text="Genast.")):
            reply, _, used = brain.ask("Radion", on_text=[].append)
            self.assertEqual(used["steps"], 2, reply)
            self.assertTrue(reply.startswith("Modellen svarar") or reply.startswith("Genast."), reply)
        with mock.patch.object(agent_core, "DIRECT_REPLIES", False):
            reply, _, used = RadioBrain({"action": "stop"}, (False, "Radion på panelen är stoppad.")).ask("Stäng av")
        self.assertEqual((used["steps"], reply), (2, "Modellen svarar: Radion på panelen är stoppad."))

    def test_direct_reply_only_for_a_radio_that_went_through(self):
        self.assertEqual(agent_core.direct_reply("radio", {}, False, "Home Assistant har startat P1 på panelens högtalare."),
                         "Spelar P1.")
        self.assertEqual(agent_core.direct_reply("radio", {}, False, "Jarvis har startat P4 Stockholm på JBL Charge 4."),
                         "Spelar P4 Stockholm.")
        self.assertEqual(agent_core.direct_reply("radio", {}, False, "Jarvis har startat P3 på JBL Charge 4.", "en"),
                         "Playing P3.")
        self.assertIsNone(agent_core.direct_reply("radio", {}, False, "Something else entirely."))
        self.assertIsNone(agent_core.direct_reply("radio", {"action": "stop"}, True, "Home Assistant svarade 500 på media_stop."))
        self.assertIsNone(agent_core.direct_reply("paminnelse", {}, False, "Påminnelse 21:24: kolla panelen"))

    def test_the_volume_level_is_spoken_without_another_round(self):
        self.assertEqual(agent_core.direct_reply("volym", {"action": "up"}, False, "Panelen: 40 %."),
                         "Panelen står på 40 procent.")
        self.assertEqual(agent_core.direct_reply("volym", {"action": "set"}, False, "JBL Charge 4: 25 %.", "en"),
                         "JBL Charge 4 is at 25 percent.")
        self.assertEqual(agent_core.direct_reply("volym", {}, False, "Panelen: 0 %.", "en"), "The panel is at 0 percent.")
        self.assertIsNone(agent_core.direct_reply("volym", {}, True, "Ingen Bluetooth-högtalare är kopplad."))
        self.assertIsNone(agent_core.direct_reply("volym", {}, False, "Vilken nivå? Säg till exempel 40 procent."))

    def test_a_command_runs_its_one_tool_without_the_model(self):
        brain = RadioBrain({"action": "stop"}, (False, "Radion är stoppad."))
        brain._round = lambda convo, on_text: self.fail("a command must not ask the model")
        self.assertEqual(brain.command("radio", {"action": "stop"}), "Radion är av.")
        self.assertEqual([t["name"] for t in brain.last_tools], ["radio"])
        brain.result = (True, "Home Assistant svarade inte (URLError).")
        self.assertEqual(brain.command("radio", {"action": "stop"}), "Home Assistant svarade inte (URLError).")

    def test_each_round_is_timed_for_the_history(self):
        brain = FakeBrain()
        brain.ask("Vilket plan?", on_text=[].append)
        self.assertEqual([(r["tools"], r["out"], r["first_ms"] is None) for r in brain.last_rounds],
                         [(3, 10, True), (0, 20, False)])
        self.assertTrue(all(r["ms"] >= 0 for r in brain.last_rounds))

    def test_a_question_waits_briefly_for_a_running_warm_up_and_not_for_a_slow_one(self):
        brain = FakeBrain()
        brain._ensure_tools()
        brain.home = None
        release = threading.Event()

        def slow_calendar():
            release.wait(2)
            brain.agenda = (time.monotonic(), "Calendar snapshot")
        brain._warm_snapshot = slow_calendar
        brain._warm_api_independent = mock.Mock()
        self.assertEqual(brain.wait_for_snapshot(0.4), 0.0)              # nothing running: no wait
        self.assertTrue(brain.start_warm("prompt", threading.Lock()))
        time.sleep(0.05)
        t0 = time.monotonic()
        self.assertGreaterEqual(brain.wait_for_snapshot(0.1), 0.09)       # still reading: waits the bound, no more
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertIsNone(brain.fresh_agenda())
        threading.Timer(0.1, release.set).start()
        waited = brain.wait_for_snapshot(1.0)                            # done within the bound: returns at once
        self.assertLess(waited, 0.6)
        self.assertEqual(brain.fresh_agenda()[0], "Calendar snapshot")
        self.assertTrue(brain._warm_inflight.acquire(timeout=2))
        brain._warm_inflight.release()
        self.assertEqual((brain._warm_jobs, brain.wait_for_snapshot(0.4)), ((), 0.0))

    def test_without_a_stream_the_reply_is_the_last_text(self):
        reply, _, _ = NoteBrain().ask("Boka något på söndag")
        self.assertEqual(reply, "Moderna museet ligger i iCloud-kalendern.")

    def test_warmed_transport_never_replaces_an_active_answer_connection(self):
        import openai_agent
        brain = openai_agent.LunaAgent(key="synthetic", mock_tools=True)
        old, warmed = NS(close=mock.Mock()), NS(close=mock.Mock())
        brain.conn = old
        lock = threading.Lock()
        def warm(candidate, instructions):
            candidate.conn = warmed
        with mock.patch.object(openai_agent.LunaAgent, "_warm_api", autospec=True, side_effect=warm):
            with lock:
                brain._warm_api_independent("prompt", lock)
            self.assertIs(brain.conn, old)
            warmed.close.assert_called_once()
            old.close.assert_not_called()
            warmed.close.reset_mock()
            brain._warm_api_independent("prompt", lock)
            self.assertIs(brain.conn, warmed)
            old.close.assert_called_once()
            warmed.close.assert_not_called()

    def test_background_warm_does_not_hold_the_question_lock_or_queue_duplicates(self):
        brain = FakeBrain()
        brain._ensure_tools()
        started, release = threading.Event(), threading.Event()
        brain.home = None
        def blocked_calendar():
            started.set()
            release.wait(2)
        brain._warm_snapshot = blocked_calendar
        brain._warm_api_independent = mock.Mock()
        question_lock = threading.Lock()
        try:
            with question_lock:
                self.assertTrue(brain.start_warm("prompt", question_lock))
            self.assertTrue(started.wait(1))
            self.assertFalse(brain.start_warm("prompt", question_lock))
            self.assertTrue(question_lock.acquire(timeout=0.1))
            question_lock.release()
        finally:
            release.set()
            self.assertTrue(brain._warm_inflight.acquire(timeout=2))
            brain._warm_inflight.release()

    def test_prefetch_cannot_restore_a_snapshot_after_a_calendar_write(self):
        brain = FakeBrain()
        brain._ensure_tools()
        brain.mcp = NS(url="http://fake", token="fake", tools=[{"name": "agenda"}])
        started, release = threading.Event(), threading.Event()
        def agenda(*args, **kwargs):
            started.set()
            release.wait(2)
            return False, "Old snapshot"
        client = NS(start=lambda: None, run=agenda, tools=None)
        with mock.patch.object(agent_core, "MCPClient", return_value=client):
            thread = threading.Thread(target=brain._warm_snapshot)
            thread.start()
            try:
                self.assertTrue(started.wait(1))
                with brain._snapshot_lock:
                    brain._agenda_generation += 1
                    brain.agenda = None
            finally:
                release.set()
                thread.join(2)
        self.assertIsNone(brain.fresh_agenda())

    def test_warm_calendar_model_and_home_overlap(self):
        brain = FakeBrain()
        brain._ensure_tools()
        barrier = threading.Barrier(3, timeout=1)

        def agenda(name, args, timeout):
            barrier.wait()
            self.assertEqual((name, timeout), ("agenda", 4))
            return False, "Calendar snapshot"

        def model(instructions):
            barrier.wait()
            self.assertEqual(instructions, "Fixed prompt")

        def home():
            barrier.wait()
            return "Home snapshot"

        brain.mcp = NS(tools=[{"name": "agenda"}], run=agenda)
        brain.home = NS(read=home)
        brain._warm_api = model
        brain.warm("Fixed prompt")
        self.assertEqual(brain.fresh_agenda()[0], "Calendar snapshot")
        self.assertEqual(brain.fresh_home()[0], "Home snapshot")

    def test_calendar_failure_still_warms_model_and_clears_stale_agenda(self):
        brain = FakeBrain()
        brain._ensure_tools()
        brain.agenda = (time.monotonic(), "Old calendar")
        brain.home = None
        brain.mcp = NS(tools=[{"name": "agenda"}], run=mock.Mock(side_effect=OSError("offline")))
        brain._warm_api = mock.Mock()
        brain.warm("Fixed prompt")
        brain._warm_api.assert_called_once_with("Fixed prompt")
        self.assertIsNone(brain.fresh_agenda())

    def test_cache_warm_failure_does_not_discard_successful_calendar(self):
        brain = FakeBrain()
        brain._ensure_tools()
        brain.home = None
        brain.mcp = NS(tools=[{"name": "agenda"}], run=lambda *args, **kwargs: (False, "Calendar snapshot"))
        brain._warm_api = mock.Mock(side_effect=RuntimeError("cache unavailable"))
        with self.assertRaisesRegex(RuntimeError, "cache unavailable"):
            brain.warm("Fixed prompt")
        self.assertEqual(brain.fresh_agenda()[0], "Calendar snapshot")



class FakeCalendar:
    """Calendar event tools plus an undo tool excluded from voice."""
    def __init__(self, url, token):
        pass

    def start(self):
        return [{"name": n, "inputSchema": {"type": "object"},
                 "annotations": {"readOnlyHint": n in agent_core.CALENDAR_READ_TOOLS.split(",")}}
                for n in CALENDAR_MCP]

    agenda = ""
    agenda_args = None

    def run(self, name, args, timeout=60):
        if name == "agenda":
            self.agenda_args = args
            return False, self.agenda
        self.sent = (name, args)
        return False, "Lagt till."


CALENDAR_MCP = ("calendars", "agenda", "search", "show_event", "add_event", "edit_event", "delete_event", "undo_change")


class CalendarBrain(agent_core.ToolAgent):
    def __init__(self, mock_tools=False, mcp_url=None, mcp_token_file=None, mcp_tools=None):
        self._init_tools(mock_tools, mcp_url, mcp_token_file, mcp_tools)

    def offered(self):
        self._ensure_tools()
        return {t["name"] for t in self.tools} & set(CALENDAR_MCP)


class CalendarTest(unittest.TestCase):
    def setUp(self):
        self.real, agent_core.MCPClient = agent_core.MCPClient, FakeCalendar
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        agent_core.MCPClient = self.real
        self.tmp.cleanup()

    def test_default_allows_event_edits_and_deletes_but_not_undo(self):
        with mock.patch.dict(os.environ, {"JARVIS_CALENDAR_MCP": "http://calendar.invalid/mcp"}):
            offered = CalendarBrain(**agent_core.tools_from_env()).offered()
        self.assertEqual(offered, {"calendars", "agenda", "search", "show_event", "add_event", "edit_event", "delete_event"})
        self.assertNotIn("undo_change", offered)

    def test_edit_and_delete_keep_occurrence_scope_and_invalidate_old_agenda(self):
        with mock.patch.dict(os.environ, {"JARVIS_CALENDAR_MCP": "http://calendar.invalid/mcp"}):
            brain = CalendarBrain(**agent_core.tools_from_env())
            brain._ensure_tools()
        for name, args in (("edit_event", {"id": "recurring-id", "occurrence": "2026-10-25", "start": "2026-10-25 10:00"}),
                           ("delete_event", {"id": "recurring-id", "occurrence": "2026-11-01", "and_later": False}),
                           ("delete_event", {"id": "recurring-id", "occurrence": "2026-11-08", "and_later": True})):
            with self.subTest(tool=name, args=args):
                brain.agenda = (time.monotonic(), "Old agenda")
                generation = brain._agenda_generation
                error, _ = brain.run_tool(name, args)
                self.assertFalse(error)
                self.assertEqual(brain.mcp.sent, (name, args))
                self.assertIsNone(brain.agenda)
                self.assertGreater(brain._agenda_generation, generation)
                self.assertNotIn(name, brain.read_only)
                self.assertNotIn(name, brain.early)

    def test_a_timed_event_always_gets_an_alert(self):
        with mock.patch.dict(os.environ, {"JARVIS_CALENDAR_MCP": "http://calendar.invalid/mcp"}), \
                mock.patch.object(agent_core, "GUARD_FILE", Path(self.tmp.name) / "guard.json"):
            brain = CalendarBrain(**agent_core.tools_from_env())
            brain._ensure_tools()
            brain.run_tool("add_event", {"calendar": "Annat", "title": "Utflykt", "start": "2026-10-24 11:29"})
            self.assertEqual(brain.mcp.sent[1]["alarms"], [agent_core.ALARM_MINUTES])
            brain.run_tool("add_event", {"calendar": "Annat", "title": "Tandläkare", "start": "2026-10-27 10:00",
                                         "alarms": [60]})
            self.assertEqual(brain.mcp.sent[1]["alarms"], [60])                 # his own alert stays
            brain.run_tool("add_event", {"calendar": "Annat", "title": "Lönedag", "start": "2026-10-25"})
            self.assertNotIn("alarms", brain.mcp.sent[1])                       # an all-day event gets none
            brain.run_tool("add_event", {"calendar": "Att göra", "title": "Gå till tvättstugan",
                                         "start": "2026-11-07 08:45", "end": "2026-11-07 09:00"})
            self.assertEqual(brain.mcp.sent[1]["alarms"], [0])                  # a nudge rings at its start
            brain.run_tool("add_event", {"calendar": "Att göra", "title": "Tvättstugan",
                                         "start": "2026-11-07T09:00", "end": "2026-11-07T12:00"})
            self.assertEqual(brain.mcp.sent[1]["alarms"], [agent_core.ALARM_MINUTES])

    def test_an_event_over_another_is_not_added_unless_asked(self):
        day = (agent_core.datetime.now(agent_core.TZ).date() + agent_core.timedelta(days=1)).isoformat()
        with mock.patch.dict(os.environ, {"JARVIS_CALENDAR_MCP": "http://calendar.invalid/mcp"}), \
                mock.patch.object(agent_core, "GUARD_FILE", Path(self.tmp.name) / "guard.json"):
            brain = CalendarBrain(**agent_core.tools_from_env())
            brain._ensure_tools()
            add = next(t for t in brain.tools if t["name"] == "add_event")
            self.assertIn("overlap_ok", add["parameters"]["properties"])
            brain.mcp.sent = None
            brain.mcp.agenda = (f"{day} | Annat | Lönedag | id A\n"
                                f"{day} 12:00-14:00 | Annat | Gondolen | id B | at Slussen\n"
                                f"{day} 19:15-19:30 | Att göra | Dammsuga hallen | id C")
            error, text = brain.run_tool("add_event", {"calendar": "Annat", "title": "Bio", "start": f"{day} 13:00",
                                                       "end": f"{day} 15:00"})
            self.assertTrue(error)
            self.assertIn("krockar med Gondolen 12:00–14:00 (Annat)", text)
            self.assertIsNone(brain.mcp.sent)                                      # nothing reached the calendar
            self.assertFalse(brain.run_tool("add_event", {"calendar": "Annat", "title": "Lunch",
                                                          "start": f"{day} 10:00", "end": f"{day} 12:00"})[0])
            self.assertFalse(brain.run_tool("add_event", {"calendar": "Annat", "title": "Bio", "start": f"{day} 13:00",
                                                          "end": f"{day} 15:00", "overlap_ok": True})[0])
            self.assertNotIn("overlap_ok", brain.mcp.sent[1])                      # ours, not the calendar's
            self.assertTrue(brain.run_tool("add_event", {"calendar": "Annat", "title": "Fika",
                                                         "start": f"{day} 19:00"})[0])   # an hour by default

    def test_allow_list_narrows(self):
        env = {"JARVIS_CALENDAR_MCP": "http://calendar.invalid/mcp",
               "JARVIS_CALENDAR_MCP_TOOLS": agent_core.CALENDAR_READ_TOOLS}
        with mock.patch.dict(os.environ, env):
            brain = CalendarBrain(**agent_core.tools_from_env())
            offered = brain.offered()
        self.assertEqual(offered, {"calendars", "agenda", "search", "show_event"})
        brain.mcp.sent = None
        for name in ("edit_event", "delete_event", "undo_change"):
            self.assertTrue(brain.run_tool(name, {"id": "protected"})[0])
            self.assertIsNone(brain.mcp.sent)

    def test_collision_query_is_scoped_to_booking_days_and_keeps_overnight_conflicts(self):
        day = agent_core.datetime.now(agent_core.TZ).date() + agent_core.timedelta(days=3)
        previous = day - agent_core.timedelta(days=1)
        with mock.patch.dict(os.environ, {"JARVIS_CALENDAR_MCP": "http://calendar.invalid/mcp"}):
            brain = CalendarBrain(**agent_core.tools_from_env())
            brain._ensure_tools()
        brain.mcp.agenda = f"{previous} 23:00 .. {day} 02:00 | Jobb | Nattpass | id night"
        self.assertEqual(brain._collisions({"start": f"{day} 01:00", "end": f"{day} 03:00"}),
                         ["Nattpass 23:00–02:00 (Jobb)"])
        self.assertEqual(brain.mcp.agenda_args, {"start": str(day), "end": str(day)})
        self.assertEqual(brain._collisions({"start": f"{day} 02:00", "end": f"{day} 03:00"}), [])
        after = day + agent_core.timedelta(days=1)
        brain._collisions({"start": f"{day} 23:00", "end": f"{after} 01:00"})
        self.assertEqual(brain.mcp.agenda_args, {"start": str(day), "end": str(after)})
        brain._collisions({"start": f"{day} 23:00", "end": f"{after} 00:00"})
        self.assertEqual(brain.mcp.agenda_args, {"start": str(day), "end": str(day)})


if __name__ == "__main__":
    unittest.main()


class DirectSpotifyTest(unittest.TestCase):
    def test_an_uncertain_direct_play_returns_none_for_the_model(self):
        brain = agent_core.ToolAgent.__new__(agent_core.ToolAgent)
        brain._ensure_tools = lambda: None
        brain.local_tools = {"spotify": lambda args: (False, "")}
        with unittest.mock.patch.object(agent_core.spotify, "quick_play", return_value=None):
            self.assertIsNone(brain.command("spotify_play", {"query": "x"}))
        with unittest.mock.patch.object(agent_core.spotify, "quick_play", return_value=(False, "Spelar X.")):
            self.assertEqual(brain.command("spotify_play", {"query": "x"}), "Spelar X.")
            self.assertTrue(brain.last_tools[0]["direct"])
