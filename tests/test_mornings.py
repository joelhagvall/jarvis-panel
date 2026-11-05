"""The tools morgon_tv, morgon_tv_andra, vackningar and vackning_andra (server/agents/mornings.py, Joel 2054-11-02):
Morgon-TV and the wakeups read and changed through their Home Assistant helpers. A fake HA in memory applies the
service calls to its states; nothing reaches Home Assistant, the TV or the panel.
Run: .venv/bin/python -m unittest tests.test_mornings"""
import json
import sys
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server" / "agents"))
import mornings  # noqa: E402
from spoken_change import Turn  # noqa: E402

ASK = Turn("ändra morgon-tv och väckningen i morgon")    # the stand-in question: Joel asks for the change

NOW = datetime(2054, 11, 2, 20, 0, tzinfo=mornings.TZ)      # a Monday evening


class FakeHA:
    def __init__(self):
        self.states = {"input_number.morgon_tv_volym": "6.0", "input_text.morgon_tv_senast": "mån 2/11 08:45 · ✅ Quiet",
                       "input_text.morgon_tv_standard": "https://www.youtube.com/watch?v=std",
                       "input_text.morgon_tv_standard_titel": "Quiet Morning",
                       "input_select.jarvis_vackning_radio": "Av",
                       "input_boolean.jarvis_vackning_engang": "off",
                       "input_datetime.jarvis_vackning_engang": "2054-10-31 10:41:00",
                       "input_select.jarvis_vackning_engang_typ": "Egen väckning",
                       "input_text.jarvis_vackning_engang_text": "förra veckans önskan"}
        for day in mornings.DAYS:
            t, w = mornings.TV_DAY[day], mornings.WAKE_DAY[day]
            self.states.update({f"input_boolean.morgon_tv_{t}": "on", f"input_datetime.morgon_tv_tid_{t}": "08:45:00",
                                f"input_text.morgon_tv_{t}": "", f"input_text.morgon_tv_{t}_titel": "",
                                f"input_boolean.jarvis_vackning_{w}": "on",
                                f"input_datetime.jarvis_vackning_{w}_tid": "08:45:00",
                                f"input_select.jarvis_vackning_{w}_typ": "Vanlig morgon"})
        self.calls = []
        self.down = False

    def get_json(self, path, timeout=10):
        assert path == "/api/states"
        if self.down:
            raise OSError("connection refused")
        rows = [{"entity_id": e, "state": s, "attributes": {}} for e, s in self.states.items()]
        rows.append({"entity_id": "sensor.jarvis_nasta_vackning", "state": "x",
                     "attributes": {"text": "Imorgon, tisdag 3 nov 08:45 · Vanlig morgon", "status": "Schemalagd"}})
        rows.append({"entity_id": "light.kitchen", "state": "on", "attributes": {}})
        return rows

    def call_service(self, domain, service, data, timeout=10):
        if self.down:
            raise OSError("connection refused")
        self.calls.append((domain, service, data))
        e = data["entity_id"]
        if service in ("turn_on", "turn_off"):
            self.states[e] = service[5:]
        elif domain == "input_datetime":
            self.states[e] = data.get("time") or data["datetime"]
        elif service == "select_option":
            self.states[e] = data["option"]
        else:
            self.states[e] = str(data["value"])
        return b"[]"


