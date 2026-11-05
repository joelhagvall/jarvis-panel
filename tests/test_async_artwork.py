"""Artwork bounds/ownership and no-op UI text updates. Free native checks, no network."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class AsyncArtworkTest(unittest.TestCase):
    def test_fixed_format_origin_and_immutable_published_buffer(self):
        source=r'''
#include "components/async_artwork/contract.h"
#include <cassert>
using namespace artwork_contract;
int main(){
uint8_t h[14]={'q','o','i','f',0,0,1,32,0,0,1,32,3,0};assert(header(h,14));assert(!header(h,13));
h[6]=2;assert(!header(h,14));h[6]=1;h[12]=5;assert(!header(h,14));h[12]=4;assert(header(h,14));
const std::string prefix="http://192.0.2.1:8123/api/jarvis_agent/media-art/",key(32,'a');
assert(key_url(prefix+key+".qoi",prefix));
assert(!key_url("http://elsewhere/"+key+".qoi",prefix));assert(!key_url(prefix+key+".qoi?x",prefix));
assert(!key_url(prefix+std::string(31,'a')+".qoi",prefix));assert(!key_url(prefix+std::string(32,'x')+".qoi",prefix));
struct Decoder {int total=0;bool is_decode_finished(){return total==10;}int feed_data(uint8_t*,size_t n){total+=n;return int(n);}} decoder;
uint8_t packet[18]={1,2,3,4,5,6,7,8,9,10,0,0,0,0,0,0,0,1};size_t used=18;
assert(feed(decoder,packet,used));assert(used==8&&packet[7]==1&&decoder.total==10);
Handoff handoff;assert(handoff.target()==0);handoff.publish(0);assert(handoff.target()==1);
// Failing/cancelling a decode does not publish: the decoder keeps using the other slot.
assert(handoff.shown==0&&handoff.target()==1);handoff.publish(1);assert(handoff.target()==0);
// A cover downloaded ahead into the free slot is used only for the same URL in that same slot.
Ahead ahead;const std::string next=prefix+std::string(32,'b')+".qoi",other=prefix+std::string(32,'c')+".qoi";
assert(!ahead.hit(next,0));ahead.keep(next,0);assert(ahead.hit(next,0)&&!ahead.hit(other,0)&&!ahead.hit(next,1));
assert(!ahead.hit("",0));ahead.forget();assert(!ahead.hit(next,0)&&ahead.slot==-1);
}
'''
        self.compile(source)
    def test_unchanged_text_does_not_invalidate_the_metadata_column(self):
        source=r'''
#include "styles/glass-v2/music_ui.h"
#include <cassert>
int main(){lv_obj_t label;panel_music_ui::text(&label,"Song");assert(label.writes==1);
for(int i=0;i<100;++i)panel_music_ui::text(&label,"Song");assert(label.writes==1);
panel_music_ui::text(&label,"Other song");assert(label.writes==2&&label.text=="Other song");
panel_music_ui::x(&label,0);panel_music_ui::width(&label,0);panel_music_ui::opacity(&label,255);assert(label.writes==2);
panel_music_ui::x(&label,58);panel_music_ui::width(&label,290);panel_music_ui::opacity(&label,70);assert(label.writes==5);
for(int i=0;i<100;++i){panel_music_ui::x(&label,58);panel_music_ui::width(&label,290);panel_music_ui::opacity(&label,70);}assert(label.writes==5);
// Two renders before LVGL's layout (cover off, then on): the second move must still happen (row text under cover).
lv_obj_t row;panel_music_ui::x(&row,58);layout(&row);panel_music_ui::x(&row,14);panel_music_ui::x(&row,58);layout(&row);
assert(row.x==58);panel_music_ui::width(&row,290);layout(&row);panel_music_ui::width(&row,304);panel_music_ui::width(&row,290);
layout(&row);assert(row.w==290);
// One visible label change per frame: the second waits for the next REFR_READY, a newer value replaces it,
// hidden labels never wait, and a waiting change that no longer changes anything is skipped.
panel_music_ui::Spread s;lv_obj_t title,artist,album,hidden;title.text="A";artist.text="B";album.text="C";
assert(s.admit(true)&&!s.admit(true)&&s.admit(false));
s.queue.push_back({&artist,"B2"});s.queue.push_back({&album,"C"});assert(s.replace(&artist,"B3"));assert(!s.replace(&title,"x"));
s.frame_done(panel_music_ui::write);assert(artist.text=="B3"&&s.queue.size()==1&&!s.admit(true));
s.frame_done(panel_music_ui::write);assert(album.writes==0&&s.queue.empty()&&s.admit(true));   // "C" was already shown
// Metadata admitted for a not-yet-drawn frame yields to urgent feedback, irrespective of arrival order.
s.remember(&title,"Long title");panel_music_ui::write(&title,"Long title");
s.yield_to(&artist);assert(title.text=="A"&&s.queue.size()==1);
s.frame_done(panel_music_ui::write);assert(title.text=="Long title"&&s.queue.empty());
// Never replace a newer queued title with the earlier admitted value.
s.remember(&title,"Intermediate");panel_music_ui::write(&title,"Intermediate");
s.queue.push_back({&title,"Latest"});s.yield_to(&artist);
s.frame_done(panel_music_ui::write);assert(title.text=="Latest");
// Urgent feedback removes a queued stale caption while retaining unrelated metadata's frame budget.
auto &live=panel_music_ui::spread();
live.queue.push_back({&artist,"stale caption"}); live.queue.push_back({&album,"Album 2"});
panel_music_ui::urgent_text(&artist,"Pausar …");
assert(artist.text=="Pausar …"&&live.queue.size()==1);
live.frame_done(panel_music_ui::write);assert(artist.text=="Pausar …"&&album.text=="Album 2");
(void)hidden;}

'''
        self.compile(source, '#pragma once\n#include <string>\nstruct lv_obj_t {std::string text;int writes=0,x=0,w=0,opa=255,style_x=0,style_w=0;};\ninline void layout(lv_obj_t*p){p->x=p->style_x;p->w=p->style_w;}\ninline const char*lv_label_get_text(lv_obj_t*p){return p->text.c_str();}\ninline void lv_label_set_text(lv_obj_t*p,const char*s){p->text=s;++p->writes;}\nconstexpr int LV_PART_MAIN=0;\ninline int lv_obj_get_x(lv_obj_t*p){return p->x;} inline int lv_obj_get_style_x(lv_obj_t*p,int){return p->style_x;}\ninline void lv_obj_set_x(lv_obj_t*p,int v){p->style_x=v;++p->writes;}\ninline int lv_obj_get_width(lv_obj_t*p){return p->w;} inline int lv_obj_get_style_width(lv_obj_t*p,int){return p->style_w;}\ninline void lv_obj_set_width(lv_obj_t*p,int v){p->style_w=v;++p->writes;}\ninline int lv_obj_get_style_opa(lv_obj_t*p,int){return p->opa;} inline void lv_obj_set_style_opa(lv_obj_t*p,int v,int){p->opa=v;++p->writes;}\n')
    def compile(self,source,lvgl=None):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'test.cpp').write_text(source)
            if lvgl:(p/'lvgl.h').write_text(lvgl)
            subprocess.run(['c++','-std=c++17','-O1','-fsanitize=address,undefined','-I',str(p),'-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
