"""Exercise the real restart wrapper with a busy/stuck shared cache engine."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'components/panel_restart/restart_guard.cpp'


class RestartGuardTest(unittest.TestCase):
    def test_busy_cache_finishes_before_reset_on_either_core_and_stuck_cache_fails_closed(self):
        compiler = shutil.which('clang++') or shutil.which('g++')
        self.assertIsNotNone(compiler, 'C++ compiler required')
        declarations = r'''
#include <cassert>
#include <cstdint>
#include <vector>
#define USE_ESP32_VARIANT_ESP32P4
#define ESP_IDF_VERSION_VAL(a,b,c) ((a)*10000+(b)*100+(c))
#define ESP_IDF_VERSION ESP_IDF_VERSION_VAL(5,5,5)
#define IRAM_ATTR
#define CACHE_SYNC_CTRL_REG 0x3ff10098
#define CACHE_SYNC_DONE 16
enum { WDT_RWDT, WDT_STAGE0, WDT_STAGE1, WDT_STAGE_ACTION_RESET_SYSTEM, WDT_STAGE_ACTION_RESET_RTC };
struct wdt_hal_context_t {};
std::vector<int> events;
int core, stalled, busy, reads, restarts, watchdog;
bool watchdog_enabled;
void rv_utils_intr_global_disable() { events.push_back(1); }
uint32_t rtc_clk_slow_freq_get_hz() { return 32768; }
void wdt_hal_init(wdt_hal_context_t*,int,int,bool) { watchdog_enabled=false; events.push_back(2); }
void wdt_hal_write_protect_disable(wdt_hal_context_t*) {}
void wdt_hal_config_stage(wdt_hal_context_t*,int stage,uint32_t ticks,int action) {
  assert(ticks==32768);
  assert((stage==WDT_STAGE0 && action==WDT_STAGE_ACTION_RESET_SYSTEM) ||
         (stage==WDT_STAGE1 && action==WDT_STAGE_ACTION_RESET_RTC));
  watchdog++;
}
void wdt_hal_set_flashboot_en(wdt_hal_context_t*,bool enabled) { assert(enabled); }
void wdt_hal_enable(wdt_hal_context_t*) { assert(watchdog==2); watchdog_enabled=true; }
void wdt_hal_write_protect_enable(wdt_hal_context_t*) { assert(watchdog==2 && watchdog_enabled); events.push_back(3); }
int esp_cpu_get_core_id() { return core; }
void esp_cpu_stall(int other) { assert(other==1-core && watchdog==2 && watchdog_enabled); stalled=other; events.push_back(4); }
uint32_t read_sync() {
  assert(watchdog==2 && stalled==1-core);
  ++reads;
  if (busy<0) return 0;
  if (busy>0) { --busy; return 0; }
  return CACHE_SYNC_DONE;
}
#define REG_GET_BIT(address,bit) read_sync()
struct Restarted {};
struct WatchdogReset {};
extern "C" void __real_esp_restart_noos(void) {
  assert(busy==0 && watchdog==2 && stalled==1-core);
  ++restarts; events.push_back(5); throw Restarted{};
}
void esp_cpu_wait_for_intr() {
  assert(busy==-1 && watchdog==2 && restarts==0);
  events.push_back(6); throw WatchdogReset{};
}
'''
        main = r'''
int main() {
  for (core=0; core<=1; ++core) {
    for (int pending : {0, 7, -1}) {
      stalled=-1; busy=pending; reads=restarts=watchdog=0; events.clear();
      try { __wrap_esp_restart_noos(); assert(false); }
      catch (Restarted&) { assert(pending>=0 && restarts==1 && reads==pending+1); }
      catch (WatchdogReset&) { assert(pending==-1 && restarts==0 && reads==10000000); }
      assert(events==std::vector<int>({1,2,3,4,pending<0?6:5}));
    }
  }
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            for name in ('esphome/core/defines.h', 'sdkconfig.h', 'esp_attr.h', 'esp_cpu.h',
                         'esp_idf_version.h', 'esp_private/rtc_clk.h', 'hal/wdt_hal.h',
                         'riscv/rv_utils.h', 'soc/cache_reg.h', 'soc/soc.h', 'soc/rtc.h'):
                header = folder / name
                header.parent.mkdir(exist_ok=True, parents=True)
                header.write_text('')
            source, binary = folder / 'test.cpp', folder / 'test'
            source.write_text(declarations + SOURCE.read_text() + main)
            result = subprocess.run([compiler, '-std=c++17', '-O2', '-fsanitize=address,undefined',
                                     '-I', tmp, str(source), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)
            # A framework upgrade must explicitly review/remove the workaround.
            source.write_text(source.read_text().replace(
                '#define ESP_IDF_VERSION ESP_IDF_VERSION_VAL(5,5,5)',
                '#define ESP_IDF_VERSION ESP_IDF_VERSION_VAL(5,5,6)'))
            result = subprocess.run([compiler, '-std=c++17', '-I', tmp, '-fsyntax-only', str(source)],
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('Review panel_restart', result.stderr)


if __name__ == '__main__':
    unittest.main()
