"""Compile the generated callback against a tiny LVGL model and exercise its safety contract."""
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import panel_test_codegen as codegen


class TestModeCodegenTest(unittest.TestCase):
    def config(self):
        return {'lvgl': {'pages': [{'id': name} for name in (
            'page_glass_v2', 'page_hemmet', 'page_tavla', 'page_natt', 'page_orb', 'page_ota')]},
            'esphome': {}, 'script': [{'id': 'night_auto', 'then': [{'if': {
                'condition': {'lambda': 'return true;'}}}]}]}

    def test_apply_requires_complete_board_and_refuses_duplicate_attachment(self):
        cfg = self.config()
        codegen.apply(cfg)
        self.assertIn('panel_test_active', cfg['script'][0]['then'][0]['if']['condition']['lambda'])
        with self.assertRaises(ValueError): codegen.apply(cfg)
        with self.assertRaises(ValueError): codegen.apply({'lvgl': {'pages': []}})

    def test_compiled_callback_refuses_busy_device_and_restores_only_its_own_changes(self):
        compiler = shutil.which('clang++') or shutil.which('g++')
        self.assertIsNotNone(compiler, 'C++ compiler required')
        declarations = r'''
#include <cassert>
#include <functional>
#include <string>
#include <cstdint>
#define HALLPANEL_ORB_PREFETCH 1
namespace packed_orb {
struct FakePrefetch {unsigned value=3;unsigned method(){return value;}void set_method(unsigned n){value=n;}} prefetch;
}
struct lv_font_t{};lv_font_t font30,font22,font19;
struct Font {const lv_font_t *get_lv_font(){return &font30;}} font_object;Font *i600_30=&font_object;
constexpr int LV_PART_MAIN=0;
struct lv_obj_t { bool hidden=false; std::string text="original"; const void *src=nullptr; uint32_t duration=5200; const lv_font_t *font=&font22; };
const lv_font_t *lv_obj_get_style_text_font(lv_obj_t *p,int){return p->font;}
void lv_obj_set_style_text_font(lv_obj_t *p,const lv_font_t *font,int){p->font=font;}
struct Page { lv_obj_t *obj; };
struct Assist { bool running=false; bool is_running() {return running;} } assist;
namespace media_player { const int MEDIA_PLAYER_STATE_ANNOUNCING=4; }
struct Media {int state=1;} media;
Media *panel_media=&media;
struct Debug { std::function<bool(const std::string&)> callback;
  void set_test_callback(std::function<bool(const std::string&)> fn) {callback=fn;} } physical_ui;
lv_obj_t all[24];
Page pg0{&all[0]},pg1{&all[1]},pg2{&all[2]},pg3{&all[3]},pg4{&all[4]},pg5{&all[5]};
Page *page_glass_v2=&pg0,*page_hemmet=&pg1,*page_tavla=&pg2,*page_natt=&pg3,*page_orb=&pg4,*page_ota=&pg5;
lv_obj_t musik_page; Page pg6{&musik_page}; Page *page_musik=&pg6;
namespace panel_vinyl { struct Disc { bool spin=false; void test_spin(bool v){spin=v;} } page; }
lv_obj_t *orb_listen=&all[6],*orb_think=&all[7],*orb_morph=&all[8],*orb_speak=&all[9],*orb_settle=&all[10],
         *cards=&all[11],*reply_lbl=&all[12],*net_pill=&all[13],*you_lbl=&all[14],*net_lbl=&all[15],*settings=&all[16],
         *you_row=&all[20],*jv_row=&all[21],*you_txt=&all[22],*jv_txt=&all[23];
bool timer_ringing=false,h_smoke_alarm=false,h_water_alarm=false,perf_running=false,night=false,
     assets_loaded=true,panel_test_active=false;
lv_obj_t *active=&all[0];
const int LV_OBJ_FLAG_HIDDEN=1;
lv_obj_t *lv_screen_active(){return active;}
void lv_screen_load(lv_obj_t*p){active=p;}
lv_obj_t *lv_obj_get_child(lv_obj_t *p,int){return p==pg2.obj?&all[17]:p==pg4.obj?&all[18]:&all[19];}
bool lv_obj_has_flag(lv_obj_t*p,int){return p->hidden;}
void lv_obj_add_flag(lv_obj_t*p,int){p->hidden=true;}
void lv_obj_remove_flag(lv_obj_t*p,int){p->hidden=false;}
const char *lv_label_get_text(lv_obj_t*p){return p->text.c_str();}
void lv_label_set_text(lv_obj_t*p,const char*t){p->text=t;}
unsigned animation_starts=0;
void lv_animimg_start(lv_obj_t*){++animation_starts;}
void lv_anim_delete(lv_obj_t*,void*){}
const void **lv_animimg_get_src(lv_obj_t*){static const void* sources[]={"frame0"};return sources;}
unsigned lv_animimg_get_src_count(lv_obj_t*){return 96;}
uint32_t lv_animimg_get_duration(lv_obj_t*p){return p->duration;}
void lv_animimg_set_duration(lv_obj_t*p,uint32_t value){p->duration=value;}
void lv_image_set_src(lv_obj_t*p,const void*s){p->src=s;}
#define id(x) x
void install() {
'''
        main = r'''
}
int main() {
  settings->hidden=true; net_pill->hidden=true; orb_think->hidden=true;
  install();
  for (bool *flag : {&timer_ringing,&h_smoke_alarm,&h_water_alarm,&perf_running,&night}) {
    *flag=true; assert(!physical_ui.callback("listening")); *flag=false;
    assert(active==pg0.obj && !panel_test_active);
  }
  assist.running=true; assert(!physical_ui.callback("speaking")); assist.running=false;
  media.state=4; assert(!physical_ui.callback("listening")); media.state=1;
  assets_loaded=false; assert(!physical_ui.callback("night")); assets_loaded=true;
  settings->hidden=false; assert(!physical_ui.callback("ota")); settings->hidden=true;
  assert(!physical_ui.callback("unknown"));
  assert(physical_ui.callback("listening"));
  assert(reply_lbl->font==&font30); // A prior long reply's smaller font cannot leak into a golden fixture.
  assert(active==pg4.obj && panel_test_active && you_lbl->text=="Jag lyssnar…");
  assert(!orb_listen->hidden && orb_think->hidden && all[18].hidden);
  assert(physical_ui.callback("thinking"));
  assert(!orb_think->hidden && orb_listen->hidden && you_lbl->text=="Test av Hallpanel");
  assert(physical_ui.callback("restore"));
  assert(active==pg0.obj && !panel_test_active && you_lbl->text=="original");
  assert(reply_lbl->font==&font22); // Restore the prior real reply's selected font, not a global default.
  assert(!orb_listen->hidden && orb_think->hidden && !all[18].hidden);
  unsigned previous_starts=animation_starts;
  assert(physical_ui.callback("animation"));
  assert(animation_starts==previous_starts+1 && !orb_listen->hidden && active==pg4.obj);
  static const char running[]="running"; orb_listen->src=running;
  assert(physical_ui.callback("animation"));  // same mode again: deadline only, frames untouched
  assert(animation_starts==previous_starts+1 && orb_listen->src==running);
  assert(physical_ui.callback("restore"));
  assert(physical_ui.callback("animation_live"));
  // the responsiveness workload stays 25 fps (40.625 ms per image) although listening now loops in 5.2 s
  assert(orb_listen->duration==3900 && !all[18].hidden && active==pg4.obj);
  assert(physical_ui.callback("restore"));
  assert(physical_ui.callback("animation_60"));
  assert(orb_listen->duration==1600 && !all[18].hidden && active==pg4.obj);
  assert(physical_ui.callback("restore"));
  assert(orb_listen->duration==5200 && active==pg0.obj);
  assert(physical_ui.callback("animation_60_linear"));
  assert(packed_orb::prefetch.method()==1 && orb_listen->duration==1600 && !all[18].hidden);
  assert(physical_ui.callback("animation_60_staged"));
  assert(packed_orb::prefetch.method()==2 && orb_listen->duration==1600);
  assert(physical_ui.callback("restore"));
  assert(packed_orb::prefetch.method()==3 && orb_listen->duration==5200 && active==pg0.obj);
  // speaking_live: the voice page with the speaking state running, so the director draws the live wave.
  previous_starts=animation_starts;
  assert(physical_ui.callback("speaking_live"));
  assert(active==pg4.obj && !orb_speak->hidden && orb_listen->hidden && animation_starts==previous_starts+1);
  assert(reply_lbl->text=="Vågen följer en påhittad röst, utan ljud eller modell.");
  assert(physical_ui.callback("restore"));
  assert(active==pg0.obj && !panel_test_active && reply_lbl->text=="original");
  assert(physical_ui.callback("ha_disconnected"));
  assert(!net_pill->hidden && net_lbl->text=="Ingen kontakt med Jarvis");
  net_lbl->text="real sensor update";
  assert(physical_ui.callback("restore"));
  assert(net_pill->hidden && net_lbl->text=="real sensor update");
  assert(physical_ui.callback("speaking"));
  active=pg1.obj; // a real user takes over
  assert(!physical_ui.callback("thinking"));
  assert(active==pg1.obj && !panel_test_active);
  active=pg0.obj;
  you_row->hidden=true; jv_row->hidden=true;
  assert(physical_ui.callback("chat"));
  assert(active==pg0.obj && !you_row->hidden && !jv_row->hidden);
  assert(you_txt->text=="Can we switch to English?");
  assert(jv_txt->text=="Of course. We can continue in English.");
  you_txt->text="a real new chat";
  assert(physical_ui.callback("restore"));
  assert(you_txt->text=="a real new chat" && !you_row->hidden);
  assert(jv_txt->text=="original" && jv_row->hidden);
  assert(physical_ui.callback("chat"));
  assist.running=true; active=pg4.obj;
  assert(physical_ui.callback("restore"));
  assert(active==pg4.obj && you_txt->text=="a real new chat" && jv_row->hidden);
  assist.running=false; active=pg0.obj;
  assert(physical_ui.callback("listening"));
  assist.running=true; you_lbl->text="actual voice";reply_lbl->font=&font19;
  assert(physical_ui.callback("restore"));
  assert(active==pg4.obj && you_lbl->text=="actual voice" && !panel_test_active);
  assert(reply_lbl->font==&font19); // Real voice preemption owns its new font.
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            source, binary = Path(tmp) / 'test.cpp', Path(tmp) / 'test'
            source.write_text(declarations + codegen.TEST_CALLBACK + main)
            subprocess.run([compiler, '-std=c++17', '-fsanitize=address,undefined',
                            str(source), '-o', str(binary)], check=True, capture_output=True)
            subprocess.run([str(binary)], check=True, capture_output=True, timeout=10)


if __name__ == '__main__':
    unittest.main()
