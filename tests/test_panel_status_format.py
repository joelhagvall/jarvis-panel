"""Compile the actual generated worker formatter; preserve JSON with bounded local stack."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class PanelStatusFormatTest(unittest.TestCase):
    def test_worker_json_values_and_bounded_formatter_frame(self):
        generated = subprocess.run([sys.executable,str(ROOT/'board.py'),'--voice','jarvis_orb.py',
                                   '--remote','http://example.invalid/assets'],cwd=ROOT/'styles/glass-v2',
                                  capture_output=True,text=True,check=True)
        cfg=json.loads(generated.stdout)
        callback=next(a['lambda'] for b in cfg['esphome']['on_boot'] for a in b['then']
                      if 'set_status_callback' in a.get('lambda',''))
        body=callback[callback.index('  std::string result = timing();'):callback.index('  return result;')+len('  return result;')]
        source=r'''
#include <cstdint>
#include <iostream>
#include <string>
struct Sample { uint32_t redraws_per_s=60; double average_ms=8.25,maximum_ms=12.5,touch_last_ms=17.5,touch_p95_ms=19.25; } sample;
static uint32_t ready_ms=13000,boot_ui=501,boot_wifi=6500,boot_bg=8500,boot_backgrounds=12800,
                boot_orb=10000,boot_voice_client=7200,boot_light=502,boot_shown=8520,boot_sound=8525,boot_data=8510,boot_fade_start=8520,boot_fade_end=8740,loop_interval=1,cache_epoch=1792990000,cache_saves=1,cache_failures=0,boot_live=9000,boot_ntp=6600;
            const bool cache_used=true,cache_active=false;
static uint32_t frames=UINT32_MAX;
static uint64_t busy_us=UINT64_C(1234567890123);
static bool healthy=true,alarm=false,announcement=false,voice=false;
static bool mic_enabled=true,wake_running=false;
static bool music_load_active=false;
static uint32_t music_load_steps=10,music_load_covers=2;
static bool detail_active=false;static uint32_t detail_steps=12;
static struct {uint32_t pending=1,applied=2,replaced=3,deferred=4,max_wait_ms=5;} priority_stats;
static std::string timing() { return "{\"protocol\":2"; }
static std::string orb_extra() { return ",\"prefetch\":true"; }
static std::string swap_extra() { return ",\"screen_swap_applied\":3"; }
static std::string artwork_extra() { return ""; }
static std::string vinyl_extra() { return ""; }
static std::string presentation() { return ",\"vsync\":60"; }
namespace wifi_boot { static std::string json() { return ",\"boot_wifi_assoc_ms\":5900"; } }
__attribute__((noinline)) std::string format_status() { BODY }
int main() {
  std::cout<<format_status()<<"}\n";
  healthy=false;alarm=true;announcement=true;voice=true;
  std::cout<<format_status()<<"}\n";
}
'''.replace('BODY',body)
        with tempfile.TemporaryDirectory(prefix='hallpanel-status-format-') as folder:
            folder=Path(folder);cpp=folder/'test.cpp';binary=folder/'test'
            cpp.write_text(source)
            compiler=shutil.which('clang++') or shutil.which('g++')
            self.assertIsNotNone(compiler,'Native C++ compiler required')
            result=subprocess.run([compiler,'-std=c++17','-O2','-fstack-usage','-fsanitize=address,undefined',
                                   str(cpp),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            first,second=map(json.loads,result.stdout.splitlines())
            expected={'protocol':2,'prefetch':True,'screen_swap_applied':3,'vsync':60,'redraws_per_s':60,'render_avg_ms':8.25,
                'render_max_ms':12.5,'render_frames_total':2**32-1,'render_busy_total_us':1234567890123,
                'touch_refresh_last_ms':17.5,'touch_refresh_p95_ms':19.25,'boot_ready_ms':13000,
                'boot_cache_used':True,'boot_cache_active':False,'boot_cache_epoch':1792990000,'boot_cache_saves':1,'boot_cache_failures':0,'boot_live_ms':9000,'boot_ntp_ms':6600,'boot_ui_ms':501,'boot_light_ms':502,'boot_shown_ms':8520,'boot_sound_ms':8525,'boot_data_ms':8510,'boot_fade_start_ms':8520,'boot_fade_end_ms':8740,'loop_interval_ms':1,
                'boot_wifi_ms':6500,'boot_wifi_assoc_ms':5900,'boot_bg_ms':8500,'boot_backgrounds_ms':12800,'boot_orb_ms':10000,
                'boot_voice_client_ms':7200,'boot_healthy':True,'alarm_active':False,
                'announcement_active':False,'voice_active':False}
            expected.update(audio_bus_hz=48000,voice_sample_rate=16000)
            expected.update(mic_enabled=True,wake_running=False)
            expected.update(music_load_active=False,music_load_steps=10,music_load_covers=2)
            expected.update(detail_load_active=False,detail_load_steps=12)
            expected.update(ui_update_pending=1,ui_update_applied=2,ui_update_replaced=3,ui_update_deferred=4,ui_update_max_wait_ms=5)
            self.assertEqual(first,expected)
            expected.update(boot_healthy=False,alarm_active=True,announcement_active=True,voice_active=True)
            self.assertEqual(second,expected)
            entries=[line.split('\t') for path in folder.glob('*.su') for line in path.read_text().splitlines()
                     if 'format_status' in line]
            self.assertEqual(len(entries),1,'Compiler stack-usage report required')
            # A local-frame regression, not a claim about the complete ESP32 call chain.
            self.assertLessEqual(int(entries[0][-2]),1024)


    def test_outer_http_json_preserves_fields_and_nested_extra_with_small_stack(self):
        source=(ROOT/'components/panel_debug/panel_debug.cpp').read_text()
        # The reply is built in PSRAM since 2026-10-29 (a boot aborted in operator new while the gates polled).
        start=source.index('    panel_debug_memory::String result;',source.index('PanelDebug::DeferredReply PanelDebug::status_'))
        body=source[start:source.index('    return result;',start)+len('    return result;')]
        pixel_fields=('ready dma_copies dma_errors staged_copies dma_burst dma_bytes dma_us ppa_ready '
            'ppa_copies ppa_errors ppa_burst ppa_bytes ppa_us masked_images masked_pixels masked_skipped masked_opaque masked_us').split()
        declarations=';'.join('uint64_t '+name+'=7' for name in pixel_fields)+';'
        cpp_source=r'''
#include "protocol.h"
#include "psram_string.h"
#include <cstdint>
#include <functional>
#include <iostream>
#include <string>
using esphome::panel_debug::quoted;
static std::string build_id_="unversioned";
static std::string page="page_\n\"test\"",sha="abcdef012",mode="idle";
static bool physical=true;
static uint32_t values[]={1,2,3,4,5,6,7,8,9,10};
struct App { const char *version="v1",*date="Oct 25",*time="22:00",*idf_ver="5.5.5"; } descriptor;
static App *app=&descriptor;
static uint32_t heap_us=55,radio_us=44,status_send_us_=12,status_send_bytes_=1234,
                status_send_at_ms_=900,status_send_count_=11,status_send_errors_=1;
static uint32_t millis() { return 1000; }
struct Pixel { PIXEL_FIELDS } pixel_copy;
static std::string wifi_json=",\"wifi_connected\":true,\"wifi_rssi\":-49,\"wifi_rssi_age_ms\":100";
static std::function<std::string()> extra=[] { return ",\"boot_ready_ms\":8520"; };
__attribute__((noinline)) panel_debug_memory::String format_outer() { BODY }
int main() { std::cout<<format_outer().c_str()<<"\n";extra={};std::cout<<format_outer().c_str()<<"\n"; }
'''.replace('PIXEL_FIELDS',declarations).replace('BODY',body)
        expected={'protocol':2,'lease_required':True,'width':1024,'height':600,'page':'page_\n"test"',
            'uptime_ms':1,'physical_touch_active':True,'physical_touch_idle_ms':2,'boot_id':3,'reset_reason':4,
            'build_id':'unversioned','firmware_elf_sha256':'abcdef012','firmware_version':'v1','build_date':'Oct 25',
            'build_time':'22:00','idf_version':'5.5.5','psram_free':5,'psram_min_free':6,'internal_free':7,
            'internal_min_free':8,'internal_largest_block':9,'test_mode':'idle','wifi_connected':True,
            'wifi_rssi':-49,'wifi_rssi_age_ms':100,'captures':10,'status_main_heap_us':55,'status_radio_us':44,
            'status_previous_send_us':12,'status_previous_send_bytes':1234,'status_previous_send_age_ms':100,
            'status_send_count':11,'status_send_errors':1,'boot_ready_ms':8520}
        expected.update({name:7 for name in ('pixel_dma_ready pixel_dma_errors pixel_dma_copies pixel_dma_bytes '
            'pixel_dma_us pixel_staged_copies pixel_dma_burst pixel_ppa_burst pixel_ppa_ready pixel_ppa_errors '
            'pixel_ppa_copies pixel_ppa_bytes pixel_ppa_us pixel_mask_images pixel_mask_pixels pixel_mask_skipped '
            'pixel_mask_opaque pixel_mask_us').split()})
        with tempfile.TemporaryDirectory(prefix='hallpanel-outer-format-') as folder:
            folder=Path(folder);cpp=folder/'test.cpp';binary=folder/'test';cpp.write_text(cpp_source)
            compiler=shutil.which('clang++') or shutil.which('g++')
            self.assertIsNotNone(compiler,'Native C++ compiler required')
            result=subprocess.run([compiler,'-std=c++17','-O2','-fstack-usage','-fsanitize=address,undefined',
                '-I'+str(ROOT/'components/panel_debug'),str(cpp),'-o',str(binary)],capture_output=True,text=True)
            self.assertEqual(result.returncode,0,result.stderr)
            result=subprocess.run([str(binary)],capture_output=True,text=True,timeout=10)
            self.assertEqual(result.returncode,0,result.stderr)
            first,second=map(json.loads,result.stdout.splitlines())
            self.assertEqual(first,expected)
            expected.pop('boot_ready_ms');self.assertEqual(second,expected)
            entries=[line.split('\t') for path in folder.glob('*.su') for line in path.read_text().splitlines()
                     if 'format_outer' in line]
            self.assertEqual(len(entries),1)
            self.assertLessEqual(int(entries[0][-2]),1024)


if __name__=='__main__':
    unittest.main()
