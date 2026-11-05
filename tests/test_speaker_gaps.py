"""Native test of the speaker gap meter the board installs on speaker_out (bench/speaker_gaps.h)."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++ compiler unavailable")
class SpeakerGapTest(unittest.TestCase):
    def test_holes_in_real_audio_are_gaps_and_new_sounds_are_not(self):
        with tempfile.TemporaryDirectory(prefix="hallpanel-gaps-") as tmp:
            source, binary = Path(tmp) / "gaps.cpp", Path(tmp) / "gaps"
            source.write_text(r'''
#include <cassert>
#include "speaker_gaps.h"
int main() {
  SpeakerGapMeter m(48000);
  uint32_t gaps, last, into, worst, total;
  const uint32_t buf = 480;                                   // 10 ms DMA buffers at 48 kHz
  int64_t t = 1000000;
  for (int i = 0; i < 100; i++) m.played(buf, t += 10000);     // 1 s of unbroken speech
  assert(!m.report(gaps, last, into, worst, total));
  m.played(240, t += 10000);                                   // half a buffer: 5 ms of padding follows
  t -= 5000;
  m.played(buf, t += 10000 + 5000);                            // padding of 5 ms is jitter-sized: ignored
  assert(!m.report(gaps, last, into, worst, total));
  t += 600000;                                                 // 600 ms starved mid-reply
  m.played(buf, t += 10000);
  assert(m.report(gaps, last, into, worst, total));
  assert(gaps == 1 && last == 600 && worst == 600 && total == 600);
  assert(into >= 1000 && into <= 1020);
  assert(!m.report(gaps, last, into, worst, total));           // reported once
  t += 3000000;                                                // 3 s of quiet, then the next sound
  m.played(buf, t += 10000);
  assert(!m.report(gaps, last, into, worst, total));
  t += 20000;
  m.played(buf, t += 10000);                                   // 20 ms hole in the new sound
  assert(m.report(gaps, last, into, worst, total));
  assert(gaps == 2 && last == 20 && into == 10 && worst == 600 && total == 620);

  // A voice reply: listening chime, recognised text, player announcing, then the reply's first audio.
  FirstReplyAudio r;
  uint32_t after_announce, after_text;
  t += 3000000;
  m.played(buf, t += 10000);                                   // the listening chime
  r.text_us = t + 3000000;                                     // text 3 s later
  r.announce_us = r.text_us + 600000;                          // announcing 600 ms after the text
  assert(!r.take(m.sound_start_us(), r.announce_us + 100000, after_announce, after_text));   // only the chime yet
  t = r.announce_us + 450000;                                  // the reply plays out 450 ms after announcing
  m.played(buf, t);
  assert(r.take(m.sound_start_us(), t + 1000, after_announce, after_text));
  assert(after_announce == 440 && after_text == 1040);         // its start: the buffer's end minus 10 ms
  assert(!r.take(m.sound_start_us(), t + 2000, after_announce, after_text));                 // logged once
  // The listening chime cuts a reply still playing: the 200 ms switch is a new sound, not a gap.
  t += 3000000;
  for (int i = 0; i < 50; i++) m.played(buf, t += 10000);      // a reply playing
  m.new_sound(t);                                              // "Hey Jarvis": the chime replaces it
  m.played(buf, t += 10000);                                   // the reply's last buffered audio
  t += 200000;
  m.played(buf, t += 10000);                                   // the chime, 200 ms later
  assert(!m.report(gaps, last, into, worst, total));
  for (int i = 0; i < 120; i++) m.played(buf, t += 10000);     // the chime goes on past the grace,
  t += 50000;
  m.played(buf, t += 10000);                                   // a real 50 ms hole counts again
  assert(m.report(gaps, last, into, worst, total) && gaps == 3 && last == 50);
  r.text_us = t + 5000000; r.announce_us = r.text_us + 500000; // no reply sound: given up after 15 s
  assert(!r.take(m.sound_start_us(), r.announce_us + 16000000, after_announce, after_text));
  assert(r.announce_us == 0 && r.text_us == 0);
}
''')
            subprocess.run([shutil.which("clang++") or shutil.which("g++"), "-std=c++17", "-O2", "-Wall", "-Werror",
                            "-I", str(ROOT / "bench"), str(source), "-o", str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True)

    def test_board_installs_the_meter_on_the_shared_speaker(self):
        result = subprocess.run([sys.executable, str(ROOT / "board.py"), "--voice", "jarvis_orb.py", "--remote",
                                 "http://example.invalid/assets"], cwd=ROOT / "styles/glass-v2",
                                capture_output=True, check=True, text=True)
        config = json.loads(result.stdout)
        self.assertIn(str(ROOT / "bench/speaker_gaps.h"), config["esphome"]["includes"])
        self.assertIn("id(speaker_out)->add_audio_output_callback", result.stdout)
        self.assertIn('ESP_LOGW(\\"audio_gap\\"', result.stdout)
        self.assertIn("panel_first_reply.take(panel_speaker_gaps.sound_start_us()", result.stdout)
        self.assertIn("panel_first_reply.announce_us = esp_timer_get_time()", result.stdout)
        self.assertIn("panel_speaker_gaps.new_sound(esp_timer_get_time());", result.stdout)
        sensors = {s.get("id"): s for s in config["sensor"]}
        self.assertEqual(sensors["audio_gaps"]["state_class"], "total_increasing")
        self.assertEqual(sensors["audio_gap_ms"]["unit_of_measurement"], "ms")
        self.assertIn("id(audio_gaps).publish_state(gaps)", result.stdout)
        # The voice page starved a 100 ms speaker ring; keep the measured cushion.
        speaker = next(s for s in config["speaker"] if s["id"] == "speaker_out")
        self.assertEqual(speaker["buffer_duration"], "500ms")


if __name__ == "__main__":
    unittest.main()
