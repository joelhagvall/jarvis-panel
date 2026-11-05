"""Control confirmation is separate from request acceptance; stale callbacks never own a new command."""
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class MusicFeedbackTest(unittest.TestCase):
    def test_urgent_status_defers_already_admitted_metadata_in_the_actual_esp32_path(self):
        header = r'''
#pragma once
#include <string>
#include <cstdint>
constexpr int LV_PART_MAIN=0,LV_EVENT_REFR_READY=1;
struct lv_obj_t {std::string text="old";bool visible=true;};struct lv_event_t{};
inline void (*refresh)(lv_event_t*)=nullptr;
inline void *lv_display_get_default(){return nullptr;}
inline void lv_display_add_event_cb(void*,void(*fn)(lv_event_t*),int,void*){refresh=fn;}
inline bool lv_obj_is_visible(lv_obj_t *p){return p->visible;}
inline const char *lv_label_get_text(lv_obj_t *p){return p->text.c_str();}
inline void lv_label_set_text(lv_obj_t *p,const char *s){p->text=s;}
inline int lv_obj_get_style_x(lv_obj_t*,int){return 0;}inline void lv_obj_set_x(lv_obj_t*,int){}
inline int lv_obj_get_style_width(lv_obj_t*,int){return 0;}inline void lv_obj_set_width(lv_obj_t*,int){}
inline int lv_obj_get_style_opa(lv_obj_t*,int){return 0;}inline void lv_obj_set_style_opa(lv_obj_t*,int,int){}
'''
        source = r'''
#define USE_ESP32 1
#include "styles/glass-v2/music_ui.h"
#include <cassert>
int main(){
 lv_obj_t title,artist,status;auto &s=panel_music_ui::spread();
 panel_music_ui::text(&title,"Long new title");assert(title.text=="Long new title");
 panel_music_ui::text(&artist,"New artist");assert(artist.text=="old");
 panel_music_ui::urgent_text(&status,"Pausar …");
 assert(status.text=="Pausar …"&&title.text=="old"&&s.queue.size()==2);
 refresh(nullptr);assert(artist.text=="New artist"&&title.text=="old");
 refresh(nullptr);assert(title.text=="Long new title"&&s.queue.empty());
 // Feedback first also reserves the same frame; metadata cannot join it afterwards.
 refresh(nullptr);panel_music_ui::urgent_text(&status,"Pausat");
 panel_music_ui::text(&title,"Another title");assert(title.text=="Long new title");
 refresh(nullptr);assert(title.text=="Another title");
}
'''
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'lvgl.h').write_text(header)
            (p/'test.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-Wall','-Wextra','-Werror','-fsanitize=address,undefined',
                            '-I',str(p),'-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)

    def test_load_fixture_changes_covers_and_never_restores_over_live_metadata(self):
        lvgl = r'''
#pragma once
#include <string>
#include <cstdint>
constexpr int LV_OBJ_FLAG_HIDDEN=1,LV_PART_MAIN=0,LV_IMAGE_HEADER_MAGIC=25,LV_COLOR_FORMAT_RGB565=3;
struct lv_obj_t {std::string text="saved";bool hidden=false;};
struct lv_image_dsc_t {struct {int magic{},cf{},w{},h{},stride{};}header;unsigned data_size{};const uint8_t *data{};};
inline const char *lv_label_get_text(lv_obj_t *p){return p->text.c_str();}
inline void lv_label_set_text(lv_obj_t *p,const char *s){p->text=s;}
inline bool lv_obj_has_flag(lv_obj_t *p,int){return p->hidden;}
inline void lv_obj_add_flag(lv_obj_t *p,int){p->hidden=true;}
inline void lv_obj_remove_flag(lv_obj_t *p,int){p->hidden=false;}
inline int lv_obj_get_style_x(lv_obj_t*,int){return 0;}inline void lv_obj_set_x(lv_obj_t*,int){}
inline int lv_obj_get_style_width(lv_obj_t*,int){return 0;}inline void lv_obj_set_width(lv_obj_t*,int){}
inline int lv_obj_get_style_opa(lv_obj_t*,int){return 0;}inline void lv_obj_set_style_opa(lv_obj_t*,int,int){}
'''
        source = r'''
#include "styles/glass-v2/music_load.h"
#include <cassert>
unsigned copies=0;
void cover(const lv_image_dsc_t *im,uint64_t){assert(im->data_size==512&&im->header.w==16);++copies;}
int main(){
 lv_obj_t a,b,c,d,group,empty;group.hidden=true;
 panel_music_load::Fixture f;f.begin({&a,&b,&c,&d},&group,&empty,0,cover);
 assert(!group.hidden&&empty.hidden);
 for(unsigned i=1;i<=30;++i)f.tick(i*600);
 assert(f.active&&f.step==30&&f.covers==6&&copies==6);
 assert(a.text=="Ingen bekräftelse från spelaren. Försök igen.");
 f.restore();assert(!f.active&&a.text=="saved"&&b.text=="saved"&&group.hidden&&!empty.hidden);
 // An unchanged ordinary UI resync is not a new real track and cannot stop background work.
 f.begin({&a,&b,&c,&d},&group,&empty,0,cover);a.text="Unchanged resync";
 f.tick(600);assert(f.active&&f.unchanged()&&a.text=="Pausar …");f.restore();
 f.begin({&a,&b,&c,&d},&group,&empty,0,cover);f.tick(600);
 b.text="Real song";group.hidden=false;panel_music::state.title="Real song";
 f.tick(1200);assert(!f.active&&b.text=="Real song"&&!group.hidden);
 // Queued diagnostics must disappear on restore, even before their labels have drawn.
 f.begin({&a,&b,&c,&d},&group,&empty,0,cover);
 panel_music_ui::spread().queue.push_back({&b,f.injected[1]});f.restore();
 assert(panel_music_ui::spread().queue.empty());
 // Restoration also owns a queued change whose label still shows its saved text.
 group.hidden=true;empty.hidden=false;b.hidden=true;
 f.begin({&a,&b,&c,&d},&group,&empty,0,cover);
 f.injected[1]="Queued metadata";panel_music_ui::spread().queue.push_back({&b,f.injected[1]});
 f.restore();assert(group.hidden&&!empty.hidden&&b.hidden&&b.text=="Real song");
}
'''
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'lvgl.h').write_text(lvgl)
            (p/'test.cpp').write_text(source)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(p), '-I', str(ROOT),
                            str(p/'test.cpp'), '-o', str(p/'test')], check=True)
            subprocess.run([str(p/'test')], check=True)

    def test_confirmation_order_timeout_preemption_and_late_responses(self):
        source = r'''
#include "styles/glass-v2/now_playing.h"
#include <cassert>
#include <cstring>
using namespace panel_music;
int main() {
 State playing; assert(playing.wire("playing|Spotify|A|Artist|1|1|JBL Charge 4"));
 State paused=playing;paused.state="paused";
 Pending p;
 assert(p.begin(Pending::Action::TOGGLE,playing,5000,100));auto first=p.ticket;
 assert(!p.begin(Pending::Action::NEXT,playing,5000,101));assert(p.ticket==first);
 assert(!p.acknowledge(first,200)&&p.waiting); // service accepted, player still playing
 assert(!p.observe(playing,5100,210)&&p.waiting);
 assert(p.observe(paused,5100,300)&&!p.waiting);
 assert(std::strcmp(p.message(),"Pausat")==0);
 assert(!p.tick(1499)&&p.tick(1500)&&!*p.message());
 assert(p.begin(Pending::Action::TOGGLE,paused,5000,2000));first=p.ticket;
 assert(!p.observe(playing,5000,2100)&&p.waiting); // state may beat service response
 assert(p.acknowledge(first,2200)&&!p.waiting);
 assert(std::strcmp(p.message(),"Spelar")==0);
 assert(p.begin(Pending::Action::NEXT,playing,5000,3000));first=p.ticket;
 assert(!p.acknowledge(first,3100));assert(!p.tick(10999)&&p.tick(11000));
 assert(!p.waiting&&p.result==Pending::Result::TIMEOUT);
 assert(p.begin(Pending::Action::NEXT,playing,5000,11001));auto second=p.ticket;
 assert(second!=first&&!p.acknowledge(first,11002)&&!p.fail(first,11003)&&p.waiting);
 assert(!p.acknowledge(second,11004));State next=playing;next.title="B";
 assert(p.observe(next,0,11005)&&!p.waiting);
 // Stop preempts an in-flight skip; the skip's late failure must not roll back the stop.
 assert(p.begin(Pending::Action::NEXT,playing,5000,12000));first=p.ticket;
 assert(p.begin(Pending::Action::STOP,playing,5000,12001));second=p.ticket;
 assert(!p.fail(first,12002)&&p.waiting);
 assert(!p.acknowledge(second,12003));State idle;
 assert(p.observe(idle,0,12004)&&!p.waiting);
 // Previous can restart the same title; it is confirmed by a fresh progress update.
 assert(p.begin(Pending::Action::PREVIOUS,playing,10000,13000));
 assert(!p.acknowledge(p.ticket,13001));assert(!p.observe(playing,10500,13002));
 assert(p.observe(playing,100,13003));
 assert(p.begin(Pending::Action::NEXT,playing,0,14000));
 assert(p.fail(p.ticket,14001)&&p.result==Pending::Result::FAILED);
 assert(!p.tick(18000)&&p.tick(18001));
 assert(p.begin(Pending::Action::TOGGLE,playing,0,0xfffffff0u));
 assert(!p.tick(100)&&p.tick(uint32_t(0xfffffff0u+8000u)));
}
'''
        with tempfile.TemporaryDirectory() as folder:
            p = Path(folder)
            (p/'test.cpp').write_text(source)
            subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror',
                            '-fsanitize=address,undefined', '-I', str(ROOT), str(p/'test.cpp'),
                            '-o', str(p/'test')], check=True)
            subprocess.run([str(p/'test')], check=True)
