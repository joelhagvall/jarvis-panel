#!/usr/bin/env python3
"""Jarvis's volume tool (server/agents/volume.py) without Home Assistant or Bluetooth: it picks the speaker
while one is connected, the panel otherwise, and clamps steps.

  python3 -m unittest tests/test_volume.py
"""
import json
import os
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "server" / "agents"))
import speaker  # noqa: E402
import volume  # noqa: E402

JBL = {"mac": "AA", "name": "JBL Charge 4", "connected": True}


class VolumeTest(unittest.TestCase):
    def setUp(self):
        os.environ.update(JARVIS_RADIO_PLAYER="media_player.hallpanel", JARVIS_SPEAKER_PLAYER="media_player.bt")
        self.calls = []

    def tearDown(self):
        for key in ("JARVIS_RADIO_PLAYER", "JARVIS_SPEAKER_PLAYER"):
            os.environ.pop(key, None)

    def ha(self, path, data=None, timeout=10):
        self.calls.append((path, data))
        level = data.get("level") if data else None
        return json.dumps({"service_response": {"volume": level if level is not None else 75 + (data or {}).get("step", 0)}}).encode()

    def test_the_connected_speaker_by_default_through_the_ducking_service(self):
        with mock.patch.object(speaker, "connected", return_value=JBL), mock.patch.object(volume.hass, "request", self.ha):
            self.assertEqual(volume.control("set", 40), (False, "JBL Charge 4: 40 %."))
            self.assertEqual(volume.control("down"), (False, "JBL Charge 4: 65 %."))
        self.assertEqual(self.calls[0], ("/api/services/jarvis_agent/speaker_volume?return_response", {"level": 40}))
        self.assertEqual(self.calls[1][1], {"step": -10})

    def test_the_panel_when_no_speaker_or_when_asked(self):
        state = {"attributes": {"volume_level": 0.65}}
        for connected, where in ((None, "auto"), (JBL, "panel")):
            with mock.patch.object(speaker, "connected", return_value=connected), \
                    mock.patch.object(volume.hass, "get_json", return_value=state), \
                    mock.patch.object(volume.hass, "call_service") as call:
                self.assertEqual(volume.control("up", step=50, where=where), (False, "Panelen: 100 %."))
                call.assert_called_once_with("media_player", "volume_set",
                                             {"entity_id": "media_player.hallpanel", "volume_level": 1.0}, timeout=8)

    def test_asking_for_the_speaker_when_none_is_connected_and_set_without_a_level(self):
        with mock.patch.object(speaker, "connected", return_value=None):
            self.assertEqual(volume.control("status", where="hogtalare"), (True, "Ingen Bluetooth-högtalare är kopplad."))
        self.assertTrue(volume.control("set")[0])


if __name__ == "__main__":
    unittest.main()
