"""Exercise the generated boot observer with the actual native LVGL renderer."""
import json
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class BootRenderFenceTest(unittest.TestCase):
    def test_sound_gate_waits_for_the_uncovered_refresh_without_nested_rendering(self):
        generated = subprocess.run([sys.executable, str(ROOT/'board.py'), '--voice', 'jarvis_orb.py',
                                   '--remote', 'http://example.invalid/assets'], cwd=ROOT/'styles/glass-v2',
                                  capture_output=True, text=True, check=True)
        cfg = json.loads(generated.stdout)
        light = next(b for b in cfg['esphome']['on_boot'] if b['priority'] == 390)['then'][0]['lambda']
        observer = light[light.index('lv_display_add_event_cb'):light.index('id(light_ready)')]
        boot = next(s for s in cfg['script'] if s['id'] == 'boot_done')
        self.assertNotIn('lv_refr_now', json.dumps(boot))
        self.assertIn('boot_shown_ms', json.dumps(boot))
        gate = boot['then'][0]['if']['then'][0]['wait_until']['condition']['lambda']
        fields = re.findall(r'id\((\w+)\)\.has_state\(\)', gate)
        self.assertEqual(set(fields), {'hm_w_temp','hm_w_line','hm_w_tip','hm_w_icon','hm_w_days','hm_sl_walk',
            'hm_sl0','hm_sl1','hm_sl2','hm_sl3','hm_cal_head','hm_cal0','hm_cal1','hm_cal2','chat_said','chat_reply','chat_card','chat_working'})
        declarations = ','.join(fields)
        pointers = ','.join('&'+name for name in fields)
        build = ROOT/'styles/glass-v2/.esphome/build/style-glass-v2'
        libraries = list(build.glob('.pioenvs/*/lib*/liblvgl.a'))
        headers = list(build.glob('.piolibdeps/*/lvgl/lvgl.h'))
        self.assertEqual((len(libraries), len(headers)), (1, 1), 'Native LVGL simulator dependency required')
        source = r'''
#include "lvgl.h"
#include <cassert>
#include <cstdint>
#include <cstdlib>
#include <cstring>
extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t n) { return std::malloc(n); }
void lv_free_core(void *p) { std::free(p); }
void *lv_realloc_core(void *p,size_t n) { return std::realloc(p,n); }
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *p) { std::memset(p,0,sizeof(*p)); }
}
static lv_obj_t *boot_cover=nullptr;
static bool urgent=false;
static bool boot_shown=false;
static uint32_t boot_shown_ms=0;
static uint32_t fake_ms=1234,boot_data_ms=0;
static bool bg_valid=false,h_smoke_alarm=false,h_water_alarm=false;
static bool boot_cache_available=false,boot_cache_used=false,boot_cache_active=true;
static uint32_t millis() { return fake_ms; }
#include "bench/boot_reveal.h"
struct Sensor { bool ready=false; bool has_state() const { return ready; } } DECLARATIONS;
struct Clock { bool ready=false; Clock now() const { return *this; } bool is_valid() const { return ready; } } ha_time;
#define id(name) name
static bool joined() { GATE }
int main() {
  Sensor *fields[]={POINTERS};
  const unsigned count=sizeof(fields)/sizeof(*fields);
  for(unsigned rotation=0;rotation<count;++rotation) {
    boot_data_ms=0;bg_valid=true;ha_time.ready=true;
    for(auto *s:fields)s->ready=false;
    for(unsigned i=0;i<count;++i) {
      assert(!joined()); // Every first value, even an empty one, is required.
      fields[(i+rotation)%count]->ready=true;
    }
    assert(joined());assert(boot_data_ms==1234);
  }
  boot_data_ms=0;bg_valid=false;assert(!joined());
  bg_valid=true;ha_time.ready=false;assert(!joined());
  h_smoke_alarm=true;assert(joined());assert(boot_data_ms==0);h_smoke_alarm=false;
  h_water_alarm=true;assert(joined());assert(boot_data_ms==0);h_water_alarm=false;
  boot_cache_available=true;assert(joined());assert(boot_cache_used);assert(boot_data_ms==0);
  bg_valid=false;assert(!joined());bg_valid=true;boot_cache_available=false;
  fake_ms=30000;assert(joined());assert(boot_data_ms==0); // Absolute offline escape, not false readiness.
  fake_ms=1234;ha_time.ready=true;
  lv_init();
  auto *display=lv_display_create(64,64);
  assert(display);
  alignas(64) static uint8_t pixels[64*64*4];
  lv_display_set_buffers(display,pixels,nullptr,sizeof(pixels),LV_DISPLAY_RENDER_MODE_FULL);
  lv_display_set_flush_cb(display,[](lv_display_t *d,const lv_area_t *,uint8_t *) { lv_display_flush_ready(d); });
  lv_tick_set_cb(millis);
  lv_display_set_color_format(display,LV_COLOR_FORMAT_RGB565);
  lv_obj_set_style_bg_color(lv_screen_active(),lv_color_hex(0x306090),LV_PART_MAIN);
  lv_obj_set_style_bg_opa(lv_screen_active(),LV_OPA_COVER,LV_PART_MAIN);
  lv_refr_now(nullptr);
  uint8_t dashboard[64*64*2];std::memcpy(dashboard,pixels,sizeof(dashboard));
  boot_cover=lv_obj_create(lv_layer_top());
  auto *cover=boot_cover;
  lv_obj_set_pos(cover,0,0);
  lv_obj_set_style_radius(cover,0,LV_PART_MAIN);
  lv_obj_set_style_border_width(cover,0,LV_PART_MAIN);
  lv_obj_set_style_pad_all(cover,0,LV_PART_MAIN);
  lv_obj_set_style_bg_color(cover,lv_color_hex(0x000000),LV_PART_MAIN);
  lv_obj_set_style_bg_opa(cover,LV_OPA_COVER,LV_PART_MAIN);
  static uint16_t logo_pixels[16*16];for(auto &p:logo_pixels)p=0xffff;
  static lv_image_dsc_t logo{};
  logo.header.magic=LV_IMAGE_HEADER_MAGIC;logo.header.cf=LV_COLOR_FORMAT_RGB565;
  logo.header.w=16;logo.header.h=16;logo.header.stride=32;
  logo.data_size=sizeof(logo_pixels);logo.data=reinterpret_cast<const uint8_t *>(logo_pixels);
  auto *image=lv_image_create(cover);lv_image_set_src(image,&logo);lv_obj_set_pos(image,24,24);
  lv_obj_set_size(cover,64,64);
  OBSERVER
  lv_refr_now(nullptr);
  assert(boot_shown_ms==0); // A rendered splash cannot release the chime.
  boot_shown=true;
  lv_obj_invalidate(lv_screen_active());lv_refr_now(nullptr);
  assert(boot_shown_ms==0); // Opaque splash is not yet the dashboard.
  // Every pixel belongs to the opaque splash before the switch, then to the
  // dashboard. No intermediate dashboard-plus-logo frame is ever produced.
  uint32_t ended=0;
  boot_reveal::show(cover,&ended,millis);
  assert(ended==fake_ms);assert(lv_obj_has_flag(cover,LV_OBJ_FLAG_HIDDEN));
  lv_refr_now(nullptr);assert(boot_shown_ms==fake_ms);
  assert(std::memcmp(dashboard,pixels,sizeof(dashboard))==0);
  boot_shown_ms=4321;lv_obj_invalidate(lv_screen_active());lv_refr_now(nullptr);
  assert(boot_shown_ms==4321);
  // Alarm bypass uses the same immediate reveal without an asynchronous tick.
  urgent=true;lv_obj_remove_flag(cover,LV_OBJ_FLAG_HIDDEN);
  boot_reveal::show(cover,&ended,millis);assert(lv_obj_has_flag(cover,LV_OBJ_FLAG_HIDDEN));
  lv_deinit();
}
'''.replace('OBSERVER', observer).replace('GATE', gate).replace('DECLARATIONS', declarations).replace('POINTERS', pointers)
        with tempfile.TemporaryDirectory(prefix='hallpanel-boot-fence-') as folder:
            folder = Path(folder)
            cpp, binary = folder/'test.cpp', folder/'test'
            cpp.write_text(source)
            (folder/'lv_conf.h').write_text((build/'src/lv_conf.h').read_text())
            compiler = shutil.which('clang++') or shutil.which('g++')
            self.assertIsNotNone(compiler, 'Native C++ compiler required')
            result = subprocess.run([compiler, '-std=c++17', '-O1', '-g', '-fsanitize=address,undefined',
                '-DLV_CONF_INCLUDE_SIMPLE', '-I'+str(ROOT), '-I'+str(folder), '-I'+str(headers[0].parent),
                str(cpp), str(libraries[0]), '-o', str(binary)], capture_output=True, text=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            result = subprocess.run([str(binary)], capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
