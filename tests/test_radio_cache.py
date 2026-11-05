"""RSSI cache freshness, wraparound and failed/disconnected samples remain explicit."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class RadioCacheTest(unittest.TestCase):
    def test_freshness_failed_queries_disconnect_and_clock_wrap(self):
        code = r'''
#include "radio_cache.h"
#include <cassert>
int main(){
 esphome::panel_debug::RadioCache c;
 assert(c.due(10) && !c.valid);
 c.record(10,true,-42);assert(!c.due(1009) && c.due(1010));
 assert(c.valid && c.value==-42 && c.age(1009)==999);
 c.record(1010,false,0);assert(!c.valid && !c.due(1011) && c.due(2010));
 c.clear();assert(c.due(1011) && !c.valid);
 c.record(0xffffff00U,true,-50);assert(!c.due(0x100U));assert(c.age(0x100U)==512);
 assert(c.due(0x300U));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (folder / 'test.cpp').write_text(code)
            binary = folder / 'test'
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                            '-I'+str(ROOT / 'components/panel_debug'), str(folder / 'test.cpp'), '-o', str(binary)],
                           check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
