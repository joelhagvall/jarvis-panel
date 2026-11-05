"""Scheduling and playback ownership against real HA API types; no model, speech or device calls."""
import asyncio
from datetime import datetime,timedelta
import importlib
import importlib.util
import inspect
from pathlib import Path
import sys
import types
from types import SimpleNamespace as NS
import unittest
from unittest.mock import AsyncMock,Mock,patch

ROOT=Path(__file__).resolve().parent.parent
HAS_HA=importlib.util.find_spec("homeassistant") is not None
if HAS_HA:
    package=types.ModuleType("jarvis_wakeup_test");package.__path__=[str(ROOT/"ha/custom_components/jarvis_agent")]
    sys.modules[package.__name__]=package
    wake=importlib.import_module("jarvis_wakeup_test.wakeup")
    sensor=importlib.import_module("jarvis_wakeup_test.sensor")
    from aioesphomeapi import APIClient
    from homeassistant.core import HassJob, HassJobType
    NOW=datetime(2054,10,25,12,0,tzinfo=wake.TZ)


def states_for_schedule():
    values={"input_boolean.jarvis_vackning_engang":"on","input_datetime.jarvis_vackning_engang":"2054-10-26 08:45:00",
        "input_select.jarvis_vackning_engang_typ":"Vanlig morgon","input_boolean.jarvis_vackning_vecka":"on",
        "input_datetime.jarvis_vackning_tid":"08:45:00","input_select.jarvis_vackning_typ":"Kontorsmorgon",
        "input_boolean.jarvis_vackning_tisdag":"on","input_boolean.jarvis_vackning_torsdag":"on",
        "input_number.jarvis_vackning_volym":"65"}
    return {k:NS(state=v,attributes={}) for k,v in values.items()}


