"""Free checks for the debug server's diagnostics: the log ring that survives a restart, replay scripts,
the redraw grid, clip plans and settings values (compiled under ASan/UBSan), and the host-side readers."""
import json
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))

NATIVE = r'''
#include <cassert>
#include <cstdio>
#include <cstdlib>
#include <string>
#include "log_ring.h"
#include "touch_log.h"
#include "redraw_grid.h"
#include "clip_format.h"
#include "settings_parse.h"

static std::string ordered(const panel_debug_logs::Ring<256> &ring) {
  char out[256];
  return std::string(out, ring.copy_out(out));
}

int main() {
  using namespace panel_debug_logs;
  // A fresh ring keeps everything; after a wrap the partial oldest line is dropped and order is kept.
  static Ring<256> ring;
  ring.reset(7, 3);
  assert(ring.valid() && ring.boot_id == 7 && ring.reset_reason == 3);
  ring.append("one\n", 4);
  assert(ordered(ring) == "one\n");
  for (int i = 0; i < 100; ++i) {
    char line[32];
    const int n = std::snprintf(line, sizeof line, "line %03d\n", i);
    ring.append(line, n);
  }
  const std::string text = ordered(ring);
  assert(text.size() <= 256 && text.size() > 200);
  assert(text.compare(0, 5, "line ") == 0);                  // starts on a whole line
  assert(text.substr(text.size() - 9) == "line 099\n");      // ends with the newest
  // An oversized append keeps its newest bytes only.
  std::string big(1000, 'x');
  big += "\nlast\n";
  ring.append(big.data(), big.size());
  assert(ordered(ring).substr(ordered(ring).size() - 5) == "last\n");
  // Power-on garbage is not a ring.
  Ring<256> garbage;
  std::memset(&garbage, 0x5a, sizeof garbage);
  assert(!garbage.valid());
  // The split copy used under the spinlock gives the same order.
  char raw[256], out[256];
  const uint32_t written = ring.copy_raw(raw);
  assert(std::string(out, Ring<256>::order(raw, written, out)) == ordered(ring));

  // Cleaning: colour codes go, inner newlines become " | ", invalid UTF-8 becomes '?', valid UTF-8 stays.
  char clean[256];
  const std::string colored = "\x1b[0;32m[I][tag:12]: hej\x1b[0m";
  assert(std::string(clean, clean_line(colored, clean)) == "[I][tag:12]: hej");
  assert(std::string(clean, clean_line("a\nb\r\n", clean)) == "a | b");
  assert(std::string(clean, clean_line("v\xc3\xa4g \xff\xfe \xe2\x82", clean)) == "v\xc3\xa4g ?? ??");
  for (int n = 0; n < 20000; ++n) {      // fuzz: never more than 3x, never a byte below 0x20
    char input[64], output[3 * 64 + 1];
    for (auto &c : input) c = char(std::rand() & 255);
    const size_t length = clean_line(std::string_view(input, std::rand() % 64), output);
    assert(length <= 3 * 64);
    for (size_t i = 0; i < length; ++i) assert(uint8_t(output[i]) >= 0x20 && output[i] != 0x7f);
  }

  using namespace panel_debug_touch;
  // The touch ring records presses and the release, never idle reads.
  static panel_debug_touch::Ring<4> touches;
  assert(!touches.observe(1, 5, 5, false));
  assert(touches.observe(2, 10, 20, true) && touches.observe(3, 11, 21, true) && touches.observe(4, 12, 22, false));
  assert(!touches.observe(5, 12, 22, false));
  assert(touches.size() == 3 && touches.at(0).t_ms == 2 && touches.at(2).pressed == 0);
  for (int i = 0; i < 10; ++i) touches.observe(10 + i, i, i, true);
  assert(touches.size() == 4 && touches.at(3).t_ms == 19);
  // Replay scripts: must press first, release last, stay on screen, keep time order.
  Sample s[8];
  assert(parse_replay("0,10,20,1;40,30,20,1;80,30,20,0", s, 8) == 3);
  assert(s[1].x == 30 && s[2].t_ms == 80 && s[2].pressed == 0);
  assert(!parse_replay("0,10,20,0;40,30,20,0", s, 8));     // never pressed
  assert(!parse_replay("0,10,20,1;40,30,20,1", s, 8));     // left pressed
  assert(!parse_replay("0,1024,20,1;40,30,20,0", s, 8));   // off screen
  assert(!parse_replay("50,10,20,1;40,30,20,0", s, 8));    // time goes back
  assert(!parse_replay("0,10,20,1;;40,30,20,0", s, 8));
  assert(!parse_replay("0,10,20,1;40,30,20,0;", s, 8));
  assert(!parse_replay("0,10,20,1;40,30,20,0,9", s, 8));
  assert(!parse_replay("0,10,20,1;30001,30,20,0", s, 8));  // longer than 30 s
  assert(sample_at(s, 3, 0, 39) == 0 && sample_at(s, 3, 0, 40) == 1 && sample_at(s, 3, 0, 999) == 2);
  for (int n = 0; n < 20000; ++n) {
    char input[48];
    for (auto &c : input) c = "0123456789,;"[std::rand() % 12];
    parse_replay(std::string_view(input, std::rand() % 48), s, 8);
  }

  using namespace panel_debug_redraw;
  // Each cell counts once per frame, whatever overlaps it; areas are clipped to the screen.
  static Grid grid;
  grid.clear();
  Area two[] = {{0, 0, 31, 15}, {16, 0, 47, 15}};
  grid.add_frame(two, 2);
  assert(grid.cells[0] == 1 && grid.cells[1] == 1 && grid.cells[2] == 1 && grid.cells[3] == 0);
  Area wide[] = {{-100, -5, 5000, 700}};
  grid.add_frame(wide, 1);
  assert(grid.frames == 2 && grid.cells[0] == 2 && grid.cells[panel_debug_redraw::ROWS * panel_debug_redraw::COLS - 1] == 1);
  assert(grid.frame_px[1] == uint32_t(panel_debug_redraw::WIDTH) * panel_debug_redraw::HEIGHT);
  Area none[] = {{10, 10, 5, 5}};
  grid.add_frame(none, 1);
  assert(grid.frames == 3 && grid.frame_px[2] == 0);

  using namespace panel_debug_clip;
  Plan p;
  assert(plan(2000, 0, 0, 1024, 600, 2, 75, p) && p.out_w == 512 && p.out_h == 300 && p.enc_h == 304);
  assert(p.enc_w % 16 == 0 && p.enc_h % 16 == 0);
  assert(!plan(5000, 0, 0, 1024, 600, 2, 75, p));       // too long
  assert(!plan(2000, 1, 0, 1024, 600, 1, 75, p));       // off screen
  assert(!plan(2000, 0, 0, 101, 600, 2, 75, p));        // odd width at scale 2
  assert(!plan(2000, 0, 0, 1024, 600, 3, 75, p));
  assert(!plan(2000, 0, 0, 32, 600, 1, 75, p));
  uint8_t header[FRAME_HEADER];
  frame_header(header, 0x01020304, 5, 6, 7, 8);
  assert(header[0] == 4 && header[3] == 1 && header[4] == 5 && header[16] == 8);

  using namespace panel_debug_settings;
  assert(parse("day_volume", "0").key == Key::DAY_VOLUME);
  assert(parse("brightness", "0").key == Key::NONE);       // a black screen is never a setting
  assert(parse("brightness", "100").a == 100 && parse("night_light", "101").key == Key::NONE);
  const auto t = parse("night_start", "22:30");
  assert(t.key == Key::NIGHT_START && t.a == 22 && t.b == 30);
  assert(parse("night_end", "24:00").key == Key::NONE && parse("night_end", "7:00").key == Key::NONE);
  assert(parse("volume", "50").key == Key::NONE && parse("day_volume", "-1").key == Key::NONE);
  std::puts("ok");
}
'''