class MorningsTest(unittest.TestCase):
    def setUp(self):
        self.ha = FakeHA()
        for name in ("get_json", "call_service"):
            patch = mock.patch.object(mornings.hass, name, getattr(self.ha, name))
            patch.start()
            self.addCleanup(patch.stop)
        # The change tools' spoken gate: unless a test passes its own Turn, Joel asked for the change.
        for name in ("tv_change", "wake_change"):
            patch = mock.patch.object(mornings, name, lambda _f=getattr(mornings, name), **kw: _f(**{"spoken": ASK, **kw}))
            patch.start()
            self.addCleanup(patch.stop)

    def ok(self, result):
        error, text = result
        self.assertFalse(error, text)
        return json.loads(text)

    # -- Morgon-TV
    def test_tv_read_shows_every_day_and_the_standard(self):
        self.ha.states["input_text.morgon_tv_ons"] = "https://www.youtube.com/watch?v=snow"
        self.ha.states["input_text.morgon_tv_ons_titel"] = "Snow Moon"
        r = self.ok(mornings.tv_read())
        self.assertEqual(r["dagar"]["onsdag"], {"på": True, "tid": "08:45", "video": "Snow Moon"})
        self.assertEqual(r["dagar"]["måndag"]["video"], "standardvideon")
        self.assertEqual((r["standardvideo"], r["volym"]), ("Quiet Morning", 6))

    def test_tv_set_writes_video_and_time_then_turns_the_day_on(self):
        self.ha.states["input_boolean.morgon_tv_tor"] = "off"
        r = self.ok(mornings.tv_change(action="sätt", days=["torsdag"], time="9.30", video="regnljud"))
        self.assertEqual(r["dagar"]["torsdag"], {"på": True, "tid": "09:30", "video": "regnljud"})
        self.assertEqual([c[1] for c in self.ha.calls], ["set_value", "set_datetime", "turn_on"])
        self.assertEqual(self.ha.states["input_datetime.morgon_tv_tid_tor"], "09:30:00")
        self.assertEqual(self.ha.states["input_text.morgon_tv_ons"], "")       # other days untouched

    def test_a_new_link_is_not_shown_with_the_old_title(self):
        self.ha.states["input_text.morgon_tv_fre"] = "https://www.youtube.com/watch?v=alone"
        self.ha.states["input_text.morgon_tv_fre_titel"] = "Alone"     # HA has not looked up the new one yet
        r = self.ok(mornings.tv_change(action="sätt", days=["fredag"], video="https://youtu.be/new"))
        self.assertEqual(r["dagar"]["fredag"]["video"], "https://youtu.be/new")

    def test_tv_time_only_keeps_the_video_and_standard_clears_it(self):
        self.ha.states["input_text.morgon_tv_fre"] = "https://www.youtube.com/watch?v=alone"
        self.ok(mornings.tv_change(action="sätt", days=["fredag"], time="10:00"))
        self.assertEqual(self.ha.states["input_text.morgon_tv_fre"], "https://www.youtube.com/watch?v=alone")
        self.ok(mornings.tv_change(action="sätt", days=["fredag"], video="Standard"))
        self.assertEqual(self.ha.states["input_text.morgon_tv_fre"], "")

    def test_tv_off_keeps_time_and_video(self):
        self.ha.states["input_text.morgon_tv_lor"] = "regn"
        r = self.ok(mornings.tv_change(action="av", days=["lördag", "söndag"]))
        self.assertEqual(set(r["dagar"]), {"lördag", "söndag"})
        self.assertEqual([c[1] for c in self.ha.calls], ["turn_off", "turn_off"])
        self.assertEqual(self.ha.states["input_text.morgon_tv_lor"], "regn")

    def test_tv_refuses_bad_arguments_without_calling_ha(self):
        for args in ({"action": "sätt", "days": ["fredagen"]}, {"action": "sätt", "days": []},
                     {"action": "sätt", "days": ["fredag"], "time": "25:00"},
                     {"action": "sätt", "days": ["fredag"], "volume": 80}, {"action": "radera", "days": ["fredag"]}):
            error, _ = mornings.tv_change(**args)
            self.assertTrue(error, args)
        self.assertEqual(self.ha.calls, [])

    # -- Väckning
    def test_the_changes_need_joels_words(self):
        # 2054-11-02 review: the change tools move real alarms, so the question's words must ask for it
        # (spoken_change.py, whose own tests cover the wording). A refusal never writes to HA.
        for spoken in (Turn("vad har jag för väckning imorgon?"), Turn("ändra inte väckningen på fredag"),
                       Turn("ja tack", "Väckningen är 09:30 i morgon. Något mer?"),
                       Turn("ja", "Ska jag stänga av morgon-TV på fredag?"), None):
            with self.subTest(spoken=spoken):
                error, text = mornings.wake_change(action="veckodag_av", days=["fredag"], spoken=spoken)
                self.assertTrue(error)
                self.assertIn("Ska jag ändra", text)     # the refusal tells the model how to get a yes
        for spoken in (Turn("stäng av tv:n"), Turn("ja", "Ska jag stänga av väckningen på fredag?")):
            with self.subTest(spoken=spoken):
                self.assertTrue(mornings.tv_change(action="av", days=["fredag"], spoken=spoken)[0])
        self.assertEqual(self.ha.calls, [])

    def test_natural_wakeup_requests_change_the_alarm(self):
        for spoken in (Turn("kan du väcka mig på fredag klockan sju?"), Turn("ändra larmet till sju på fredag"),
                       Turn("jag vill vakna sju på fredag"), Turn("wake me up at seven on Friday"),
                       Turn("ja, gör det", "Kan jag flytta väckningen till 10.00?"),
                       Turn("sju", "Till vilken tid?", "Ändra väckningen på fredag")):
            with self.subTest(spoken=spoken):
                self.ha.calls.clear()
                self.ok(mornings.wake_change(action="veckodag", days=["fredag"], time="10:00", spoken=spoken))
                self.assertEqual(self.ha.states["input_datetime.jarvis_vackning_fredag_tid"], "10:00:00")
                self.assertEqual(len(self.ha.calls), 2)

    def test_natural_morning_tv_requests_change_only_morning_tv(self):
        for spoken, action in ((Turn("aktivera morgon-tv på fredag"), "sätt"),
                               (Turn("kan du sätta morgon TV på fredag klockan sju?"), "sätt"),
                               (Turn("ja tack!", "Vill du att jag stänger av morgon-TV på fredag?"), "av")):
            with self.subTest(spoken=spoken):
                self.ha.calls.clear()
                self.ok(mornings.tv_change(action=action, days=["fredag"], time="10:00", spoken=spoken))
                expected = [] if action == "av" else [
                    ("input_datetime", "set_datetime", {"entity_id": "input_datetime.morgon_tv_tid_fre",
                                                        "time": "10:00:00"})]
                expected.append(("input_boolean", "turn_off" if action == "av" else "turn_on",
                                 {"entity_id": "input_boolean.morgon_tv_fre"}))
                self.assertEqual(self.ha.calls, expected)

    def test_wake_read(self):
        r = self.ok(mornings.wake_read())
        self.assertEqual(r["dagar"]["lördag"], {"på": True, "tid": "08:45", "innehåll": "Vanlig morgon"})
        self.assertEqual(r["extra"]["på"], False)
        self.assertEqual(r["extra"]["instruktion"], "förra veckans önskan")
        self.assertEqual(r["nästa"], "Imorgon, tisdag 3 nov 08:45 · Vanlig morgon")

    def test_weekday_set_and_off(self):
        r = self.ok(mornings.wake_change(action="veckodag", days=["tisdag"], time="08:00", content="Kontorsmorgon"))
        self.assertEqual(r["dagar"]["tisdag"], {"på": True, "tid": "08:00", "innehåll": "Kontorsmorgon"})
        self.ok(mornings.wake_change(action="veckodag_av", days=["söndag"]))
        self.assertEqual(self.ha.states["input_boolean.jarvis_vackning_sondag"], "off")
        self.assertEqual(self.ha.states["input_datetime.jarvis_vackning_sondag_tid"], "08:45:00")

    def test_extra_with_instruction_is_egen_vackning(self):
        r = self.ok(mornings.wake_change(action="extra", at="2054-11-03 09:00",
                                         instruction="väck mig  och Alex, vi ska till Demoparken", _now=NOW))
        self.assertEqual(r["extra"], {"på": True, "när": "2054-11-03 09:00", "innehåll": "Egen väckning",
                                      "instruktion": "väck mig och Alex, vi ska till Demoparken"})
        self.assertEqual(r["tidigare"], "ingen extra väckning var på")
        # the switch goes on last, after time, wish and content are in place
        self.assertEqual(self.ha.calls[-1][1:], ("turn_on", {"entity_id": "input_boolean.jarvis_vackning_engang"}))

    def test_new_extra_without_instruction_is_not_the_old_wish(self):
        r = self.ok(mornings.wake_change(action="extra", at="2054-11-03 09:00", _now=NOW))
        self.assertEqual(r["extra"]["innehåll"], "Vanlig morgon")
        self.assertNotIn("instruktion", r["extra"])

    def test_editing_the_extra_keeps_what_is_not_given(self):
        self.ok(mornings.wake_change(action="extra", at="2054-11-03 09:00", instruction="Demoparken", _now=NOW))
        r = self.ok(mornings.wake_change(action="extra", at="2054-11-03 09:30", _now=NOW))
        self.assertEqual((r["extra"]["när"], r["extra"]["instruktion"]), ("2054-11-03 09:30", "Demoparken"))
        self.assertEqual(r["tidigare"]["när"], "2054-11-03 09:00")
        r = self.ok(mornings.wake_change(action="extra", instruction="Demomuseet i stället", _now=NOW))
        self.assertEqual((r["extra"]["när"], r["extra"]["instruktion"]), ("2054-11-03 09:30", "Demomuseet i stället"))

    def test_extra_off_reports_what_was_removed(self):
        self.ok(mornings.wake_change(action="extra", at="2054-11-03 09:00", _now=NOW))
        r = self.ok(mornings.wake_change(action="extra_av", _now=NOW))
        self.assertFalse(r["extra"]["på"])
        self.assertEqual(r["tidigare"]["när"], "2054-11-03 09:00")

    def test_extra_refusals(self):
        cases = ({"action": "extra", "at": "2054-11-02 19:00"},          # passed
                 {"action": "extra"},                                     # new one without a moment
                 {"action": "extra", "at": "i morgon"},
                 {"action": "extra", "at": "2054-11-03 09:00", "instruction": "x" * 256},
                 {"action": "veckodag", "days": ["måndag"], "content": "Egen väckning"},
                 {"action": "veckodag"})
        for args in cases:
            error, _ = mornings.wake_change(**args, _now=NOW)
            self.assertTrue(error, args)
        self.assertEqual(self.ha.calls, [])

    def test_editing_a_passed_extra_needs_a_new_time(self):
        self.ha.states["input_boolean.jarvis_vackning_engang"] = "on"      # 2054-10-31 10:41, already passed
        error, text = mornings.wake_change(action="extra", instruction="ny", _now=NOW)
        self.assertTrue(error)
        self.assertIn("redan varit", text)

    def test_home_assistant_down_is_an_error(self):
        self.ha.down = True
        for result in (mornings.tv_read(), mornings.wake_read(),
                       mornings.tv_change(action="av", days=["måndag"]),
                       mornings.wake_change(action="extra_av", _now=NOW)):
            self.assertTrue(result[0])
            self.assertIn("Home Assistant svarade inte", result[1])


if __name__ == "__main__":
    unittest.main()
