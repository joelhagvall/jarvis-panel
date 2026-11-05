#!/usr/bin/env python3
"""Jarvis's Bluetooth speaker (server/agents/speaker.py) without Bluetooth: a fake bluetoothctl with a paired
JBL, a paired phone and a second speaker, and the radio's moves checked against a fake Home Assistant.

  python3 -m unittest tests/test_speaker.py
"""
import os
import socket
import sys
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import radio  # noqa: E402
import speaker  # noqa: E402

JBL, PHONE, SONOS = "02:00:00:00:00:04", "02:00:00:00:00:05", "02:00:00:00:00:06"
SINK = "UUID: Audio Sink                (0000110b-0000-1000-8000-00805f9b34fb)"


class FakeCtl:
    def __init__(self, connected=()):
        self.devices = {JBL: ["Name: JBL Charge 4", "Alias: JBL Charge 4", "Icon: audio-card", "Paired: yes", SINK],
                        PHONE: ["Name: iPhone", "Alias: iPhone", "Icon: phone", "Paired: yes"],
                        SONOS: ["Name: Roam", "Alias: Roam", "Icon: audio-headset", "Paired: yes", SINK]}
        self.connected, self.calls = set(connected), []

    def __call__(self, *args, timeout=10):
        self.calls.append(args)
        if args[0] == "devices":
            macs = [m for m in self.devices if "Connected" not in args or m in self.connected]
            return "".join(f"Device {m} {self.devices[m][0][6:]}\n" for m in macs)
        if args[0] == "info":
            m = args[1]
            return "\n".join(f"\t{line}" for line in self.devices[m] + [f"Connected: {'yes' if m in self.connected else 'no'}"])
        if args[0] == "connect":
            self.connected.add(args[1])
        if args[0] == "disconnect":
            self.connected.discard(args[1])
        return ""


