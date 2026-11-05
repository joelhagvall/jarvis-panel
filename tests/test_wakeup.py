"""Morning production path with fake facts, voice and model. Never plays or calls a provider."""
from datetime import datetime, timedelta
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace as NS
import unittest
from unittest.mock import MagicMock, Mock, patch
import wave

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server"))
import wakeup

DUE = datetime(2054, 10, 26, 8, 45, tzinfo=wakeup.TZ)


def wav():
    out = io.BytesIO()
    with wave.open(out, "wb") as w:
        w.setparams((1,2,16000,0,"NONE","not compressed")); w.writeframes(b"\0\0" * 160)
    return out.getvalue()


class WakeupTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.voice = NS(speech=Mock(return_value=(wav(), "cloud")))
        self.manager = wakeup.Wakeups(Path(self.tmp.name)/"state", Path(self.tmp.name)/"audio", self.voice)
        self.manager.reserves()

    def facts(self):
        return {"due":DUE.isoformat(),"kind":"normal","prepared_at":(DUE-timedelta(minutes=3)).isoformat(),
                "facts":{"weather":"Åtta grader.","calendar":"Inget inbokat.","sunrise":"Solen går upp 07.30."},
                "sources":{"weather":"ok","calendar":"ok","sunrise":"ok"},
                "writer":"template","message":wakeup.greeting(DUE)+" Åtta grader. Inget inbokat."}

    def test_alarm_claim_needs_no_live_model_or_voice_and_deduplicates_across_modes_and_restart(self):
        self.voice.speech.reset_mock()
        result = self.manager.claim(DUE,"office",now=DUE)
        self.assertTrue(result["claimed"])
        self.assertEqual(result["writer"],"reserve")
        self.assertIn("Demoorten",result["message"])
        self.assertTrue((self.manager.media / result["media_id"].rsplit("/",1)[-1]).is_file())
        fresh = wakeup.Wakeups(self.manager.root,self.manager.media,self.voice)
        self.assertFalse(fresh.claim(DUE,"normal",now=DUE)["claimed"])
        self.voice.speech.assert_not_called()

    def test_reserves_are_persistent_and_preview_does_not_consume_scheduled_alarm(self):
        self.voice.speech.reset_mock()
        self.manager.reserves();self.voice.speech.assert_not_called()
        self.assertTrue(self.manager.claim(DUE,"normal",preview=True,now=DUE-timedelta(hours=2))["claimed"])
        self.assertTrue(self.manager.claim(DUE,"normal",now=DUE)["claimed"])

    def test_truncated_prepared_audio_uses_reserve_without_any_provider_call(self):
        data=self.facts();data['audio']=self.manager.audio_file(data['message'])
        self.manager.save(self.manager.key(DUE,'normal')+'.json',data)
        (self.manager.media/data['audio']).write_bytes(wav()[:-20])
        self.voice.speech.reset_mock()
        result=self.manager.claim(DUE,'normal',now=DUE)
        self.assertEqual(result['writer'],'reserve')
        self.voice.speech.assert_not_called()

    def test_corrupt_reserve_is_not_claimed_or_reported_ready(self):
        reserve=self.manager.read('reserves.json')['normal']
        (self.manager.media/reserve['audio']).write_bytes(b'not a WAV')
        self.voice.speech.reset_mock()
        self.assertNotIn('normal',self.manager.status()['reserves_ready'])
        with self.assertRaises(ValueError):self.manager.claim(DUE,'normal',now=DUE)
        self.assertEqual(list(self.manager.root.glob('played-*.json')),[])
        self.voice.speech.assert_not_called()

    def test_invalid_prepared_metadata_uses_reserve_without_consuming_a_bad_candidate(self):
        for field,value in (('prepared_at','invalid'),('message',None),('writer','unknown')):
            with self.subTest(field=field):
                manager=wakeup.Wakeups(self.manager.root/field,self.manager.media,self.voice)
                manager.reserves()
                data=self.facts();data['audio']=manager.audio_file(data['message']);data[field]=value
                manager.save(manager.key(DUE,'normal')+'.json',data)
                self.voice.speech.reset_mock()
                self.assertEqual(manager.claim(DUE,'normal',now=DUE)['writer'],'reserve')
                self.voice.speech.assert_not_called()

    def test_preparation_publishes_factual_audio_before_model_and_playback_does_not_wait(self):
        def slow_model(data):
            result=self.manager.claim(DUE,"normal",now=DUE)
            self.assertEqual((result["writer"],result["message"]),("template",data["message"]))
            raise TimeoutError("model offline")
        with patch.object(wakeup,"compose",return_value=self.facts()),patch.object(wakeup,"write_with_claude",side_effect=slow_model):
            result=self.manager.prepare(DUE,"normal",now=DUE-timedelta(minutes=3))
        self.assertEqual(result["writer"],"template")
        self.assertEqual(self.manager.read(self.manager.key(DUE,"normal")+".json")["writer_error"],"TimeoutError")

    def test_complete_model_and_audio_are_prepared_once(self):
        with patch.object(wakeup,"compose",return_value=self.facts()) as facts,patch.object(wakeup,"write_with_claude",side_effect=lambda d:{**d,"writer":"claude","message":"God morgon, Joel. Färdig rapport."}) as model:
            for _ in range(2):self.manager.prepare(DUE,"normal",now=DUE-timedelta(minutes=3))
        facts.assert_called_once();model.assert_called_once()
        self.assertEqual(self.manager.claim(DUE,"normal",now=DUE)["writer"],"claude")

    def test_stale_or_missing_prepared_audio_uses_reserve_and_invalid_times_are_refused(self):
        data=self.facts();data["audio"]="missing.wav"
        self.manager.save(self.manager.key(DUE,"normal")+".json",data)
        self.assertEqual(self.manager.claim(DUE,"normal",now=DUE)["writer"],"reserve")
        for now in (DUE-timedelta(minutes=2),DUE+timedelta(minutes=3)):
            with self.assertRaises(ValueError):self.manager.claim(DUE,"normal",now=now)
        with self.assertRaises(ValueError):self.manager.prepare(DUE,"normal",now=DUE-timedelta(days=1))
        with self.assertRaises(ValueError):wakeup.due_time("2054-10-26T08:45:00")

    def test_model_gets_only_current_facts_and_office_mode_without_tools_or_chat(self):
        factory=MagicMock();client=factory.return_value.__enter__.return_value
        client.messages.create.return_value=NS(stop_reason="end_turn",content=[NS(type="text",text=" ".join(["Morgon"]*60))])
        result=wakeup.write_with_claude(self.facts(),factory)
        args=client.messages.create.call_args.kwargs
        self.assertNotIn("tools",args);self.assertEqual(len(args["messages"]),1)
        self.assertEqual(json.loads(args["messages"][0]["content"])["fakta"],self.facts()["facts"])
        self.assertEqual(result["writer"],"claude")
        self.assertEqual(factory.call_args.kwargs,{"max_retries":0,"timeout":18.0})
        client.messages.create.return_value.stop_reason="max_tokens"
        with self.assertRaises(ValueError):wakeup.write_with_claude(self.facts(),factory)

    def test_custom_wish_reaches_the_model_and_jarvis_writes_the_greeting_himself(self):
        factory=MagicMock();client=factory.return_value.__enter__.return_value
        text="God morgon Joel och Alex! "+" ".join(["Morgon"]*50)
        client.messages.create.return_value=NS(stop_reason="end_turn",content=[NS(type="text",text=text)])
        data={**self.facts(),"kind":"custom","wish":"Väck mig och Alex, vi ska till Demoparken"}
        result=wakeup.write_with_claude(data,factory)
        sent=json.loads(client.messages.create.call_args.kwargs["messages"][0]["content"])
        self.assertEqual(sent["önskemål"],data["wish"]);self.assertEqual(sent["fakta"],data["facts"])
        self.assertEqual(sent["klockslag"],wakeup.clock_text(DUE))
        self.assertEqual(result["message"],text)        # no fixed "God morgon, Joel." before his own greeting
        for wish in ("","   ","x"*256,None):
            with self.assertRaises(ValueError):wakeup.wish_text("custom",wish)
        self.assertEqual(wakeup.wish_text("normal","ignored"),"")

    def test_custom_claim_plays_the_text_prepared_for_that_wish_else_the_normal_reserve(self):
        self.assertNotIn("custom",self.manager.reserves()["ready"])
        wish="Väck mig och Alex"
        with patch.object(wakeup,"compose",return_value={**self.facts(),"kind":"custom"}),patch.object(wakeup,"write_with_claude",
                side_effect=lambda d:{**d,"writer":"claude","message":"Hej Joel och Alex."}) as model:
            self.manager.prepare(DUE,"custom",now=DUE-timedelta(minutes=3),wish=wish)
        self.assertEqual(model.call_args.args[0]["wish"],wish)
        fresh=wakeup.Wakeups(Path(self.tmp.name)/"state2",Path(self.tmp.name)/"audio2",self.voice);fresh.reserves()
        self.assertEqual(fresh.claim(DUE,"custom",now=DUE,wish=wish)["writer"],"reserve")       # nothing prepared there
        self.assertNotEqual(self.manager.key(DUE,"custom",wish),self.manager.key(DUE,"custom","Annan text"))
        played=self.manager.claim(DUE,"custom",now=DUE,wish=wish)
        self.assertEqual((played["writer"],played["message"]),("claude","Hej Joel och Alex."))
        with self.assertRaises(ValueError):self.manager.prepare(DUE+timedelta(days=1),"custom",now=DUE+timedelta(days=1),wish="")

    def test_a_wakeup_after_sunrise_says_the_sun_is_up_and_never_sends_an_empty_fact(self):
        # Synthetic astronomy state: the next rising is tomorrow.
        up={"state":"above_horizon","attributes":{"next_rising":"2054-10-27T05:55:00+00:00"}}
        self.assertEqual(wakeup.sunrise_text(up,DUE.replace(hour=10,minute=41)),"Solen är redan uppe.")
        self.assertEqual(wakeup.sunrise_text({**up,"state":"below_horizon"},DUE.replace(hour=20)),"")
        self.assertEqual(wakeup.sunrise_text({**up,"attributes":{"next_rising":"2054-10-26T06:20:00Z"}},DUE.replace(hour=6)),
                         "Solen går upp klockan 07.20.")
        factory=MagicMock();client=factory.return_value.__enter__.return_value
        client.messages.create.return_value=NS(stop_reason="end_turn",content=[NS(type="text",text=" ".join(["Morgon"]*60))])
        data=self.facts();data["facts"]["sunrise"]=""
        wakeup.write_with_claude(data,factory)
        self.assertNotIn("sunrise",json.loads(client.messages.create.call_args.kwargs["messages"][0]["content"])["fakta"])

    def test_calendar_preserves_real_dates_and_times_and_does_not_mistake_failure_for_empty(self):
        raw="\n".join(["2054-10-26 | Annat | Födelsedag | id a", "2054-10-26 03:00-07:00 | Jobb | Tidigare | id b",
            "2054-10-26 09:00-10:00 | Jobb | Kontoret | id c | at Demoorten",
            "2054-10-26 09:00-09:01 | Hem | Dammsuga | id d"])
        with patch.dict(os.environ, {"JARVIS_SILENT_TOPICS": r"dammsug\w*|vacuum\w*"}):
            text=wakeup.calendar_text(raw,DUE)
        self.assertIn("Födelsedag",text);self.assertIn("09.00",text)
        self.assertNotIn("Tidigare",text);self.assertNotIn("Dammsuga",text)
        for invalid in ("", "FAILED: offline", "2054-10-27 09:00-10:00 | Annat | Fel | id e"):
            with self.assertRaises(ValueError):wakeup.calendar_text(invalid,DUE)
        self.assertIn("inget inbokat",wakeup.calendar_text("Nothing in that period.",DUE))

    def test_ha_config_preserves_other_packages_and_dashboards_and_is_idempotent(self):
        spec=importlib.util.spec_from_file_location("wakeup_setup",ROOT/"tools/setup_wakeup_ha.py")
        setup=importlib.util.module_from_spec(spec);spec.loader.exec_module(setup)
        before="homeassistant:\n  packages:\n    booking: !include booking.yaml\nlovelace:\n  dashboards:\n    old-dashboard:\n      mode: yaml\n"
        after=setup.configure(before)
        self.assertIn("booking: !include booking.yaml",after)
        self.assertIn("old-dashboard:",after)
        self.assertEqual(setup.configure(after),after)
        with self.assertRaises(ValueError):setup.configure("homeassistant: {}\n")

    def test_ha_helpers_restore_state_instead_of_reenabling_alarms_on_every_restart(self):
        import yaml
        package=yaml.safe_load((ROOT/"ha/jarvis_wakeup.yaml").read_text())
        for domain in ("input_boolean","input_datetime","input_select","input_number","input_text"):
            for config in package[domain].values():self.assertNotIn("initial",config)


    def test_every_day_has_its_own_helpers_and_the_dashboard_names_only_real_entities(self):
        import re,yaml
        package=yaml.safe_load((ROOT/"ha/jarvis_wakeup.yaml").read_text())
        days=("mandag","tisdag","onsdag","torsdag","fredag","lordag","sondag")
        for day in days:
            self.assertIn("jarvis_vackning_"+day,package["input_boolean"])
            self.assertIn("jarvis_vackning_"+day+"_tid",package["input_datetime"])
            self.assertEqual(package["input_select"]["jarvis_vackning_"+day+"_typ"]["options"],["Vanlig morgon","Kontorsmorgon"])
        self.assertNotIn("jarvis_vackning_vecka",package["input_boolean"])      # no master switch to forget
        self.assertIn("Egen väckning",package["input_select"]["jarvis_vackning_engang_typ"]["options"])
        self.assertEqual(package["input_text"]["jarvis_vackning_engang_text"]["max"],wakeup.WISH_MAX)
        known={f"{domain}.{name}" for domain in ("input_boolean","input_datetime","input_select","input_number","input_text","script")
               for name in package[domain]}|{"binary_sensor.jarvis_vackning_pagar","sensor.jarvis_nasta_vackning"}
        dashboard=(ROOT/"ha/jarvis_wakeup_dashboard.yaml").read_text()
        used=set(re.findall(r"\b(?:input_\w+|script|binary_sensor|sensor)\.jarvis_\w+",dashboard))
        self.assertTrue(used)
        self.assertEqual(used-known,set())
        for hidden in ("input_boolean.jarvis_vackning_snooze","input_datetime.jarvis_vackning_snooze",
                       "input_datetime.jarvis_vackning_tid","input_select.jarvis_vackning_typ"):
            self.assertNotIn(hidden+"\n",dashboard)

if __name__=="__main__":unittest.main()
