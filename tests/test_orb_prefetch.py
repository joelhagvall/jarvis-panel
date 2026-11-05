"""Bounded prefetch ownership: a worker cannot overwrite pixels leased by LVGL."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PrefetchOwnershipTest(unittest.TestCase):
    def test_parallel_publication_reuse_and_pins_preserve_exact_pixels(self):
        source = r'''
#include "prefetch_slots.h"
#include <array>
#include <cassert>
#include <thread>
using Pool = packed_orb::PrefetchSlots<3>;
int main() {
  // A skipped index can make FIFO age disagree with animation order. Preparing
  // frame 11 must not evict the already READY nearer frame 10.
  Pool skew;
  for (unsigned value : {10U,8U,9U}) { auto *write=skew.reserve(value);assert(write);skew.publish(*write); }
  auto *current=skew.take(9);assert(current);
  auto *future=skew.reserve(11,10);assert(future && future->index.load()==11);skew.publish(*future);
  auto *near=skew.take(10);assert(near);skew.release(*near);
  assert(!skew.reserve(12,10,11));
  skew.release(*current);
  assert(skew.reserve(12,10,11));
  Pool pool;
  std::array<std::array<uint8_t, 256>, 3> pixels{};
  for (size_t i=0;i<3;++i) pool.slots[i].data=pixels[i].data();
  auto *first=pool.reserve(7); assert(first);
  for (unsigned i=0;i<256;++i) first->data[i]=uint8_t(i+7);
  pool.publish(*first);
  auto *pin=pool.take(7); assert(pin==first);
  assert(!pool.take(7));
  assert(!pool.reserve(7));
  for (unsigned value=8;value<1000;++value) {
    auto *write=pool.reserve(value); assert(write && write!=pin);
    for (unsigned i=0;i<256;++i) write->data[i]=uint8_t(i+value);
    pool.publish(*write);
    for (unsigned i=0;i<256;++i) assert(pin->data[i]==uint8_t(i+7));
  }
  pool.release(*pin);
  auto *again=pool.take(7); assert(again==pin); pool.release(*again);
  std::atomic<bool> done{false};
  std::thread writer([&]() {
    for (unsigned value=0;value<100000;++value) {
      auto *write=pool.reserve(value%143);
      if (!write) continue;
      for (unsigned i=0;i<256;++i) write->data[i]=uint8_t(i+value%143);
      if (value%19==0) pool.abort(*write); else pool.publish(*write);
    }
    done.store(true);
  });
  unsigned hits=0;
  while(!done.load()) {
    for (unsigned wanted=0;wanted<143;++wanted) {
      if (auto *read=pool.take(wanted)) {
        for (unsigned i=0;i<256;++i) assert(read->data[i]==uint8_t(i+wanted));
        ++hits;
        pool.release(*read);
      }
    }
  }
  writer.join();
  assert(hits>0);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)
            (path / 'test.cpp').write_text(source)
            binary = path / 'test'
            subprocess.run(['c++', '-std=c++17', '-O1', '-pthread', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(ROOT / 'styles/glass-v2'),
                            str(path / 'test.cpp'), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=20)


if __name__ == '__main__':
    unittest.main()