class SpeakerTest(unittest.TestCase):
    def setUp(self):
        os.environ.update(JARVIS_SPEAKER_PLAYER="media_player.bluetooth_hogtalare",
                          JARVIS_RADIO_PLAYER="media_player.hallpanel")
        radio._playing.clear()

    def tearDown(self):
        for key in ("JARVIS_SPEAKER_PLAYER", "JARVIS_RADIO_PLAYER"):
            os.environ.pop(key, None)
        radio._playing.clear()

    def test_only_paired_speakers_count_and_the_connected_one_comes_first(self):
        with mock.patch.object(speaker, "_ctl", FakeCtl(connected=[SONOS, PHONE])):
            self.assertEqual([s["name"] for s in speaker.speakers()], ["Roam", "JBL Charge 4"])
            self.assertEqual(speaker.connected()["name"], "Roam")       # the phone is not a speaker

    def test_the_radio_plays_where_a_speaker_is_connected(self):
        with mock.patch.object(speaker, "_ctl", FakeCtl()):
            self.assertEqual(speaker.output(), ("media_player.hallpanel", "panelens högtalare"))
        with mock.patch.object(speaker, "_ctl", FakeCtl(connected=[JBL])):
            self.assertEqual(speaker.output(), ("media_player.bluetooth_hogtalare", "JBL Charge 4"))
        os.environ.pop("JARVIS_SPEAKER_PLAYER")                         # not set up: never asks Bluetooth
        with mock.patch.object(speaker, "_ctl", side_effect=AssertionError("asked Bluetooth")):
            self.assertEqual(speaker.output(), ("media_player.hallpanel", "panelens högtalare"))
            self.assertFalse(speaker.configured())

    def test_connect_by_spoken_name_and_one_speaker_at_a_time(self):
        ctl = FakeCtl(connected=[SONOS])
        with mock.patch.object(speaker, "_ctl", ctl), mock.patch.object(radio, "follow_output", return_value=""):
            error, text = speaker.control("connect", "JBL-högtalaren")
        self.assertFalse(error, text)
        self.assertEqual(ctl.connected, {JBL})
        self.assertIn(("disconnect", SONOS), ctl.calls)
        self.assertTrue(text.startswith("JBL Charge 4 är kopplad."), text)

    def test_connect_asks_which_when_the_name_does_not_settle_it(self):
        with mock.patch.object(speaker, "_ctl", FakeCtl()):
            error, text = speaker.control("connect", None)
        self.assertTrue(error)
        self.assertIn("JBL Charge 4", text)
        self.assertIn("Roam", text)

    def test_a_speaker_that_does_not_answer(self):
        ctl = FakeCtl()
        real = ctl.__call__
        ctl_off = lambda *a, timeout=10: "" if a[0] == "connect" else real(*a, timeout=timeout)
        with mock.patch.object(speaker, "_ctl", ctl_off), mock.patch.object(speaker, "_wait_connected", return_value=False):
            error, text = speaker.control("connect", "jbl")
        self.assertEqual((error, text), (True, "JBL Charge 4 svarar inte. Är den på och inom räckhåll?"))

    def test_disconnect_moves_a_playing_radio_to_the_panel(self):
        """The radio follows the output both ways: off the speaker it goes on on the panel (Joel, 2026-10-27)."""
        ctl = FakeCtl(connected=[JBL])
        calls = []
        def mpd(*commands, timeout=5):
            calls.extend(commands)
            return [{"state": "play"}, {}] if commands == ("status", "currentsong") else [{}]
        played = []
        with mock.patch.object(speaker, "_ctl", ctl), mock.patch.object(speaker, "mpd", mpd), \
                mock.patch.object(radio, "play", lambda channel: (played.append(channel), (False, ""))[1]):
            radio._playing.update(channel="P3", player="media_player.bluetooth_hogtalare")
            error, text = speaker.control("connect", "vanliga högtalaren")      # = Panelen
        self.assertFalse(error, text)
        self.assertEqual(calls, ["status", "currentsong", "stop"])
        self.assertEqual(ctl.connected, set())
        self.assertEqual(played, ["P3"])
        self.assertEqual(text, "JBL Charge 4 är frånkopplad. P3 spelas nu på panelen.")

    def test_the_channel_on_the_speaker_comes_from_mpd_when_this_process_did_not_start_it(self):
        """After a server restart, or from speaker.py in a terminal, a disconnect still moves the radio."""
        mpd = lambda *commands, timeout=5: [{"state": "play"},
                                            {"file": "https://www.sverigesradio.se/topsy/direkt/srapi/164.mp3"}]
        with mock.patch.object(speaker, "mpd", mpd), \
                mock.patch.object(radio, "channels", return_value=[{"id": 164, "name": "P3"}, {"id": 132, "name": "P1"}]):
            self.assertEqual(radio.on_speaker(), "P3")
        stopped = lambda *commands, timeout=5: [{"state": "stop"}, {}]
        with mock.patch.object(speaker, "mpd", stopped):
            self.assertIsNone(radio.on_speaker())

    def test_disconnect_with_nothing_playing_moves_nothing(self):
        ctl = FakeCtl(connected=[JBL])
        with mock.patch.object(speaker, "_ctl", ctl), mock.patch.object(speaker, "mpd", return_value=[{"state": "stop"}, {}]), \
                mock.patch.object(radio, "play", side_effect=AssertionError("played")):
            self.assertEqual(speaker.choose("Panelen"),
                             (False, "JBL Charge 4 är frånkopplad. Radion spelas på panelen nästa gång."))

    def test_a_connected_speaker_takes_over_a_playing_radio(self):
        played = []
        radio._playing.update(channel="P3", player="media_player.hallpanel")
        fake_play = lambda channel: (played.append(channel), (False, ""))[1]
        with mock.patch.object(speaker, "_ctl", FakeCtl(connected=[JBL])), \
                mock.patch.object(radio.hass, "get_json", return_value={"state": "playing"}), \
                mock.patch.object(radio, "play", fake_play), \
                mock.patch.object(radio, "_ha", return_value=(False, "")) as ha:
            self.assertEqual(radio.follow_output(), "P3 spelas nu på JBL Charge 4.")
        self.assertEqual(played, ["P3"])
        ha.assert_called_once_with("media_stop", {}, "media_player.hallpanel")

    def test_a_station_on_the_panel_follows_from_what_home_assistant_shows(self):
        played = []
        shown = {"state": "playing", "attributes": {"control_entity": "media_player.hallpanel", "radio_channel": "P1"}}
        states = {"/api/states/sensor.jarvis_uppspelning": shown, "/api/states/media_player.hallpanel": {"state": "playing"}}
        with mock.patch.object(speaker, "_ctl", FakeCtl(connected=[JBL])), \
                mock.patch.object(radio.hass, "get_json", lambda path, timeout=10: states.get(path)), \
                mock.patch.object(radio, "play", lambda channel: (played.append(channel), (False, ""))[1]), \
                mock.patch.object(radio, "_ha", return_value=(False, "")) as ha:
            self.assertEqual(radio.follow_output(), "P1 spelas nu på JBL Charge 4.")
        self.assertEqual(played, ["P1"])
        ha.assert_called_once_with("media_stop", {}, "media_player.hallpanel")

    def test_a_stopped_radio_stays_stopped(self):
        radio._playing.update(channel="P3", player="media_player.hallpanel")
        with mock.patch.object(speaker, "_ctl", FakeCtl(connected=[JBL])), \
                mock.patch.object(radio.hass, "get_json", return_value={"state": "idle"}), \
                mock.patch.object(radio, "play", side_effect=AssertionError("played")):
            self.assertEqual(radio.follow_output(), "")

    def test_pairing_bonds_and_leaves_the_adapter_closed(self):
        """Jarvis's adapter is not pairable by default: a pairing then stores no keys (seen 2026-10-27)."""
        ctl = FakeCtl()
        ctl.devices[JBL] = [line for line in ctl.devices[JBL] if line != "Paired: yes"] + ["Paired: no"]
        real = ctl.__call__
        state = {"pairable": False}

        def fake(*args, timeout=10):
            if args[:1] == ("pairable",):
                state["pairable"] = args[1] == "on"
            if args[:1] == ("pair",):
                ctl.devices[JBL] = [line for line in ctl.devices[JBL] if line != "Paired: no"] + \
                    ["Paired: yes", f"Bonded: {'yes' if state['pairable'] else 'no'}"]
                return "Pairing successful\n"
            return real(*args, timeout=timeout)
        with mock.patch.object(speaker, "_ctl", fake), mock.patch.object(radio, "follow_output", return_value=""), \
                mock.patch("builtins.print"):
            error, text = speaker.pair("jbl")
        self.assertFalse(error, text)
        self.assertFalse(state["pairable"])
        self.assertIn(("trust", JBL), ctl.calls)

    def test_the_radio_goes_straight_to_mpd_not_through_home_assistant(self):
        """HA's MPD player took 12.3 s per play_media on Jarvis (2026-10-27); MPD itself answers at once."""
        server = socket.create_server(("127.0.0.1", 0))
        got = []

        def serve():
            conn, _ = server.accept()
            with conn, conn.makefile("rwb") as f:
                f.write(b"OK MPD 0.24.0\n")
                f.flush()
                while line := f.readline():
                    got.append(line.decode().strip())
                    f.write(b"OK\n")
                    f.flush()
        threading.Thread(target=serve, daemon=True).start()
        os.environ["JARVIS_SPEAKER_MPD"] = f"127.0.0.1:{server.getsockname()[1]}"
        try:
            with mock.patch.object(speaker, "_ctl", FakeCtl(connected=[JBL])), \
                    mock.patch.object(radio, "find", return_value={"id": 164, "name": "P3",
                                                                   "liveaudio": {"url": "https://sr.example/164.mp3"}}), \
                    mock.patch.object(radio, "_ha", side_effect=AssertionError("asked Home Assistant")):
                self.assertEqual(radio.play("P3"), (False, "Jarvis har startat P3 på JBL Charge 4."))
        finally:
            os.environ.pop("JARVIS_SPEAKER_MPD")
            server.close()
        self.assertEqual(got, ["clear", 'add "https://sr.example/164.mp3"', "play"])
        self.assertEqual(radio._playing, {"channel": "P3", "player": "media_player.bluetooth_hogtalare"})

    def test_a_deploy_restarts_mpd_only_when_its_files_changed(self):
        """Every deploy restarted MPD and cut the radio on the speaker (2026-10-27)."""
        deploy = (ROOT / "tools/deploy_jarvis.sh").read_text()
        restart = "systemctl --user restart hallpanel-mpd.service"
        self.assertEqual(deploy.count(restart), 1)
        guard = deploy.index('if [ "$MPD_SUM" != "$(cat ~/.config/hallpanel/mpd.sum')
        self.assertLess(guard, deploy.index(restart))
        self.assertLess(deploy.index(restart), deploy.index("systemctl --user start hallpanel-mpd.service"))

    def test_coloured_bluetoothctl_output_still_lists_the_speaker(self):
        """2026-11-01: bluetoothctl coloured "Device ..." even over ssh without a terminal; no speaker was found."""
        done = mock.Mock(stdout=f"\x1b[1;30mDevice {JBL} JBL Charge 4\x1b[0m\n")
        with mock.patch.object(speaker.subprocess, "run", return_value=done):
            self.assertEqual(speaker._devices("Paired"), [(JBL, "JBL Charge 4")])

    def test_missing_bluetooth_is_a_spoken_error(self):
        with mock.patch.object(speaker.subprocess, "run", side_effect=FileNotFoundError):
            self.assertEqual(speaker.control("status"), (True, "Bluetooth är inte installerat på Jarvis."))


