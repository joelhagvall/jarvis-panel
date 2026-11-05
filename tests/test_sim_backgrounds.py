#!/usr/bin/env python3
"""The simulator's Inställningar Bakgrund list (Joel, 2026-11-02: "simulatorn låter mig inte klicka bland
bildkällorna"): with a choices.json beside the pictures (tools/sim_backgrounds.py), jarvis_orb.py fills the list
and each entry points bg, art and voice_bg at its own folder; without one the entries ask Home Assistant as on
the board. The simulator's config must also build: only the board's async_artwork has picture_identity().
Run: python3 -m unittest tests/test_sim_backgrounds.py"""
import json
import subprocess
import sys
import tempfile
import threading
import unittest
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GLASS = ROOT / "styles" / "glass-v2"


class Quiet(SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass


def sim_config(remote):
    return json.loads(subprocess.check_output([sys.executable, "jarvis_orb.py", "--remote", remote], cwd=GLASS))


class SimBackgroundsTest(unittest.TestCase):
    def serve(self, folder):
        server = ThreadingHTTPServer(("127.0.0.1", 0), partial(Quiet, directory=str(folder)))
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return f"http://127.0.0.1:{server.server_port}"

    def test_choices_fill_the_list_and_each_entry_loads_its_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            choices = [{"name": "Som på Jarvis", "dir": "", "title": "", "credit": ""},
                       {"name": "Bing", "dir": "choice-bing", "title": "Isbjörnar", "credit": "© X · Bing"}]
            (Path(tmp) / "choices.json").write_text(json.dumps(choices, ensure_ascii=False))
            base = self.serve(tmp)
            cfg = sim_config(base)
        render = json.dumps(cfg, ensure_ascii=False)
        self.assertNotIn("picture_identity", render)          # the simulator has no async_artwork
        scripts = {s["id"]: s for s in cfg["script"]}
        first = scripts["bg_pick_0"]["then"][0]["lambda"]
        self.assertIn(f'id(bg).set_url("{base}/bg.qoi")', first)
        pick = scripts["bg_pick_1"]["then"]
        self.assertIn(f'id(voice_bg).set_url("{base}/choice-bing/voice_bg.qoi")', pick[0]["lambda"])
        self.assertIn('lv_label_set_text(id(t_title), "Isbjörnar")', pick[0]["lambda"])
        self.assertEqual(pick[1], {"component.update": "bg"})
        self.assertIn("homeassistant.action", json.dumps(scripts["bg_pick_2"]))   # no third choice: unchanged
        boot = json.dumps(cfg["esphome"]["on_boot"], ensure_ascii=False)
        self.assertIn('choice_list::bg.options(\\"Som på Jarvis\\\\nBing\\")', boot)

    def test_without_choices_the_list_asks_home_assistant(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = sim_config(self.serve(tmp))
        scripts = {s["id"]: s for s in cfg["script"]}
        self.assertIn("homeassistant.action", json.dumps(scripts["bg_pick_0"]))
        self.assertNotIn("choice_list::bg.options(", json.dumps(cfg["esphome"]["on_boot"]))


if __name__ == "__main__":
    unittest.main()
