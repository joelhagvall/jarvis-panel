"""Turn a simulator config into a config for the real board, optionally with measurements.

Usage:
  python3 board.py [--sim] [--bench] [GENERATOR.py] [generator args...] > out.yaml

  GENERATOR.py  a generator that prints a simulator config (default: styles/glass-v2/jarvis_orb.py).
                Run board.py from the generator's folder so relative asset paths resolve.
  --bench       log redraws per second, render time and free PSRAM/heap every 2 s
                (bench/bench.h), and replay a `demo` script every 20 s if the config has one.
  --sim         keep the simulator display (host + SDL), e.g. to try --bench on the Mac.
  --es          the board has an early ESP32-P4 (an engineering sample, chip revision below v3.0).
                ESPHome builds for rev3 by default, and that firmware does not start on older chips.
                The first USB flash prints the revision ("Chip is ESP32-P4 (revision vX.Y)").
  --voice       add the microphones, the speaker, on-device wake word ("hey Jarvis", micro_wake_word)
                and the Home Assistant voice assistant. If the config has the stateful orb page
                (styles/glass-v2/jarvis_orb.py), the assistant's events drive it: wake word -> orb page,
                listening; your sentence -> thinking; Jarvis starts speaking -> morph and speaking; done
                speaking -> settle and back to the dashboard. The reply plays through a speaker media
                player, so Home Assistant can stream it: the panel starts playing when the first
                sentence is ready (on_intent_progress), before Jarvis has finished writing. Audio pins: Waveshare 7B as in a working community
                config (I2S MCLK 13, LRCLK 10, BCLK 12, mic DIN 11, speaker DOUT 9, amplifier 53,
                ES8311 at 0x18 and ES7210 at 0x40 on the touch I2C bus).
                In Home Assistant, give the panel the Assist pipeline "Jarvis" (its Assistant select)
                and turn on "Allow the device to perform Home Assistant actions", so the wake word
                can call jarvis_agent.warm (and Rensa jarvis_agent.clear).
                That pipeline's Jarvis Cloud STT provider allows adjustable pauses (0.8 s by default) and 60 s recordings;
                ESPHome's conversation_timeout controls remembered context, not the recording length.
                It also declares Home Assistant's Assist timers (counted down on screen, a chime when
                done), shows Jarvis's announcements (assist_satellite.announce) on the voice page,
                shows why a question failed, and goes back to Natt instead of the dashboard at night.
  Without --sim, a generator gets HALLPANEL_BOARD=1 (live labels start empty), its host clock ha_time
  becomes Home Assistant's, and glass-v2's night mode gets the backlight (12 %) and, with --voice,
  the volume (at most 35 %). glass-v2's Inställningar gets the same: Skärmljus (the day's level, kept on
  the panel and followed when Home Assistant changes it), Nattljus (the screen shows it while the slider
  moves) and, with --voice, Volym (the chime plays at the new level).

Board: Waveshare ESP32-P4-WIFI6-Touch-LCD-7B. ESP32-P4 with an ESP32-C6 for Wi-Fi over SDIO,
GT911 touch on I2C, MIPI-DSI panel and an LEDC backlight. Pins follow a working community
config for this board (Home Assistant forum, "Waveshare ESP32-P4-WIFI6-Touch-LCD-7B working
config") and Waveshare's wiki (I2C on GPIO7/GPIO8).

The Wi-Fi values are placeholders so the config compiles. For the real device, put the
network in secrets.yaml and switch these to !secret in a hand-written wrapper.
"""
import json
import os
import re
import subprocess
import sys
from pathlib import Path

from board_config import bench, hardware, pages, status, voice, ui_updates
from board_config.common import ROOT, scripts

flags = {a for a in sys.argv[1:] if a in ("--sim", "--bench", "--voice", "--es", "--direct-display", "--double-buffer", "--fast-display")}
rest = [a for a in sys.argv[1:] if a not in flags]
gen = ROOT / "styles/glass-v2/jarvis_orb.py"
if rest and rest[0].endswith(".py"):
    gen = Path(rest.pop(0)).resolve()
# HALLPANEL_BOARD=1 tells a generator to start its live labels empty instead of with example values.
env = {**os.environ, **({} if "--sim" in flags else {"HALLPANEL_BOARD": "1"})}
cfg = json.loads(subprocess.run([sys.executable, str(gen), *rest], check=True,
                                capture_output=True, text=True, env=env).stdout)
build_id = os.environ.get('HALLPANEL_BUILD_ID')
if build_id:
    if not re.fullmatch(r'[0-9a-f]{32}', build_id):
        raise ValueError('HALLPANEL_BUILD_ID must be 32 lowercase hexadecimal characters')

# The order matters: later sections extend what earlier ones add.
board = "--sim" not in flags
has_orb = False
if board:
    hardware.apply(cfg, flags, build_id)
if "--voice" in flags and board:
    has_orb = voice.apply(cfg, flags)
if board:
    pages.apply(cfg)
if board and "--bench" not in flags:
    bench.diagnostics(cfg)
if board:
    bench.touch(cfg, flags, has_orb)
if "--bench" in flags:
    bench.bench(cfg, flags)
if board and "--voice" in flags:
    status.apply(cfg)
    ui_updates.apply(cfg)
if "--voice" in flags and scripts(cfg)("music_render"):
    sys.path.insert(0, str(ROOT / "styles/glass-v2"))
    import now_playing
    now_playing.board(cfg)
    import ai_page
    ai_page.board(cfg)   # after Spelas nu: the AI page after a voice turn wraps the same scripts

json.dump(cfg, sys.stdout, ensure_ascii=False, indent=1)