class FakeWatch:
    """server/soloist/watch.py as speaker.reconnect sees it: who plays, the hold-off and the heal, in order."""
    def __init__(self, ctl, playing=True, active=None):
        self.ctl, self.here, self.calls = ctl, playing, []
        self.active = playing if active is None else active

    def active_here(self):
        return self.active

    def playing(self):
        return self.here

    def hold_off(self, seconds=120, heal=False):
        self.calls.append(("hold_off", seconds, heal, frozenset(self.ctl.connected)))

    def heal(self, resume, activate=False):
        self.calls.append(("heal", resume, frozenset(self.ctl.connected)))
        self.activated = activate

    def nodes(self):
        return {"spotify": "running", "jbl": "running"}


class ReconnectTest(unittest.TestCase):
    """The panel's Koppla om (2026-11-01): the JBL said connected and Spotify said playing, but nothing was heard."""
    def setUp(self):
        os.environ.update(JARVIS_SPEAKER_PLAYER="media_player.bluetooth_hogtalare",
                          JARVIS_RADIO_PLAYER="media_player.hallpanel")
        radio._playing.clear()
        speaker._settled.clear()
        self.said = []

    def tearDown(self):
        for key in ("JARVIS_SPEAKER_PLAYER", "JARVIS_RADIO_PLAYER"):
            os.environ.pop(key, None)
        radio._playing.clear()
        speaker._settled.clear()

    def reconnect(self, ctl, watch, connected=True, channel=None):
        with mock.patch.object(speaker, "_ctl", ctl), mock.patch.object(speaker, "_soloist_watch", return_value=watch), \
                mock.patch.object(speaker, "_wait_connected", return_value=connected), \
                mock.patch.object(speaker.time, "sleep"), \
                mock.patch.object(radio, "on_speaker", return_value=channel), \
                mock.patch.object(radio, "play", return_value=(False, "")) as play, \
                mock.patch.object(radio, "follow_output", return_value=""):
            result = speaker.reconnect(log=self.said.append, background=False)
        return result, play

    def test_a_silent_jbl_is_disconnected_connected_again_and_the_sound_path_healed(self):
        ctl = FakeCtl(connected=[JBL])
        watch = FakeWatch(ctl)
        (error, text), _ = self.reconnect(ctl, watch)
        self.assertFalse(error, text)
        self.assertLess(ctl.calls.index(("disconnect", JBL)), ctl.calls.index(("connect", JBL)))
        # the watch is held off before Jarvis's own drop, and PipeWire restarts only with the JBL back
        self.assertEqual(watch.calls, [("hold_off", 120, False, frozenset({JBL})),
                                       ("heal", True, frozenset({JBL})),
                                       ("hold_off", 0, False, frozenset({JBL}))])
        self.assertEqual(speaker._settled["mac"], JBL)   # the Watcher leaves Jarvis's own reconnect alone

    def test_a_jbl_that_dropped_off_is_connected_without_a_disconnect(self):
        ctl = FakeCtl()
        watch = FakeWatch(ctl, playing=False)
        (error, _), _ = self.reconnect(ctl, watch)
        self.assertFalse(error)
        self.assertNotIn(("disconnect", JBL), ctl.calls)
        self.assertIn(("heal", False, frozenset({JBL})), watch.calls)   # Spotify on the phone is not taken

    def test_a_jbl_that_does_not_come_back_is_never_healed_and_the_watch_takes_over(self):
        ctl = FakeCtl(connected=[JBL])
        watch = FakeWatch(ctl)
        (error, text), _ = self.reconnect(ctl, watch, connected=False)
        self.assertTrue(error)
        self.assertIn("inte kopplad till en telefon", text)
        self.assertFalse([c for c in watch.calls if c[0] == "heal"])
        self.assertEqual(watch.calls[-1][:3], ("hold_off", 0, True))   # heals by itself when the JBL is back

    def test_a_connect_that_times_out_is_tried_once_more(self):
        """2026-11-01 13:46: bluetoothctl connect right after the disconnect timed out after 20 s."""
        ctl = FakeCtl(connected=[JBL])
        first = []

        def flaky(*args, timeout=10):
            if args[0] == "connect" and not first:
                first.append(args)
                raise speaker.SpeakerError("Bluetooth på Jarvis svarade inte i tid.")
            return ctl(*args, timeout=timeout)
        watch = FakeWatch(ctl)
        with mock.patch.object(speaker, "_ctl", flaky), mock.patch.object(speaker, "_soloist_watch", return_value=watch), \
                mock.patch.object(speaker.time, "sleep"), mock.patch.object(radio, "on_speaker", return_value=None), \
                mock.patch.object(radio, "follow_output", return_value=""):
            error, text = speaker.reconnect(log=self.said.append, background=False)
        self.assertFalse(error, text)
        self.assertEqual(ctl.calls.count(("connect", JBL)), 1)
        # the second try comes after Jarvis's adapter was switched off and on (what cleared it on 2026-11-01)
        self.assertLess(ctl.calls.index(("power", "off")), ctl.calls.index(("connect", JBL)))
        self.assertIn(("heal", True, frozenset({JBL})), watch.calls)

    def test_paused_here_soloist_is_activated_but_not_played(self):
        """2026-11-01 13:50: restarted without activate, the next play was silent; with activate it sounded."""
        ctl = FakeCtl(connected=[JBL])
        watch = FakeWatch(ctl, playing=False, active=True)
        (error, _), _ = self.reconnect(ctl, watch)
        self.assertFalse(error)
        self.assertIn(("heal", False, frozenset({JBL})), watch.calls)
        self.assertTrue(watch.activated)

    def test_a_radio_on_the_speaker_starts_again(self):
        ctl = FakeCtl(connected=[JBL])
        (error, _), play = self.reconnect(ctl, FakeWatch(ctl, playing=False), channel="P3")
        self.assertFalse(error)
        play.assert_called_once_with("P3")

    def test_no_paired_speaker(self):
        ctl = FakeCtl()
        ctl.devices = {PHONE: ctl.devices[PHONE]}
        (error, text), _ = self.reconnect(ctl, FakeWatch(ctl))
        self.assertTrue(error)
        self.assertIn("inga parade", text)



