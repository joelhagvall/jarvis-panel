"""Frame clock: boundary/presentation counts, interval ring, stale detection and torn-copy safety."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class VsyncClockTest(unittest.TestCase):
    def test_counts_intervals_stale_and_concurrent_snapshot_never_report_overwritten_entries(self):
        code = r'''
#include "vsync_clock.h"
#include <atomic>
#include <cassert>
#include <thread>
int main() {
  vsync::SourceClock source;
  constexpr uint32_t active=vsync::SOURCE_ACTIVE,step=16560;
  source.frame(step,true,active|1);source.frame(2*step,false,0);
  source.frame(3*step,true,active|2);
  auto src=source.snapshot();assert(src.count==3&&src.presented==2&&src.us[0]==2*step);
  source.frame(4*step,true,active|2); // A button redraw is not a new record image.
  source.frame(5*step,true,active|3);src=source.snapshot();assert(src.us[1]==2*step);
  source.frame(6*step,true,0);source.frame(1000*step,false,0); // Intentionally paused/hidden.
  source.frame(1001*step,true,active|4);src=source.snapshot();assert(src.total==2&&src.presented==4);
  source.frame(1002*step,true,active|5);src=source.snapshot();assert(src.us[2]==step);
  assert(src.json(1002*step,"vinyl_presentation").find("\"vinyl_presentation\":")!=std::string::npos);
  vsync::Clock clock;
  assert(!clock.live(0));
  uint32_t now = 5000;
  clock.frame(now, false);          // a boundary that repeated the previous image
  assert(clock.count.load() == 1 && clock.presented.load() == 0 && clock.live(now + 50000) && !clock.live(now + 100000));
  for (unsigned i = 0; i < 5; ++i) { now += 16560; clock.frame(now, true); }
  assert(clock.count.load() == 6 && clock.presented.load() == 5 && clock.interval_total.load() == 4);
  auto s = clock.snapshot();
  assert(s.count == 6 && s.presented == 5 && s.total == 4 && s.valid == 4 && s.last_us == now);
  for (unsigned i = 0; i < 4; ++i) assert(s.us[i] == 16560);
  const auto json = s.json(now + 7);
  assert(json.find("\"vsync_total\":6") != std::string::npos && json.find("\"presented_total\":5") != std::string::npos);
  assert(json.find("\"vsync_age_us\":7") != std::string::npos);
  assert(json.find("\"presented_interval\":{\"total\":4,\"us\":[16560,16560,16560,16560]}") != std::string::npos);
  assert(!clock.pending.load());
  clock.submitted(); assert(clock.pending.load());
  now += 16560; clock.frame(now, false); assert(clock.pending.load());   // not presented yet
  now += 16560; clock.frame(now, true); assert(!clock.pending.load());   // presented at this boundary
  assert(clock.count.load() == 8 && clock.presented.load() == 6 && clock.interval_total.load() == 5);
  assert(clock.interval_us[4] == 2 * 16560);
  // Wrap: only the newest 128 entries exist. The oldest one shares its slot with the entry
  // the interrupt could be writing right now, so a snapshot reports 127 of them.
  for (unsigned i = 0; i < 300; ++i) { now += 16560 + i; clock.frame(now, true); }
  s = clock.snapshot();
  assert(s.total == 305 && s.valid == 127);
  for (uint32_t i = s.total - s.valid; i < s.total; ++i) assert(s.us[i % vsync::RING] == 16560 + (i - 5));
  // Entries carry a deterministic pattern so a concurrent reader can detect any torn/overwritten entry.
  vsync::Clock shared;
  std::atomic<bool> done{false}, reading{false};
  // The writer starts once the reader runs and yields now and then: under a loaded test run (12 processes at once,
  // 2026-10-30) it once finished all 200,000 frames before the reader's first snapshot, and checked stayed 0.
  std::thread writer([&]() {
    while (!reading.load()) std::this_thread::yield();
    uint32_t t = 0;
    for (uint32_t i = 0; i < 200000; ++i) { t += 1000 + (i % 7); shared.frame(t, true); if (i % 1024 == 0) std::this_thread::yield(); }
    done.store(true);
  });
  unsigned checked = 0;
  reading.store(true);
  while (!done.load()) {
    const auto snap = shared.snapshot();
    assert(snap.valid <= snap.total && snap.valid <= vsync::RING);
    for (uint32_t i = snap.total - snap.valid; i < snap.total; ++i) {
      // interval i was written for presentation i+1: 1000 + ((i+1) % 7)
      assert(snap.us[i % vsync::RING] == 1000 + ((i + 1) % 7));
      ++checked;
    }
  }
  writer.join();
  const auto final = shared.snapshot();
  assert(final.count == 200000 && final.presented == 200000 && final.total == 199999 && final.valid == 127);
  for (uint32_t i = final.total - final.valid; i < final.total; ++i, ++checked)
    assert(final.us[i % vsync::RING] == 1000 + ((i + 1) % 7));
  assert(checked > 0);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'test.cpp').write_text(code)
            binary = folder / 'test'
            subprocess.run(['c++', '-std=c++17', '-O1', '-pthread', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I' + str(ROOT / 'bench'),
                            str(folder / 'test.cpp'), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=60)


if __name__ == '__main__':
    unittest.main()