class NativeDiagnosticsTest(unittest.TestCase):
    def test_ring_replay_grid_clip_and_settings(self):
        compiler = shutil.which('clang++') or shutil.which('g++')
        self.assertIsNotNone(compiler, 'A C++ compiler is required for native safety checks')
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'diag.cpp', Path(tmp) / 'diag'
            source.write_text(NATIVE.replace('#include <cassert>', '#include <cassert>\n#include <cstring>'))
            subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined', '-UNDEBUG',
                            '-I', str(ROOT / 'components/panel_debug'), str(source), '-o', str(binary)],
                           check=True, capture_output=True)
            result = subprocess.run([str(binary)], capture_output=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr.decode(errors='replace'))


class InternalRamTest(unittest.TestCase):
    def test_no_large_static_buffers_in_internal_ram(self):
        # 2026-10-29: 4.6 kB of new static char buffers came out of the boot's tightest internal RAM and a boot
        # aborted in operator new. Large buffers go to PSRAM (heap_caps_malloc, psram_string.h).
        import re
        for source in (ROOT / 'components/panel_debug').glob('*.cpp'):
            for match in re.finditer(r'^\s*(?:static\s+)?(?:DRAM_ATTR\s+)?char\s+\w+\[([^\]]+)\]', source.read_text(), re.M):
                size = match.group(1)
                if size.isdigit():
                    self.assertLess(int(size), 1024, f'{source.name}: {match.group(0).strip()}')
                else:
                    self.assertNotRegex(size, r'TX_BUFFER|1024', f'{source.name}: {match.group(0).strip()}')