@unittest.skipUnless(HAS_HA,"run inside Home Assistant")
class WakeupHATest(unittest.IsolatedAsyncioTestCase):
    def controller(self,states=None):
        states=states if states is not None else states_for_schedule()
        ha=NS(states=states,async_create_task=asyncio.create_task,data={},services=NS(async_call=AsyncMock()))
        client=NS(wakeup=AsyncMock(return_value={"claimed":True,"media_id":"http://example.test/alarm.wav","message":"God morgon."}))
        with patch.object(wake,"Store",return_value=NS(async_load=AsyncMock(return_value={}),async_save=AsyncMock())):
            ctrl=wake.WakeupController(ha,client,"test")
        self.addAsyncCleanup(self.cleanup,ctrl)
        return ctrl

    async def cleanup(self,ctrl):
        tasks=list(ctrl.tasks)
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)

    async def test_requested_schedule_and_dates_are_exact_and_live_edits_apply(self):
        states=states_for_schedule();plans=wake.plans(states,NOW)
        self.assertEqual([(p.due.strftime("%Y-%m-%d %H:%M"),p.kind) for p in plans[:3]],
            [("2054-10-26 08:45","normal"),("2054-10-27 08:45","office"),("2054-10-29 08:45","office")])
        states["input_boolean.jarvis_vackning_engang"].state="off"
        states["input_datetime.jarvis_vackning_tid"].state="09:10:00"
        self.assertEqual(wake.plans(states,NOW)[0].due.strftime("%Y-%m-%d %H:%M"),"2054-10-27 09:10")

    async def test_each_day_has_its_own_time_and_content_and_no_master_switch(self):
        # 2054-10-28: "Veckoschema på" was on with every day off, and Joel read "Vanlig morgon" as "every day".
        states=states_for_schedule();states["input_boolean.jarvis_vackning_engang"].state="off"
        states["input_boolean.jarvis_vackning_vecka"].state="off"            # an old master switch is ignored
        states["input_boolean.jarvis_vackning_onsdag"]=NS(state="on")
        states["input_datetime.jarvis_vackning_onsdag_tid"]=NS(state="10:30:00")
        states["input_select.jarvis_vackning_onsdag_typ"]=NS(state="Vanlig morgon")
        states["input_select.jarvis_vackning_torsdag_typ"]=NS(state="Kontorsmorgon")
        self.assertEqual([(p.due.strftime("%a %H:%M"),p.kind) for p in wake.plans(states,NOW)[:3]],
            [("Tue 08:45","office"),("Wed 10:30","normal"),("Thu 08:45","office")])
        for day in wake.DAYS:
            self.assertIn("input_datetime.jarvis_vackning_"+day+"_tid",wake.CONTROLS)
            self.assertIn("input_select.jarvis_vackning_"+day+"_typ",wake.CONTROLS)
        states["input_select.jarvis_vackning_onsdag_typ"].state="unavailable"
        self.assertEqual(wake.plans(states,NOW)[1].kind,"office")             # falls back to the common content
        for day in wake.DAYS:states.pop("input_boolean.jarvis_vackning_"+day,None)
        self.assertEqual(wake.plans(states,NOW),[])

    async def test_the_dashboard_line_and_the_reason_when_nothing_is_scheduled(self):
        plan=wake.Plan(datetime(2054,10,29,8,45,tzinfo=wake.TZ),"normal","oneoff")
        self.assertEqual(wake.describe(plan,datetime(2054,10,28,9,15,tzinfo=wake.TZ)),
            "Imorgon, torsdag 29 okt 08:45 · Vanlig morgon · extra väckning")
        self.assertEqual(wake.describe(wake.Plan(plan.due,"office","weekly"),datetime(2054,10,26,22,0,tzinfo=wake.TZ)),
            "Torsdag 29 okt 08:45 · Kontorsmorgon")
        self.assertTrue(wake.describe(plan,plan.due.replace(hour=1)).startswith("Idag, torsdag"))
        ctrl=self.controller();ctrl.alarm_at=None
        for key in [k for k in ctrl.hass.states if k.startswith("input_boolean.")]:ctrl.hass.states[key].state="off"
        self.assertEqual(ctrl.status(NOW),"Ingen väckning – inga dagar valda")
        ctrl.hass.states["input_boolean.jarvis_vackning_tisdag"].state="on"
        self.assertEqual(ctrl.status(NOW),"Ingen väckning schemalagd")

    async def test_the_natt_page_line(self):
        # 2054-11-02: Natt shows the next wakeup, short, so the morning can be checked before bed.
        night = datetime(2054,10,28,22,30,tzinfo=wake.TZ)
        self.assertEqual(wake.panel_line(wake.Plan(datetime(2054,10,29,8,45,tzinfo=wake.TZ),"normal","weekly"),night),
            "Väckning 08:45")
        self.assertEqual(wake.panel_line(wake.Plan(datetime(2054,11,2,8,45,tzinfo=wake.TZ),"office","weekly"),night),
            "Väckning mån 08:45")
        self.assertEqual(wake.panel_line(wake.Plan(datetime(2054,10,29,8,50,tzinfo=wake.TZ),"normal","snooze"),night),
            "Snooze till 08:50")
        self.assertEqual(wake.panel_line(None,night),"Ingen väckning")

    async def test_panel_timers_use_the_panels_device(self):
        # Hemmet's quick timers: HA's own Assist timers, which need the panel's device (a button has none).
        timers=importlib.import_module("jarvis_wakeup_test.panel_timers")
        registry=NS(async_get=Mock(return_value=NS(device_id="panel-device")))
        with patch.object(timers.er,"async_get",return_value=registry), \
             patch.object(timers.intent,"async_handle",AsyncMock()) as handle:
            await timers.start("hass",10)
            await timers.cancel("hass")
            registry.async_get.assert_called_with(wake.SATELLITE)
            self.assertEqual(handle.await_args_list[0].args[2:],(timers.intent.INTENT_START_TIMER,{"minutes":{"value":10}}))
            self.assertEqual(handle.await_args_list[0].kwargs["device_id"],"panel-device")
            self.assertEqual(handle.await_args_list[1].args[2],timers.intent.INTENT_CANCEL_ALL_TIMERS)
            registry.async_get.return_value=None              # the panel not set up: a clear error, no intent
            with self.assertRaises(timers.HomeAssistantError):
                await timers.start("hass",5)
            self.assertEqual(handle.await_count,2)

    async def test_one_panel_timer_by_id_and_never_another_devices(self):
        # Hemmet's reminder page (2054-11-03): Avbryt and +1 min on one timer, by the id the panel got.
        timers=importlib.import_module("jarvis_wakeup_test.panel_timers")
        registry=NS(async_get=Mock(return_value=NS(device_id="panel-device")))
        manager=NS(timers={"a":NS(device_id="panel-device",name="pasta",seconds_left=90),
                           "b":NS(device_id="kitchen",name="ugn",seconds_left=60)},
                   cancel_timer=Mock(),add_time=Mock())
        hass=NS(data={timers.TIMER_DATA:manager})
        with patch.object(timers.er,"async_get",return_value=registry):
            timers.add_time(hass,"a",60)
            timers.cancel_one(hass,"a")
            manager.add_time.assert_called_once_with("a",60)
            manager.cancel_timer.assert_called_once_with("a")
            for wrong in ("b","gone"):
                with self.assertRaises(timers.HomeAssistantError):
                    timers.cancel_one(hass,wrong)
            self.assertEqual(manager.cancel_timer.call_count,1)

    async def test_snooze_and_stop_are_offered_only_during_and_shortly_after_a_wakeup(self):
        ctrl=self.controller()
        self.assertFalse(ctrl.active(NOW))
        ctrl.saved["last_due"]=(NOW-timedelta(minutes=10)).isoformat()
        self.assertTrue(ctrl.active(NOW))
        self.assertFalse(ctrl.active(NOW+timedelta(minutes=25)))
        ctrl.hass.states["input_boolean.jarvis_vackning_snooze"]=NS(state="on")
        self.assertTrue(ctrl.active(NOW+timedelta(minutes=25)))

    async def test_an_own_wakeup_carries_the_wish_to_preparation_claim_and_snooze(self):
        states=states_for_schedule()
        states["input_select.jarvis_vackning_engang_typ"].state="Egen väckning"
        states[wake.WISH]=NS(state="  Väck mig och  Alex ",attributes={})
        plan=wake.plans(states,NOW)[0]
        self.assertEqual((plan.kind,plan.wish,plan.source),("custom","Väck mig och Alex","oneoff"))
        self.assertEqual(wake.payload(plan),{"due":plan.due.isoformat(),"kind":"custom","wish":"Väck mig och Alex"})
        self.assertIn("Egen väckning",wake.describe(plan,NOW))
        self.assertNotIn("wish",wake.payload(wake.plans(states_for_schedule(),NOW)[0]))
        for empty in ("","unknown"):        # an empty wish is a normal morning, never a bare custom request
            states[wake.WISH].state=empty
            self.assertEqual((wake.plans(states,NOW)[0].kind,wake.plans(states,NOW)[0].wish),("normal",""))
        self.assertIn(wake.WISH,wake.CONTROLS)
        ctrl=self.controller();ctrl.hass.states.update(states);ctrl.hass.states[wake.WISH].state="Väck mig och Alex"
        ctrl.client.wakeup=AsyncMock(return_value={"writer":"claude","sources":{}})
        with patch.object(wake.dt_util,"now",return_value=NOW):await ctrl.prepare(wake.plans(ctrl.hass.states,NOW)[0])
        self.assertEqual(ctrl.client.wakeup.call_args.args,("prepare",wake.payload(plan)))
        snoozed={"input_boolean.jarvis_vackning_snooze":NS(state="on",attributes={}),
                 "input_datetime.jarvis_vackning_snooze":NS(state="2054-10-26 08:50:00",attributes={})}
        again=wake.plans(snoozed,NOW,"custom","Väck mig och Alex")[0]
        self.assertEqual((again.kind,again.wish,again.source),("custom","Väck mig och Alex","snooze"))

    async def test_preview_uses_the_next_wakeups_content(self):
        ctrl=self.controller();ctrl.play=AsyncMock()
        ctrl.next_plans=lambda now=None:[wake.Plan(NOW,"office","weekly")]
        await ctrl.preview();await asyncio.sleep(0)
        self.assertEqual(ctrl.play.call_args.args[0].kind,"office")
        ctrl.next_plans=lambda now=None:[];ctrl.play_task=None
        await ctrl.preview();await asyncio.sleep(0)
        self.assertEqual(ctrl.play.call_args.args[0].kind,"normal")

    async def test_the_log_is_one_local_line_per_event_and_the_minute_tick_does_not_repeat_it(self):
        self.assertEqual(wake.log_line(datetime(2054,10,29,7,45,3,tzinfo=wake.ZoneInfo("UTC")),"Spelad:\n  torsdag"),
            "2054-10-29 08:45:03  Spelad: torsdag")
        ctrl=self.controller()
        with patch.object(wake,"async_track_point_in_time",return_value=Mock()):
            for minute in range(3):await ctrl.tick(NOW+timedelta(minutes=minute))
            self.assertEqual(ctrl.log_lines,["2054-10-25 12:00:00  Nästa väckning: måndag 26 okt 08:45 · Vanlig morgon (engång)"])
            ctrl.hass.states["input_boolean.jarvis_vackning_engang"].state="off"
            await ctrl.tick(NOW+timedelta(minutes=3))
            for key in [k for k in ctrl.hass.states if k.startswith("input_boolean.")]:ctrl.hass.states[key].state="off"
            await ctrl.tick(NOW+timedelta(minutes=4));await ctrl.tick(NOW+timedelta(minutes=5))
        self.assertEqual([line[21:] for line in ctrl.log_lines[1:]],
            ["Nästa väckning: tisdag 27 okt 08:45 · Kontorsmorgon (vecka)","Ingen väckning – inga dagar valda"])

    async def test_a_setting_change_names_the_entity_old_and_new_value_and_who(self):
        ctrl=self.controller();ctrl.tick=AsyncMock()
        ctrl.hass.auth=NS(async_get_user=AsyncMock(return_value=NS(name="Joel")))
        old=NS(state="08:45:00",entity_id="input_datetime.jarvis_vackning_mandag_tid",attributes={"friendly_name":"Måndag tid"})
        new=NS(state="09:00:00",entity_id=old.entity_id,attributes=old.attributes)
        await ctrl.on_change(NS(data={"old_state":old,"new_state":new},context=NS(user_id="d8615bef")))
        await ctrl.on_change(NS(data={"old_state":new,"new_state":old},context=NS(user_id=None)))
        await ctrl.on_change(NS(data={"old_state":old,"new_state":old},context=NS(user_id=None)))   # attributes only
        self.assertEqual([line[21:] for line in ctrl.log_lines],[
            "Inställning: Måndag tid (input_datetime.jarvis_vackning_mandag_tid) 08:45:00 → 09:00:00, av Joel",
            "Inställning: Måndag tid (input_datetime.jarvis_vackning_mandag_tid) 09:00:00 → 08:45:00, av system"])
        self.assertIn("input_select.jarvis_vackning_radio",wake.SETTINGS)
        self.assertEqual(ctrl.tick.await_count,3)

    async def test_the_log_reaches_the_file_in_order_through_the_executor(self):
        import tempfile
        ctrl=self.controller()
        with tempfile.TemporaryDirectory() as tmp:
            ctrl.log_path=Path(tmp)/"jarvis_logs"/"vackning.log"
            ctrl.hass.async_add_executor_job=lambda fn,*args:asyncio.to_thread(fn,*args)
            for n in range(5):ctrl.note(f"rad {n}",NOW)
            await ctrl._flushing
            self.assertEqual(ctrl.log_path.read_text().splitlines(),[f"2054-10-25 12:00:00  rad {n}" for n in range(5)])

    async def test_played_failed_and_radio_events_are_logged(self):
        ctrl=self.controller();ctrl.hass.states[wake.SATELLITE]=NS(state="idle",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="idle",attributes={"volume_level":.2})
        async def service(domain,name,data):
            if name=="volume_set":ctrl.hass.states[wake.PLAYER].attributes["volume_level"]=data["volume_level"]
        ctrl.service=AsyncMock(side_effect=service)
        ctrl.client.wakeup=AsyncMock(return_value={"claimed":True,"media_id":"x","message":"Hej","writer":"reserve"})
        ctrl.hass.states["input_select.jarvis_vackning_radio"]=NS(state="P3",attributes={})
        ctrl.client.radio=AsyncMock(side_effect=wake.HomeAssistantError("Jarvis restarting"))
        plan=wake.plans(ctrl.hass.states,NOW)[0]
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):await ctrl.play(plan)
        lines=[line[21:] for line in ctrl.log_lines]
        self.assertIn("Spelad: måndag 26 okt 08:45 · Vanlig morgon (engång), volym 65 %, reservhälsning",lines)
        self.assertIn("Radio efter väckningen startade inte: HomeAssistantError: Jarvis restarting",lines)
        async def boom():raise wake.HomeAssistantError("Panelen används")
        task=ctrl.spawn(boom(),"väckning");await asyncio.gather(task,return_exceptions=True);await asyncio.sleep(0)
        self.assertEqual(ctrl.log_lines[-1][21:],"Fel (väckning): HomeAssistantError: Panelen används")

    async def test_startup_and_helper_callbacks_stay_on_the_ha_event_loop(self):
        ctrl=self.controller()
        self.assertEqual(HassJob(ctrl.start).job_type,HassJobType.Callback)
        self.assertEqual(HassJob(ctrl.changed).job_type,HassJobType.Callback)

    async def test_tuesday_has_its_own_time_and_live_edits_leave_thursday_and_oneoff_unchanged(self):
        states=states_for_schedule()
        key="input_datetime.jarvis_vackning_tisdag_tid"
        states[key]=NS(state="08:00:00")
        self.assertIn(key,wake.CONTROLS)
        self.assertEqual([(p.due.strftime("%Y-%m-%d %H:%M"),p.kind) for p in wake.plans(states,NOW)[:3]],
            [("2054-10-26 08:45","normal"),("2054-10-27 08:00","office"),("2054-10-29 08:45","office")])
        states[key].state="07:55:00"
        self.assertEqual(wake.plans(states,NOW)[1].due.strftime("%H:%M"),"07:55")
        self.assertEqual(wake.plans(states,NOW)[2].due.strftime("%H:%M"),"08:45")
        states[key].state="unavailable"
        self.assertEqual(wake.plans(states,NOW)[1].due.strftime("%H:%M"),"08:45")

    async def test_status_reports_the_armed_timer_as_well_as_the_configured_next_wakeup(self):
        ctrl=self.controller();due=wake.plans(ctrl.hass.states,NOW)[0].due
        ctrl.alarm_at=due.timestamp()
        ctrl.next_plans=lambda:[wake.Plan(due,"normal","oneoff")]
        client=NS(wakeups=ctrl,wakeup=AsyncMock(return_value={"reserves_ready":["normal","office"]}))
        entity=sensor.JarvisWakeup(NS(entry_id="test",runtime_data=client))
        await entity.async_update()
        self.assertEqual(entity.native_value,due)
        self.assertTrue(entity.extra_state_attributes["klocka_aktiv"])
        self.assertEqual(datetime.fromisoformat(entity.extra_state_attributes["alarmtid"]).timestamp(),due.timestamp())

    async def test_status_shows_the_armed_clock_once_an_old_outcome_has_left_the_snooze_window(self):
        # 2054-10-26: stopped at 08:45, one-off armed for the next morning, dashboard still said stopped at 22:37.
        ctrl=self.controller();ctrl.saved["last_due"]="2054-10-26T08:45:00+01:00"
        morning=datetime(2054,10,26,8,50,tzinfo=wake.TZ);evening=datetime(2054,10,26,22,37,tzinfo=wake.TZ)
        ctrl.phase="Väckning stoppad";ctrl.alarm_at=datetime(2054,10,27,8,45,tzinfo=wake.TZ).timestamp()
        self.assertEqual(ctrl.status(morning),"Väckning stoppad")
        self.assertEqual(ctrl.status(evening),"Schemalagd")
        ctrl.alarm_at=None
        self.assertEqual(ctrl.status(evening),"Ingen väckning schemalagd")
        ctrl.phase="Snoozad i fem minuter";ctrl.alarm_at=1.0
        ctrl.hass.states["input_boolean.jarvis_vackning_snooze"]=NS(state="on")
        self.assertEqual(ctrl.status(morning),"Snoozad i fem minuter")
        ctrl.hass.states["input_boolean.jarvis_vackning_snooze"].state="off"
        self.assertEqual(ctrl.status(morning),"Schemalagd")
        ctrl.phase="Fel: HomeAssistantError"
        self.assertEqual(ctrl.status(evening),"Fel: HomeAssistantError")

    async def test_oneoff_wins_same_slot_and_dst_is_one_correct_instant(self):
        states=states_for_schedule();states["input_boolean.jarvis_vackning_mandag"]=NS(state="on")
        first=wake.plans(states,NOW)[0]
        self.assertEqual(first.source,"oneoff")
        states["input_datetime.jarvis_vackning_engang"].state="2054-10-25 08:45:00"
        plan=wake.plans(states,NOW.replace(hour=8,minute=44))[0]
        self.assertEqual(plan.due.isoformat(),"2054-10-25T08:45:00+01:00")
        self.assertEqual(plan.due.astimezone(wake.ZoneInfo("UTC")).strftime("%H:%M"),"07:45")

    async def test_prepare_is_separate_from_play_and_exact_alarm_timer_is_registered(self):
        ctrl=self.controller();ctrl.prepare=AsyncMock();ctrl.play=AsyncMock()
        due=wake.plans(ctrl.hass.states,NOW)[0].due
        with patch.object(wake,"async_track_point_in_time",return_value=Mock()) as timer:
            await ctrl.tick(due-timedelta(minutes=3));await asyncio.sleep(0)
            await ctrl.tick(due-timedelta(minutes=2));await asyncio.sleep(0)
            ctrl.prepare.assert_awaited_once();ctrl.play.assert_not_awaited()
            self.assertEqual(timer.call_args.args[2],due)
            await ctrl.tick(due);await asyncio.sleep(0)
            await ctrl.tick(due+timedelta(seconds=10));await asyncio.sleep(0)
            ctrl.play.assert_awaited_once()

    async def test_busy_panel_does_not_claim_or_interrupt_any_session(self):
        ctrl=self.controller();ctrl.hass.states[wake.SATELLITE]=NS(state="responding",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="playing",attributes={"volume_level":.2})
        plan=wake.plans(ctrl.hass.states,NOW)[0]
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):
            with self.assertRaises(wake.HomeAssistantError):await ctrl.play(plan)
        ctrl.client.wakeup.assert_not_awaited();ctrl.hass.services.async_call.assert_not_awaited()

    async def test_failed_preparation_can_retry_without_repeating_a_successful_preparation(self):
        ctrl=self.controller();plan=wake.plans(ctrl.hass.states,NOW)[0]
        ctrl.arm_night=AsyncMock()
        ctrl.client.wakeup=AsyncMock(side_effect=[wake.HomeAssistantError("server restarting"),{}])
        with patch.object(wake,"async_track_point_in_time",return_value=Mock()):
            await ctrl.tick(plan.due-timedelta(minutes=3))
            await asyncio.gather(*list(ctrl.tasks),return_exceptions=True)
            await ctrl.tick(plan.due-timedelta(minutes=2))
            await asyncio.gather(*list(ctrl.tasks),return_exceptions=True)
            await ctrl.tick(plan.due-timedelta(minutes=1))
            await asyncio.gather(*list(ctrl.tasks),return_exceptions=True)
        self.assertEqual(ctrl.client.wakeup.await_count,2)

    async def test_busy_panel_failure_can_retry_inside_catchup_window(self):
        ctrl=self.controller();plan=wake.plans(ctrl.hass.states,NOW)[0]
        ctrl.hass.states[wake.SATELLITE]=NS(state="responding",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="playing",attributes={"volume_level":.2})
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()),patch.object(wake,"async_track_point_in_time",return_value=Mock()):
            await ctrl.tick(plan.due)
            await asyncio.gather(*list(ctrl.tasks),return_exceptions=True)
        self.assertNotIn(plan.due.isoformat(),ctrl.attempted)
        ctrl.hass.states[wake.SATELLITE].state="idle"
        ctrl.hass.states[wake.PLAYER].state="idle"
        ctrl.play=AsyncMock()
        with patch.object(wake,"async_track_point_in_time",return_value=Mock()):
            await ctrl.tick(plan.due+timedelta(seconds=60))
            await asyncio.gather(*list(ctrl.tasks),return_exceptions=True)
        ctrl.play.assert_awaited_once()

    async def test_volume_failure_never_consumes_the_durable_claim_or_oneoff(self):
        ctrl=self.controller();plan=wake.plans(ctrl.hass.states,NOW)[0]
        ctrl.hass.states[wake.SATELLITE]=NS(state="idle",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="idle",attributes={"volume_level":.2})
        ctrl.service=AsyncMock(side_effect=wake.HomeAssistantError("device disconnected"))
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):
            with self.assertRaises(wake.HomeAssistantError):await ctrl.play(plan)
        ctrl.client.wakeup.assert_not_awaited()
        self.assertNotIn("last_due",ctrl.saved)

    async def test_uncertain_claim_is_not_retried(self):
        ctrl=self.controller();plan=wake.plans(ctrl.hass.states,NOW)[0]
        ctrl.hass.states[wake.SATELLITE]=NS(state="idle",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="idle",attributes={"volume_level":.65})
        ctrl.client.wakeup=AsyncMock(side_effect=wake.HomeAssistantError("claim response lost"))
        ctrl.service=AsyncMock()
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()),patch.object(wake,"async_track_point_in_time",return_value=Mock()):
            await ctrl.tick(plan.due)
            await asyncio.gather(*list(ctrl.tasks),return_exceptions=True)
            await ctrl.tick(plan.due+timedelta(seconds=60))
            await asyncio.gather(*list(ctrl.tasks),return_exceptions=True)
        self.assertEqual(ctrl.client.wakeup.await_count,1)
        self.assertIn(plan.due.isoformat(),ctrl.attempted)

    async def test_failed_night_setting_can_retry_without_losing_the_original_boundary(self):
        ctrl=self.controller();due=NOW.replace(hour=8,minute=45)
        ctrl.hass.states[wake.NIGHT_START]=NS(state="22:00:00")
        ctrl.hass.states[wake.NIGHT_END]=NS(state="09:00:00")
        ctrl.service=AsyncMock(side_effect=wake.HomeAssistantError("device restarting"))
        with self.assertRaises(wake.HomeAssistantError):
            await ctrl.arm_night(due,now=due-timedelta(minutes=3))
        async def recovered(domain,name,data):ctrl.hass.states[wake.NIGHT_END].state=data['time']
        ctrl.service=AsyncMock(side_effect=recovered)
        await ctrl.arm_night(due,now=due-timedelta(minutes=2))
        ctrl.service.assert_awaited_once()
        self.assertEqual(ctrl.hass.states[wake.NIGHT_END].state,"08:44:00")
        self.assertEqual(ctrl.saved['night_override']['old'],"09:00:00")

    async def test_consuming_old_alarm_preserves_a_later_schedule_edit(self):
        ctrl=self.controller();plan=wake.plans(ctrl.hass.states,NOW)[0]
        ctrl.hass.states['input_datetime.jarvis_vackning_engang'].state='2054-10-27 09:00:00'
        ctrl.service=AsyncMock()
        await ctrl.consume(plan)
        ctrl.service.assert_not_awaited()

    async def test_pending_night_override_preserves_manual_edits(self):
        ctrl=self.controller();due=NOW.replace(hour=8,minute=45)
        ctrl.hass.states[wake.NIGHT_START]=NS(state='22:00:00')
        ctrl.hass.states[wake.NIGHT_END]=NS(state='09:00:00')
        ctrl.service=AsyncMock(side_effect=wake.HomeAssistantError('disconnected'))
        with self.assertRaises(wake.HomeAssistantError):
            await ctrl.arm_night(due,now=due-timedelta(minutes=3))
        ctrl.hass.states[wake.NIGHT_END].state='09:30:00'
        ctrl.service=AsyncMock()
        await ctrl.arm_night(due,now=due-timedelta(minutes=2))
        ctrl.service.assert_not_awaited()
        self.assertNotIn('night_override',ctrl.saved)

    async def test_play_uses_finished_media_at_selected_volume_and_consumes_only_oneoff(self):
        ctrl=self.controller();ctrl.hass.states[wake.SATELLITE]=NS(state="idle",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="idle",attributes={"volume_level":.2})
        async def service(domain,name,data):
            if name=="volume_set":ctrl.hass.states[wake.PLAYER].attributes["volume_level"]=data["volume_level"]
        ctrl.service=AsyncMock(side_effect=service)
        plan=wake.plans(ctrl.hass.states,NOW)[0]
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):await ctrl.play(plan)
        calls=ctrl.service.call_args_list
        announce=next(c for c in calls if c.args[:2]==("assist_satellite","announce"))
        self.assertEqual(announce.args[2]["media_id"],"http://example.test/alarm.wav")
        self.assertIn("volume_set",[c.args[1] for c in calls])
        self.assertEqual(ctrl.hass.states[wake.PLAYER].attributes["volume_level"],.65)
        ctrl.service.assert_any_await("input_boolean","turn_off",{"entity_id":"input_boolean.jarvis_vackning_engang"})

    async def test_night_override_survives_restart_and_preserves_later_manual_changes(self):
        ctrl=self.controller();ctrl.hass.states[wake.NIGHT_START]=NS(state="22:00:00")
        ctrl.hass.states[wake.NIGHT_END]=NS(state="09:00:00")
        due=NOW.replace(hour=8,minute=45)
        async def service(domain,name,data):ctrl.hass.states[wake.NIGHT_END].state=data["time"]
        ctrl.service=AsyncMock(side_effect=service)
        await ctrl.arm_night(due,now=due-timedelta(minutes=3))
        self.assertEqual(ctrl.hass.states[wake.NIGHT_END].state,"08:44:00")
        ctrl.store.async_save.assert_awaited()
        await ctrl.restore_night(due.replace(hour=8,minute=59))
        self.assertEqual(ctrl.hass.states[wake.NIGHT_END].state,"08:44:00")
        ctrl.hass.states[wake.NIGHT_END].state="09:15:00"
        await ctrl.restore_night(due.replace(hour=9,minute=0))
        self.assertEqual(ctrl.hass.states[wake.NIGHT_END].state,"09:15:00")
        self.assertNotIn("night_override",ctrl.saved)

    async def test_restart_after_night_edge_chooses_a_future_edge_and_preview_leaves_night_settings_alone(self):
        ctrl=self.controller();ctrl.hass.states[wake.NIGHT_START]=NS(state="22:00:00")
        ctrl.hass.states[wake.NIGHT_END]=NS(state="09:00:00")
        due=NOW.replace(hour=8,minute=45)
        ctrl.service=AsyncMock()
        await ctrl.arm_night(due,preview=True,now=due)
        ctrl.service.assert_not_awaited()
        await ctrl.arm_night(due,now=due+timedelta(seconds=20))
        self.assertEqual(ctrl.saved["night_override"]["set"],"08:46:00")

    async def test_disabling_an_armed_future_alarm_restores_the_previous_night_boundary(self):
        ctrl=self.controller();due=wake.plans(ctrl.hass.states,NOW)[0].due
        ctrl.hass.states[wake.NIGHT_START]=NS(state="22:00:00")
        ctrl.hass.states[wake.NIGHT_END]=NS(state="09:00:00")
        async def service(domain,name,data):ctrl.hass.states[wake.NIGHT_END].state=data["time"]
        ctrl.service=AsyncMock(side_effect=service)
        await ctrl.arm_night(due,now=due-timedelta(minutes=3))
        ctrl.hass.states["input_boolean.jarvis_vackning_engang"].state="off"
        with patch.object(wake,"async_track_point_in_time",return_value=Mock()):
            await ctrl.tick(due-timedelta(minutes=2))
        self.assertEqual(ctrl.hass.states[wake.NIGHT_END].state,"09:00:00")

    async def test_the_radio_starts_after_the_greeting_only_when_chosen_and_never_fails_the_wakeup(self):
        ctrl=self.controller();ctrl.hass.states[wake.SATELLITE]=NS(state="idle",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="idle",attributes={"volume_level":.2})
        async def service(domain,name,data):
            if name=="volume_set":ctrl.hass.states[wake.PLAYER].attributes["volume_level"]=data["volume_level"]
        ctrl.service=AsyncMock(side_effect=service)
        ctrl.client.radio=AsyncMock(return_value={"said":["P3"]})
        plan=wake.plans(ctrl.hass.states,NOW)[0]
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):await ctrl.play(plan)
        ctrl.client.radio.assert_not_awaited()                          # no helper: no radio
        ctrl.hass.states["input_select.jarvis_vackning_radio"]=NS(state="P3",attributes={})
        ctrl.attempted.clear()
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):await ctrl.play(plan)
        ctrl.client.radio.assert_awaited_once_with("P3",connect=True)
        ctrl.client.radio=AsyncMock(side_effect=wake.HomeAssistantError("Jarvis restarting"))
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):await ctrl.play(plan)   # still a finished wakeup
        self.assertEqual(ctrl.phase,"Väckning klar, radion startade inte")
        ctrl.hass.states["input_select.jarvis_vackning_radio"].state="Av"
        ctrl.client.radio=AsyncMock()
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):await ctrl.play(plan, preview=True)
        ctrl.client.radio.assert_not_awaited()

    async def test_stop_targets_only_owned_announcement_with_real_native_api_signature(self):
        ctrl=self.controller();native=NS(_key=123,_static_info=NS(device_id=0),_client=NS(media_player_command=Mock()))
        ctrl.hass.data[wake.media_player.DATA_COMPONENT]=NS(get_entity=lambda entity:native)
        ctrl.play_task=asyncio.create_task(asyncio.Event().wait());ctrl.announcing=True
        await ctrl.stop()
        kwargs=native._client.media_player_command.call_args.kwargs
        self.assertTrue(kwargs["announcement"])
        inspect.signature(APIClient.media_player_command).bind(None,123,**kwargs)
        self.assertTrue(ctrl.play_task.cancelled() if ctrl.play_task else True)

    async def test_snooze_preserves_mode_and_schedules_exactly_five_minutes_without_changing_weekly_time(self):
        ctrl=self.controller();now=NOW.replace(hour=8,minute=46,second=37)
        ctrl.saved={"last_due":(now-timedelta(minutes=1)).isoformat(),"last_kind":"office"}
        ctrl.service=AsyncMock()
        with patch.object(wake.dt_util,"now",return_value=now):await ctrl.snooze()
        ctrl.service.assert_any_await("input_datetime","set_datetime",{
            "entity_id":"input_datetime.jarvis_vackning_snooze","datetime":"2054-10-25 08:51:37"})
        self.assertEqual(ctrl.saved["last_kind"],"office")
        self.assertEqual(ctrl.hass.states["input_datetime.jarvis_vackning_tid"].state,"08:45:00")
        ctrl.saved={}
        with self.assertRaises(wake.HomeAssistantError):await ctrl.snooze()

    async def test_preview_restores_volume_and_does_not_change_scheduled_alarms(self):
        ctrl=self.controller();ctrl.hass.states[wake.SATELLITE]=NS(state="idle",attributes={})
        ctrl.hass.states[wake.PLAYER]=NS(state="idle",attributes={"volume_level":.2})
        async def service(domain,name,data):
            if name=="volume_set":ctrl.hass.states[wake.PLAYER].attributes["volume_level"]=data["volume_level"]
        ctrl.service=AsyncMock(side_effect=service)
        with patch.object(wake.asyncio,"sleep",new=AsyncMock()):
            await ctrl.play(wake.Plan(NOW,"office","preview"),preview=True)
        self.assertEqual(ctrl.hass.states[wake.PLAYER].attributes["volume_level"],.2)
        self.assertNotIn("last_due",ctrl.saved)
        self.assertFalse(any(c.args[0]=="input_boolean" for c in ctrl.service.call_args_list))

    async def test_stop_without_owned_wakeup_never_stops_music_or_other_voice(self):
        ctrl=self.controller();native=Mock()
        ctrl.hass.data[wake.media_player.DATA_COMPONENT]=NS(get_entity=native)
        await ctrl.stop()
        native.assert_not_called()


if __name__=="__main__":unittest.main()