class WatcherTest(unittest.TestCase):
    """A speaker switched on or off by hand (speaker.Watcher): the radio follows on, stops off, after two checks."""
    def setUp(self):
        os.environ.update(JARVIS_SPEAKER_PLAYER="media_player.bluetooth_hogtalare",
                          JARVIS_RADIO_PLAYER="media_player.hallpanel")
        radio._playing.clear()
        speaker._settled.clear()
        self.ctl, self.said = FakeCtl(), []
        patcher = mock.patch.object(speaker, "_ctl", self.ctl)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.watcher = speaker.Watcher(log=self.said.append)

    def tearDown(self):
        for key in ("JARVIS_SPEAKER_PLAYER", "JARVIS_RADIO_PLAYER"):
            os.environ.pop(key, None)
        radio._playing.clear()
        speaker._settled.clear()

    def test_switched_on_by_hand_moves_the_radio_after_the_second_check(self):
        self.ctl.connected.add(JBL)                  # the JBL reconnected to Jarvis by itself
        with mock.patch.object(radio, "follow_output", return_value="P3 spelas nu på JBL Charge 4.") as follow:
            self.watcher.check()
            follow.assert_not_called()               # one look is not enough: a flapping link does nothing
            self.watcher.check()
            follow.assert_called_once()
            self.watcher.check()
            follow.assert_called_once()              # a steady state acts once
        self.assertIn("P3 spelas nu på JBL Charge 4.", self.said[-1])

    def test_a_blip_shorter_than_two_checks_does_nothing(self):
        with mock.patch.object(radio, "follow_output", side_effect=AssertionError("followed")):
            self.ctl.connected.add(JBL)
            self.watcher.check()
            self.ctl.connected.discard(JBL)
            self.watcher.check()
            self.watcher.check()
        self.assertEqual(self.said, [])

    def test_switched_off_stops_the_radio_there_and_never_moves_it_to_the_panel(self):
        self.ctl.connected.add(JBL)
        self.watcher = speaker.Watcher(log=self.said.append)
        radio._playing.update(channel="P3", player="media_player.bluetooth_hogtalare")
        self.ctl.connected.discard(JBL)
        with mock.patch.object(radio, "stop_speaker") as stop, \
                mock.patch.object(radio, "play", side_effect=AssertionError("moved to the panel")):
            self.watcher.check()
            self.watcher.check()
        stop.assert_called_once()
        self.assertIn("stoppad", self.said[-1])

    def test_switched_off_while_quiet_stops_nothing(self):
        self.ctl.connected.add(JBL)
        self.watcher = speaker.Watcher(log=self.said.append)
        self.ctl.connected.discard(JBL)
        with mock.patch.object(speaker, "mpd", return_value=[{"state": "stop"}]), \
                mock.patch.object(radio, "stop_speaker", side_effect=AssertionError("stopped")):
            self.watcher.check()
            self.watcher.check()
        self.assertEqual(self.said, ["JBL Charge 4 försvann."])

    def test_jarvis_own_connect_and_disconnect_are_left_alone(self):
        with mock.patch.object(speaker, "_wait_connected", return_value=True), \
                mock.patch.object(radio, "follow_output", return_value="") as follow:
            speaker.connect("jbl")
            self.assertEqual(follow.call_count, 1)   # connect moved the radio itself
            self.watcher.check()
            self.watcher.check()
            self.assertEqual(follow.call_count, 1)
        with mock.patch.object(radio, "on_speaker", return_value=None), mock.patch.object(radio, "stop_speaker"):
            speaker.disconnect()
        with mock.patch.object(radio, "stop_speaker", side_effect=AssertionError("stopped twice")):
            self.watcher.check()
            self.watcher.check()
        self.assertEqual(self.said, [])

    def test_switched_on_again_after_jarvis_connected_it_earlier_still_follows(self):
        with mock.patch.object(speaker, "_wait_connected", return_value=True), \
                mock.patch.object(radio, "follow_output", return_value=""):
            speaker.connect("jbl")
        self.watcher.check(); self.watcher.check()
        self.ctl.connected.discard(JBL)              # off by hand, then on by hand
        with mock.patch.object(speaker, "mpd", return_value=[{"state": "stop"}]):
            self.watcher.check(); self.watcher.check()
        self.ctl.connected.add(JBL)
        with mock.patch.object(radio, "follow_output", return_value="P3 spelas nu på JBL Charge 4.") as follow:
            self.watcher.check(); self.watcher.check()
        follow.assert_called_once()

    def test_a_bluetooth_error_is_skipped_and_the_loop_survives(self):
        with mock.patch.object(speaker, "connected", side_effect=speaker.SpeakerError("no")):
            self.watcher.check()
        stop = mock.Mock()
        stop.wait.side_effect = [False, True]
        with mock.patch.object(self.watcher, "check", side_effect=RuntimeError("boom")):
            self.watcher.run(stop)
        self.assertIn("RuntimeError", self.said[-1])


if __name__ == "__main__":
    unittest.main()