class BootMemoryTest(unittest.TestCase):
    def test_tcp_receive_window_stays_small(self):
        # 2026-10-29: with IDF's 64 kB window each boot image download held 60-90 kB of internal DMA RAM in the
        # hosted SDIO buffers; boots fell to 3.8-8.8 kB internal / 1.2 kB DMA and some aborted. 16 kB: 115-119 kB.
        import re
        match = re.search(r'"CONFIG_LWIP_TCP_WND_DEFAULT"\] = "(\d+)"', (ROOT / 'board_config/hardware.py').read_text())
        self.assertIsNotNone(match)
        self.assertLessEqual(int(match.group(1)), 16384)


class HealthTest(unittest.TestCase):
    def test_limits_flag_crashes_low_ram_stalls_and_gaps(self):
        import panel_diag
        heap = lambda low: {'minimum_free': low * 1024}
        status = {'reset_reason': 4, 'uptime_ms': 600000, 'wifi_rssi': -75,
                  'responsiveness': {'render_work': {'us': [20000, 21000, 22000]}}}
        tasks = {'tasks': [{'name': 'loopTask', 'stack_min_free_bytes': 100}],
                 'heap': {'internal': heap(20), 'dma': heap(10), 'psram': heap(9000)},
                 'boot_heap': {'internal_min': 8 * 1024, 'internal_min_at_ms': 9400}}
        logs = {'lines': ['70000 - [W][component:419]: lvgl took a long time for an operation (180 ms), max is 70 ms',
                          '30000 - [W][component:419]: wifi took a long time for an operation (900 ms), max is 50 ms',
                          '80000 - [W][speaker_gaps:1]: audio_gap 120 ms']}
        crash = {'esphome_record': ['*** CRASH DETECTED ON PREVIOUS BOOT ***'], 'abort': {'text': 'assert failed'}}
        result = panel_diag.health_report(status, tasks, logs, crash)
        failed = {c['name'] for c in result['checks'] if not c['ok']}
        self.assertFalse(result['ok'])
        self.assertEqual(failed, {'restart reason', 'crash record', 'smallest stack margin', 'internal RAM lowest since boot',
                                  'dma RAM lowest since boot', 'boot internal RAM low point',
                                  'render work per frame (median)', 'main-loop stalls after boot', 'Wi-Fi signal',
                                  'audio gaps'})
        stall = next(c for c in result['checks'] if c['name'] == 'main-loop stalls after boot')
        self.assertEqual(stall['value'], '1, longest 180 ms')   # the boot's first minute is not counted
        # 2026-10-30: 19.4 ms at 50 s after a power cut was the boot's image builds; 5.9 ms at 3 min.
        status['uptime_ms'] = 50000
        early = panel_diag.health_report(status, tasks, logs, crash)
        render = next(c for c in early['checks'] if c['name'] == 'render work per frame (median)')
        self.assertTrue(render['ok'])
        self.assertIn('not judged', panel_diag.health_text(early))


class HostReadersTest(unittest.TestCase):
    def test_night_times_reach_the_panel_unencoded(self):
        # 2026-10-29: urlencode sent "06%3A00" and the panel, which does not percent-decode, refused it.
        import panel_remote
        self.assertEqual(panel_remote.form_body({'key': 'night_end', 'value': '06:00'}), b'key=night_end&value=06:00')

    def test_clip_container_round_trip(self):
        import panel_diag
        header = {'format': 'hpclip1', 'width': 64, 'height': 64, 'encoded_width': 64, 'encoded_height': 64,
                  'frames': 2}
        body = json.dumps(header).encode() + b'\n'
        for sequence, jpeg in ((10, b'\xff\xd8one\xff\xd9'), (12, b'\xff\xd8two\xff\xd9')):
            body += struct.pack('<5I', len(jpeg), sequence, 1000 * sequence, 1000 * sequence + 50, 900) + jpeg
        parsed, frames = panel_diag.parse_clip(body)
        self.assertEqual(parsed['frames'], 2)
        self.assertEqual([f['sequence'] for f in frames], [10, 12])
        self.assertEqual(frames[1]['jpeg'], b'\xff\xd8two\xff\xd9')
        with self.assertRaises(ValueError):
            panel_diag.parse_clip(body[:-3])                 # truncated frame
        with self.assertRaises(ValueError):
            panel_diag.parse_clip(b'{"format":"other"}\n')

    def test_clip_timing_names_missed_and_slow_frames(self):
        import panel_diag
        frames = [{'sequence': s, 'presented_us': t} for s, t in ((1, 0), (2, 16700), (4, 50100), (5, 100100))]
        timing = panel_diag.clip_timing(frames)
        self.assertEqual(timing['sequence_gaps'], 1)
        self.assertEqual(timing['intervals_ms'], [16.7, 33.4, 50.0])
        self.assertEqual(timing['max_interval_ms'], 50.0)

    def test_replay_script_from_recorded_touches(self):
        import panel_diag
        samples = [[1000, 10, 20, 1], [1016, 15, 20, 1], [1100, 15, 20, 0], [5000, 1, 1, 1], [5040, 1, 1, 0]]
        self.assertEqual(panel_diag.replay_script(samples, since_ms=900, until_ms=2000), '0,10,20,1;16,15,20,1;100,15,20,0')
        # A window that starts mid-press begins at the next press.
        self.assertEqual(panel_diag.replay_script(samples, since_ms=1010, until_ms=6000), '0,1,1,1;40,1,1,0')
        with self.assertRaises(ValueError):
            panel_diag.replay_script(samples, since_ms=6000)

    def test_touch_summary_pairs_gestures_with_warnings_and_latency(self):
        import panel_diag
        touches = {'samples': [[1000, 400, 300, 1], [1100, 150, 300, 0], [2000, 500, 560, 1], [2040, 500, 560, 0]]}
        logs = {'lines': ['900 - [I][x:1]: before', '1150 - [I][latency:47]: Touch sample -> next refresh ready 137.8 ms',
                          '1200 - [W][wifi:1]: weak', '2100 - [I][audio:1]: fine']}
        summary = panel_diag.touch_summary(touches, logs, since_ms=950)
        self.assertEqual([g['kind'] for g in summary['gestures']], ['swipe left', 'tap'])
        self.assertEqual(summary['touch_to_frame_ms'], [137.8])
        self.assertEqual([w['uptime_ms'] for w in summary['warnings_errors']], [1200])

    def test_repeated_touch_reads_must_not_restart_a_swipe(self):
        # One of Joel's left swipes, 2026-10-29: the GT911 repeats its last point on every other LVGL read.
        import panel_diag
        steps = [0, 0, 0, 1, 0, 9, 0, 15, 0, 19, 0, 19, 0, 20, 0, 21, 0, 22, 0, 20]
        x, reads = 576, []
        for i, step in enumerate(steps):
            x -= step
            reads.append([i * 7, x, 300, 1])
        self.assertIsNone(panel_diag.gesture_fires_ms(reads, 3))      # LVGL's default: never a gesture
        self.assertEqual(panel_diag.gesture_fires_ms(reads, 0), 77)   # the panel's setting: one gesture
        self.assertIn('lv_indev_set_gesture_min_velocity(indev, 0)', (ROOT / 'board_config/hardware.py').read_text())

    def test_crash_report_decodes_only_this_builds_addresses(self):
        import panel_diag
        record = ['1 - [E][esp32.crash:1]: *** CRASH DETECTED ON PREVIOUS BOOT ***',
                  '2 - [E][esp32.crash:1]:   PC:  0x4FF2A070  (fault location)',
                  '3 - [E][esp32.crash:1]:   BT0: 0x4FF00010  (backtrace)',
                  '4 - [E][esp32.crash:1]:   bt1: 0x4FF00020']
        self.assertEqual(panel_diag.crash_addresses(record), ['0x4FF2A070', '0x4FF00010'])

    def test_reset_reason_names(self):
        import panel_diag
        self.assertEqual(panel_diag.reset_reason(3), 'software restart')
        self.assertEqual(panel_diag.reset_reason(4), 'panic (crash)')
        self.assertEqual(panel_diag.reset_reason(99), 'unknown (99)')


if __name__ == '__main__':
    unittest.main()
