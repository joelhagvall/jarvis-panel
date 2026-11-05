"""Run the real Disc's cache handoff and cancellation under ASan/UBSan, with a no-op LVGL boundary."""
from pathlib import Path
import json
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class VinylBuffersTest(unittest.TestCase):
    def test_building_or_cancelling_a_cover_never_overwrites_the_active_cover(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'esphome/core').mkdir(parents=True)
            (p/'esphome/core/log.h').write_text('#define ESP_LOGI(...) ((void)0)\n#define ESP_LOGE(...) ((void)0)\n')
            (p/'esphome/core/hal.h').write_text('#pragma once\n#include <cstdint>\nnamespace esphome { inline uint32_t millis(){return 100;} inline uint32_t micros(){return 100000;} }\n')
            (p/'lvgl.h').write_text('''#pragma once
#include <cstdint>
using lv_value_precise_t=int;
struct lv_point_precise_t {int x,y;};
struct lv_obj_t {int x=0,y=0;lv_point_precise_t points[2]{};};
struct lv_image_header_t {int magic=0,cf=0,w=0,h=0,stride=0;};
struct lv_image_dsc_t {lv_image_header_t header;const uint8_t *data=nullptr;uint32_t data_size=0;};
struct lv_timer_t {void *user_data;};
constexpr int LV_IMAGE_HEADER_MAGIC=1,LV_COLOR_FORMAT_RGB565=1,LV_COLOR_FORMAT_RGB565A8=2,LV_COLOR_FORMAT_A8=3,LV_OBJ_FLAG_HIDDEN=1,LV_PART_MAIN=0;
inline uint32_t lv_tick_get(){return 100;}
inline int lv_obj_get_x(lv_obj_t*o){return o->x;} inline int lv_obj_get_y(lv_obj_t*o){return o->y;}
inline int lv_obj_get_width(lv_obj_t*){return 10;} inline int lv_obj_get_height(lv_obj_t*){return 10;}
inline void *lv_timer_get_user_data(lv_timer_t*t){return t->user_data;}
inline void lv_timer_create(void(*)(lv_timer_t*),int,void*){}
inline void lv_image_set_src(lv_obj_t*,const void*){} inline void lv_obj_invalidate(lv_obj_t*){}
inline void lv_line_set_points(lv_obj_t*o,const lv_point_precise_t*p,int){o->points[0]=p[0];o->points[1]=p[1];}
inline void lv_obj_set_pos(lv_obj_t*o,int x,int y){o->x=x;o->y=y;} inline void lv_obj_add_flag(lv_obj_t*,int){}
inline void lv_obj_remove_flag(lv_obj_t*,int){} inline void lv_obj_set_style_clip_corner(lv_obj_t*,bool,int){}
inline void lv_obj_set_style_radius(lv_obj_t*,int,int){}
''')
            (p/'test.cpp').write_text(r'''
#include "styles/glass-v2/vinyl.h"
#include "components/async_artwork/contract.h"
#include <cassert>
#include <iostream>
#include <vector>
using namespace panel_vinyl;
int main(){
 constexpr int n=16; Disc d; d.radius=n/2;
 std::vector<uint16_t> raw(n*n,0xF800),copy(n*n),out(n*n);
 d.layout.make(n,nullptr);
 std::vector<uint16_t> a(d.layout.count*HALF),b(d.layout.count*HALF),second(n*n);
 d.inputs[0]=copy.data();d.inputs[1]=second.data();d.copy=copy.data();d.cache[0]=a.data();d.cache[1]=b.data();d.output[0]=out.data();
 lv_image_dsc_t image;image.header.cf=LV_COLOR_FORMAT_RGB565;image.header.w=n;image.header.h=n;
 image.data=reinterpret_cast<uint8_t*>(raw.data());
 assert(d.identity(&image,Kind::PICTURE)==artwork_contract::picture_identity(image.data,raw.size()*2));
 { Disc idle; assert(!idle.building()&&!idle.prepare_batch()); }   // nothing to build before the first source
 // On the panel only one atlas exists between covers; a source must still build into it.
 Disc single;std::vector<uint16_t> single_cache(d.layout.count*HALF),single_in0(n*n),single_in1(n*n);
 single.radius=n/2;single.layout.make(n,nullptr);single.inputs[0]=single_in0.data();single.inputs[1]=single_in1.data();
 single.copy=single_in0.data();single.cache[0]=single_cache.data();
 single.source(&image,Kind::PICTURE);assert(single.active==0&&single.prepared.load()==1);
 // The panel keeps each atlas image in its own block; it builds and samples the same pixels as one block.
 Disc split;split.radius=n/2;split.layout.make(n,nullptr);std::vector<uint16_t> split_in0(n*n),split_in1(n*n);
 std::vector<std::vector<uint16_t>> images(HALF,std::vector<uint16_t>(split.layout.count));
 for(int i=0;i<HALF;++i)split.chunks[0][i]=images[i].data();
 split.inputs[0]=split_in0.data();split.inputs[1]=split_in1.data();split.copy=split_in0.data();
 split.source(&image,Kind::PICTURE);assert(split.active==0&&split.has_atlas(0)&&!split.has_atlas(1));
 for(int i=0;i<HALF;++i)assert(std::equal(images[i].begin(),images[i].end(),single_cache.begin()+i*split.layout.count));
 std::vector<uint16_t> split_out(n*n),single_out(n*n);
 split.output[0]=split_out.data();single.output[0]=single_out.data();
 for(auto *disc:{&split,&single}){disc->jobs[0].angle=97.0f;disc->jobs[0].interpolate=true;disc->render(0);}
 assert(split_out==single_out);
 // A new cover turns once its even half is built and never reads the odd images still being written.
 assert(pick(10.0f,true,1).lo==3&&pick(10.0f,true,2).lo==2&&pick(10.0f,true,2).hi==4);
 assert(pick(359.0f,true,2).hi==0&&pick(5.0f,false,2).lo==2&&pick(1.0f,false,2).lo==0);
 // The coarse first stage: every sixth image, also across the mirrored half-turn (54 is a multiple of 6).
 for(float angle=0;angle<360;angle+=0.7f){const auto c=pick(angle,true,6);assert(c.lo%6==0&&c.hi%6==0&&(c.lo%HALF)%6==0&&(c.hi%HALF)%6==0);}
 { bool seen[HALF]={};for(int k=0;k<HALF;++k){const int i=Disc::build_image(k);assert(i>=0&&i<HALF&&!seen[i]);seen[i]=true;
     if(k<Disc::FIRST)assert(i%6==0);if(k<HALF/2)assert(i%2==0);} }
 Disc half;half.radius=n/2;half.layout.make(n,nullptr);
 std::vector<uint16_t> half_a(half.layout.count*HALF),half_b(half.layout.count*HALF,0xDEAD),half_in0(n*n),half_in1(n*n),half_out(n*n);
 half.inputs[0]=half_in0.data();half.inputs[1]=half_in1.data();half.copy=half_in0.data();
 half.cache[0]=half_a.data();half.cache[1]=half_b.data();half.output[0]=half_out.data();half.active=0;half.active_gen=1;
 half.prepared.store(1);half.offered.store(1);half.generation=1;half.build_gen=1;half.build_slot=-1;
 std::fill(half_in0.begin(),half_in0.end(),0x07E0);half.copy_size=n;half.pending_input=0;half.offered.store(++half.generation);
 for(int k=0;k<HALF/2;++k)assert(half.prepare_batch(n));
 assert(half.active==1&&half.active_step==2&&half.prepared.load()==2);
 for(int k=1;k<HALF;k+=2)assert(half_b[k*half.layout.count]==0xDEAD);   // odd images not built yet
 for(float angle:{1.0f,5.0f,9.9f,181.0f,359.0f}){half.jobs[0].angle=angle;half.jobs[0].interpolate=false;half.render(0);
   for(auto v:half_out)assert(v!=0xDEAD);}
 while(half.prepare_batch(n)){}
 assert(half.active==1&&half.active_step==1&&half_b[half.layout.count]!=0xDEAD);
 // Built on core 1 (from_builder): the worker alone switches, between images; a superseded half never turns.
 Disc core;core.radius=n/2;core.layout.make(n,nullptr);
 std::vector<uint16_t> core_a(core.layout.count*HALF),core_b(core.layout.count*HALF,0xDEAD),core_in0(n*n),core_in1(n*n);
 core.inputs[0]=core_in0.data();core.inputs[1]=core_in1.data();core.copy=core_in0.data();
 core.cache[0]=core_a.data();core.cache[1]=core_b.data();core.active=0;core.active_gen=1;
 core.prepared.store(1);core.generation=1;core.build_gen=1;core.build_slot=-1;
 std::fill(core_in0.begin(),core_in0.end(),0x07E0);core.copy_size=n;core.pending_input=0;core.offered.store(++core.generation);
 for(int k=0;k<Disc::FIRST;++k)assert(core.prepare_batch(n,true));
 assert(core.active==0&&core.publish.pending&&core.publish.step==6);   // turns from every sixth image already
 for(int k=Disc::FIRST;k<HALF/2;++k)assert(core.prepare_batch(n,true));
 assert(core.active==0&&core.publish.pending&&core.publish.step==2&&core.prepared.load()==1);   // not yet switched
 core.apply_publish();
 assert(core.active==1&&core.active_step==2&&core.prepared.load()==2&&!core.publish.pending);
 while(core.prepare_batch(n,true)){}
 assert(core.active_step==2&&core.publish.pending&&core.publish.step==1);
 assert(!core.prepare_batch(n,true)&&core.prepared.load()==2);   // nothing more until the worker has switched
 core.apply_publish();assert(core.active==1&&core.active_step==1);
 core.pending_input=1;core.copy=core.inputs[1];core.offered.store(++core.generation);   // 3: another cover
 for(int k=0;k<HALF/2;++k)assert(core.prepare_batch(n,true));
 assert(core.publish.pending&&core.publish.gen==3&&core.publish.slot==0);
 core.offered.store(++core.generation);                                                    // 4 before the switch
 assert(core.prepare_batch(n,true)&&!core.publish.pending);                                // 3's half is dropped
 core.apply_publish();assert(core.active==1&&core.prepared.load()==2);
 // The next cover's first stage built ahead: the real source of the same picture turns it at once, with the pixels
 // a normal build gives; another picture drops it.
 Disc pre;pre.radius=n/2;pre.layout.make(n,nullptr);
 std::vector<uint16_t> pre_a(pre.layout.count*HALF),pre_b(pre.layout.count*HALF,0xDEAD),pre_in0(n*n),pre_in1(n*n),next(n*n,0x7BEF);
 pre.inputs[0]=pre_in0.data();pre.inputs[1]=pre_in1.data();pre.copy=pre_in0.data();
 pre.cache[0]=pre_a.data();pre.cache[1]=pre_b.data();pre.active=0;pre.active_gen=1;pre.prepared.store(1);pre.offered.store(1);
 pre.generation=1;pre.build_gen=1;pre.picture=11;
 pre.source_ahead(next.data(),n,22);
 assert(pre.ahead.built==Disc::FIRST&&pre.ahead.slot==1&&pre.active==0);          // built, not turned
 for(int k=0;k<Disc::FIRST;++k)assert(pre_b[Disc::build_image(k)*pre.layout.count]!=0xDEAD);
 assert(pre_b[2*pre.layout.count]==0xDEAD);                                          // only the coarse stage
 lv_image_dsc_t next_dsc{};next_dsc.header.cf=LV_COLOR_FORMAT_RGB565;next_dsc.header.w=n;next_dsc.header.h=n;next_dsc.data=reinterpret_cast<const uint8_t*>(next.data());
 next_dsc.data_size=n*n*2;
 { Disc once;once.radius=n/2;once.layout.make(n,nullptr);   // the same built-ahead state, adopted by hand
   std::vector<uint16_t> once_a(once.layout.count*HALF),once_b(once.layout.count*HALF),once_in0(n*n),once_in1(n*n);
   once.inputs[0]=once_in0.data();once.inputs[1]=once_in1.data();once.copy=once_in0.data();
   once.cache[0]=once_a.data();once.cache[1]=once_b.data();once.active=0;once.active_gen=1;once.prepared.store(1);
   once.offered.store(1);once.generation=1;once.build_gen=1;once.picture=11;
   once.source_ahead(next.data(),n,22);assert(once.ahead.built==Disc::FIRST);
   once.offered.store(++once.generation);once.adopt={once.generation,1,once.ahead.input,n,once.ahead.built,once.ahead.row};
   once.ahead=Disc::Ahead{};assert(once.prepare_batch(n));
   assert(once.active==1&&once.active_step==6&&once.prepared.load()==once.generation);   // turns from the first batch
   assert(once.build_frame==Disc::FIRST+1); }                                            // and goes on from image 9
 pre.source(&next_dsc,Kind::PICTURE,22);
 assert(pre.active==1&&pre.active_step==1);
 Disc ref;ref.radius=n/2;ref.layout.make(n,nullptr);std::vector<uint16_t> ref_a(ref.layout.count*HALF),ref_in0(n*n),ref_in1(n*n);
 ref.inputs[0]=ref_in0.data();ref.inputs[1]=ref_in1.data();ref.copy=ref_in0.data();ref.cache[0]=ref_a.data();
 ref.source(&next_dsc,Kind::PICTURE,22);while(ref.prepare_batch(n)){}
 assert(std::equal(ref_a.begin(),ref_a.end(),pre_b.begin()));                       // the same atlas as a normal build
 pre.source_ahead(image.data?reinterpret_cast<const uint16_t*>(image.data):nullptr,n,33);
 pre.source(&next_dsc,Kind::PICTURE,44);assert(pre.ahead.id==0&&pre.adopt.gen!=pre.generation);   // another picture
 // "Föregående": the atlas left keeps its first stage (behind); the same picture again turns from it at once and
 // ends as the atlas a normal build gives. Owned per-image atlases, as on the panel.
 { auto setup=[&](Disc &x,std::vector<uint16_t> &in0,std::vector<uint16_t> &in1){x.radius=n/2;x.layout.make(n,nullptr);
     in0.resize(n*n);in1.resize(n*n);x.inputs[0]=in0.data();x.inputs[1]=in1.data();x.copy=in0.data();x.owned_cache=true;
     assert(x.allocate_atlas(0));};
   std::vector<uint16_t> pa(n*n,0x1234),pb(n*n,0x4321);
   auto dsc=[&](std::vector<uint16_t> &px){lv_image_dsc_t d{};d.header.cf=LV_COLOR_FORMAT_RGB565;d.header.w=n;d.header.h=n;
     d.data=reinterpret_cast<const uint8_t*>(px.data());d.data_size=n*n*2;return d;};
   auto da=dsc(pa),db=dsc(pb);
   Disc back;std::vector<uint16_t> b0,b1;setup(back,b0,b1);
   back.source(&da,Kind::PICTURE,101);assert(back.active==0&&back.active_step==1&&back.active_picture==101);
   back.source(&db,Kind::PICTURE,202);assert(back.active==1&&back.active_picture==202);
   assert(back.behind_id==101&&back.chunks[2][0]&&back.chunks[2][6]&&!back.chunks[2][2]&&!back.has_atlas(0));
   // One batch of the adopted previous cover already turns it.
   back.pending_input=back.build_input==0?1:0;back.copy=back.inputs[back.pending_input];back.picture=101;back.paint(&da,Kind::PICTURE);
   back.offered.store(++back.generation);back.adopt={back.generation,-1,-1,0,0,0,true};
   assert(back.prepare_batch(n)&&back.active==0&&back.active_step==6);
   assert(back.behind_id==202&&back.chunks[2][0]);   // B, just left, is behind now: back and forth both turn at once
   while(back.prepare_batch(n)){}
   assert(back.active==0&&back.active_step==1&&back.behind_id==202);   // and B's first stage is behind now
   Disc ref;std::vector<uint16_t> r0,r1;setup(ref,r0,r1);ref.source(&da,Kind::PICTURE,101);
   for(int i=0;i<HALF;++i)assert(std::equal(ref.chunks[0][i],ref.chunks[0][i]+ref.layout.count,back.chunks[0][i]));
   // The panel's next button: the cover built ahead turns before the skip is confirmed; the confirmation (the
   // same picture) changes nothing.
   std::vector<uint16_t> pc(n*n,0x0F0F);back.source_ahead(pc.data(),n,303);assert(back.ahead.built==Disc::FIRST);
   const auto gen_before=back.generation;assert(back.take_ahead()&&back.generation==gen_before+1&&back.picture==303);
   assert(back.active_picture==303&&back.active_step==1);
   auto dc=dsc(pc);back.source(&dc,Kind::PICTURE,303);assert(back.generation==gen_before+1);    // already shown
   assert(!back.take_ahead());                                                                  // nothing ahead now
   // Through source(): the previous picture is recognised by its identity.
   back.source(&da,Kind::PICTURE,101);assert(back.behind_id==303);
   // PSRAM short: a change drops the previous cover's first stage before it allocates, and nothing is built ahead.
   back.psram_left=[]()->size_t{return Disc::KEEP_FREE;};
   assert(back.chunks[2][0]);back.source(&db,Kind::PICTURE,202);
   // Short all along (here even after the old atlas went): 303's stage went first, then A's, kept a moment, too.
   assert(back.active_picture==202&&back.behind_id==0&&!back.chunks[2][0]);
   std::vector<uint16_t> pd(n*n,0x00FF);back.source_ahead(pd.data(),n,404);assert(back.ahead.id==0); }
 // No room for a second atlas: the builder hands over, the worker stops the record, the builder draws over it.
 Disc lone;lone.radius=n/2;lone.layout.make(n,nullptr);
 std::vector<uint16_t> lone_a(lone.layout.count*HALF),lone_in0(n*n),lone_in1(n*n);
 lone.inputs[0]=lone_in0.data();lone.inputs[1]=lone_in1.data();lone.copy=lone_in0.data();
 lone.cache[0]=lone_a.data();lone.active=0;lone.active_gen=1;lone.prepared.store(1);lone.generation=1;lone.build_gen=1;
 std::fill(lone_in0.begin(),lone_in0.end(),0x001F);lone.copy_size=n;lone.pending_input=0;lone.offered.store(++lone.generation);
 assert(!lone.prepare_batch(n,true)&&lone.build_in_worker.load()&&lone.active==0&&lone.build_slot<0);
 assert(!lone.prepare_batch(n,true)&&lone.prepared.load()==1);   // waits for the worker, never "prepared"
 lone.claim_for_builder();assert(lone.active==-1&&lone.build_slot==0&&!lone.build_in_worker.load());
 while(lone.prepare_batch(n,true)){}
 lone.apply_publish();assert(lone.active==0&&lone.active_step==1&&lone.prepared.load()==2&&lone_a[0]==0x001F);
 d.source(&image,Kind::PICTURE);assert(d.active==0&&d.active_gen==1);
 const auto original=a;
 auto offer=[&](uint16_t colour){d.pending_input=d.build_input==0?1:0;d.copy=d.inputs[d.pending_input];std::fill(d.copy,d.copy+n*n,colour);d.offered.store(++d.generation);};
 offer(0x001F);assert(d.prepare_batch(1));assert(d.build_row==1);assert(a==original); // bounded foreground batch
 d.render();assert(out[n*n/2+n/2]==0xF800);assert(d.output_gen[0]==1);
 offer(0x07E0); // supersede the partially prepared blue cover with green
 while(d.prepare_batch()){};
 assert(a==original&&d.active==1&&d.active_gen==3);
 d.render();assert(out[n*n/2+n/2]==0x07E0&&d.output_gen[0]==3);
 assert(!d.fresh());d.shown_gen=3;assert(d.fresh()); // readiness means shown, not merely downloaded/prepared
 // Moving lines have tight local boxes and retain the same global endpoints.
 Disc geometry;lv_obj_t frame;frame.x=72;frame.y=110;lv_obj_t parts[8];
 geometry.radius=144;geometry.frame=&frame;geometry.parts={parts,parts+1,parts+2,parts+3,parts+4,parts+5,parts+6,parts+7};
 geometry.motion.lift=.5f;geometry.groove=.7f;geometry.place_arm();
 const auto arm=panel_vinyl::arm(216,254,144,.5f,.7f);
 assert(parts[4].x+parts[4].points[0].x==int(arm.pivot_x));
 assert(parts[4].y+parts[4].points[1].y==int(arm.end_y));
 for(int i:{3,4,5,6,7})for(const auto &p:parts[i].points)assert(p.x>=0&&p.x<180&&p.y>=0&&p.y<180);
 // Worker arm pixels are copied into separately owned UI buffers before the next job can change them.
 std::array<std::vector<uint8_t>,6> arm_main,arm_worker;
 const auto sizes=ARM_MASK_CAPACITY;
 for(int i:{0,2,3,4,5}){
   arm_main[i].resize(sizes[i]);arm_worker[i].resize(sizes[i]);
   geometry.line_masks[i].pixels=arm_main[i].data();geometry.line_masks[i].capacity=sizes[i];
   geometry.work_masks[i].pixels=arm_worker[i].data();geometry.work_masks[i].capacity=sizes[i];
 }
 geometry.ready_arm=panel_vinyl::arm(216,254,144,.7f,.4f);
 assert(geometry.prepare_arm(geometry.ready_arm,geometry.work_masks));geometry.arm_async=true;geometry.have_arm_pose=false;
 geometry.place_arm();
 for(int i:{0,2,3,4,5}){
   const auto &m=geometry.line_masks[i];assert(m.pixels==arm_main[i].data()&&m.pixels!=arm_worker[i].data());
   assert(std::equal(m.pixels,m.pixels+m.w*m.h,geometry.work_masks[i].pixels));
   const int crop=std::clamp(360-m.x,0,m.w);
   if(crop<m.w){assert(geometry.line_images[i].data==m.pixels+crop);
     const auto &image=geometry.line_images[i];
     assert(image.header.w==m.w-crop&&image.header.stride==m.w);
     assert(image.data_size>=size_t(image.header.stride)*image.header.h);
     assert(image.data+image.data_size==m.pixels+m.capacity);}
 }
 const auto published_arm=arm_main;arm_worker[2][0]^=255;assert(arm_main==published_arm);
 geometry.work_masks[2].w=100000;assert(!geometry.copy_arm()&&arm_main==published_arm);
 geometry.arm_async=false;
 // A button's frame in flight must not stall the record clock for the whole press transition.
 std::atomic<uint32_t> boundary{1},last{100000};std::atomic<bool> pending{true};
 geometry.clock={&boundary,&last,&pending};geometry.shown=[](){return true;};geometry.playing=true;
 geometry.tick();assert(geometry.applied==1&&geometry.foreground.load());boundary.store(2);geometry.tick();
 assert(geometry.applied==2&&geometry.motion.angle>3.0f);
 // A completed image discarded offscreen must not change ownership of the image still referenced by LVGL.
 geometry.shown=[](){return false;};geometry.displayed=0;geometry.slot_state[0]=Disc::SHOWN;
 geometry.jobs[1].frame=geometry.frame_now+1;geometry.slot_state[1]=Disc::READY;boundary.store(3);
 geometry.tick();assert(geometry.displayed==0&&!geometry.foreground.load()&&geometry.slot_state[0]==Disc::SHOWN);
 assert(geometry.slot_state[1]==Disc::FREE);
 // The queue: a ready image waits for its boundary; the newest due one wins and older ones are dropped.
 geometry.displayed=-1;for(auto &state:geometry.slot_state)state=Disc::FREE;
 geometry.jobs[0].frame=geometry.frame_now+1;geometry.slot_state[0]=Disc::READY;assert(geometry.take_due()==-1);
 geometry.jobs[1].frame=geometry.frame_now-1;geometry.slot_state[1]=Disc::READY;
 geometry.jobs[2].frame=geometry.frame_now;geometry.slot_state[2]=Disc::READY;
 assert(geometry.take_due()==2&&geometry.slot_state[1]==Disc::FREE&&geometry.slot_state[0]==Disc::READY);
 for(auto &state:geometry.slot_state)state=Disc::FREE;
 // A paused visible page has no frame submissions: never wait on one to prepare its next cover.
 geometry.shown=[](){return true;};geometry.playing=false;geometry.motion.settle(false);boundary.store(4);
 geometry.tick();assert(!geometry.foreground.load());
 // A completed image between boundaries is consumed without advancing motion again or queuing twice.
 geometry.playing=true;geometry.motion.settle(true);boundary.store(5);geometry.tick();
 geometry.jobs[1].frame=geometry.frame_now;geometry.slot_state[1]=Disc::READY;
 const auto unchanged_angle=geometry.motion.angle;geometry.tick();
 assert(geometry.slot_state[1]==Disc::SHOWN&&geometry.displayed==1&&geometry.motion.angle==unchanged_angle);
 // Two frames of lookahead with the opaque arm: each boundary takes exactly its own image, the worker never draws
 // into the shown one, and an unchanged arm shares one painted strip while a moved arm gets a free one.
 Disc q;lv_obj_t q_frame,q_image;q.radius=8;q.frame=&q_frame;q.image=&q_image;q.parts=geometry.parts;
 q.shown=[](){return true;};q.layout.make(16,nullptr);std::vector<uint16_t> q_cache(q.layout.count*HALF,0x1234);
 std::vector<uint16_t> q_out[Disc::SLOTS];for(int i=0;i<Disc::SLOTS;++i){q_out[i].resize(256);q.output[i]=q_out[i].data();}
 q.cache[0]=q_cache.data();q.active=0;q.active_gen=1;q.prepared.store(1);q.right_arm.enabled=true;q.playing=true;
 std::atomic<uint32_t> q_boundary{10},q_last{100000};std::atomic<bool> q_pending{false};
 q.clock={&q_boundary,&q_last,&q_pending};
 q.tick();   // opens at frame 10 and fills the queue in one tick (LVGL ticks once per frame): frames 11 and 12
 int queued=0;for(int i=0;i<Disc::SLOTS;++i)if(q.slot_state[i]==Disc::READY){++queued;assert(q.jobs[i].frame==11||q.jobs[i].frame==12);}
 assert(queued==2&&q.displayed==-1);
 float last_angle=-1;
 for(uint32_t f=11;f<20;++f) {
   const auto spare=q.taken_spare;q_boundary.store(f);q.tick();   // one tick per frame, as on the panel
   assert(q.displayed>=0&&q.jobs[q.displayed].frame==f&&q.slot_state[q.displayed]==Disc::SHOWN);
   assert(q.taken_spare==spare+1&&q.taken_boundary==f-10);        // the next image already waits
   assert(q.jobs[q.displayed].angle>last_angle);last_angle=q.jobs[q.displayed].angle;
   for(int i=0;i<Disc::SLOTS;++i)if(i!=q.displayed)assert(q.slot_state[i]!=Disc::SHOWN);
   assert(q.jobs[q.displayed].strip==0&&q.right_arm.shown==0);
 }
 q.groove=.5f;q_boundary.store(20);q.tick();q.tick();
 for(int i=0;i<Disc::SLOTS;++i)if(q.slot_state[i]==Disc::READY&&q.jobs[i].frame==22)
   assert(q.jobs[i].paint_strip&&q.jobs[i].strip!=0&&q.jobs[i].strip>=0);
 // Opening a paused page must refresh a cached playing-arm image even when the album is already fresh.
 Disc parked;lv_obj_t parked_image;std::vector<uint16_t> parked_out0(288*288),parked_out1(288*288);
 parked.radius=144;parked.frame=&frame;parked.image=&parked_image;parked.parts=geometry.parts;
 parked.shown=[](){return true;};parked.playing=false;parked.motion.settle(false);
 parked.layout.make(288,nullptr);std::vector<uint16_t> parked_cache(parked.layout.count*HALF,0xF800);
 parked.cache[0]=parked_cache.data();parked.output[0]=parked_out0.data();parked.output[1]=parked_out1.data();
 std::vector<uint16_t> parked_out2(288*288);parked.output[2]=parked_out2.data();
 parked.arm_async=true;parked.line_masks=geometry.line_masks;parked.work_masks=geometry.work_masks;
 parked.active=0;parked.active_gen=1;parked.prepared.store(1);parked.shown_gen=1;
 boundary.store(42);parked.clock={&boundary,&last,&pending};parked.tick();
 assert(parked.work_total.load()==1&&parked.any_ready());parked.tick();assert(parked.work_total.load()==1);
 boundary.store(43);parked.tick();
 assert(parked.shown_lift==1&&parked.work_total.load()==1&&!parked.any_ready()&&parked.arm_errors.load()==0);
 // A cover change fades (2026-10-31, v2): the old atlas stays for FADE_FRAMES images, its sharp image blended over
 // the new one's packed pixels at a falling weight, then goes as `keep_behind` always did. A new cover meanwhile
 // ends the fade early.
 assert(Disc::fade_weight(-1)==255&&Disc::fade_weight(Disc::FADE_FRAMES)==0&&Disc::fade_weight(0)<255&&Disc::fade_weight(0)>200);
 for(int k=1;k<Disc::FADE_FRAMES;++k)assert(Disc::fade_weight(k)<=Disc::fade_weight(k-1));
 {
   Disc g;g.radius=n/2;g.layout.make(n,nullptr);const size_t c=g.layout.count;
   std::vector<uint16_t> red(c*HALF,0xF800),green(c*HALF,0x07E0),gout(n*n);
   g.cache[0]=red.data();g.cache[1]=green.data();g.output[0]=gout.data();
   g.active=0;g.active_picture=5;g.active_step=1;g.fade_enabled=true;g.generation=2;
   g.publish={1,n,Disc::COARSE,2,true,6};g.apply_publish();
   assert(g.active==1&&g.fade.on&&g.fade_on.load()&&g.fade.slot==0&&g.fade.step==1&&g.fade.picture==5&&g.fades.load()==1);
   const size_t mid=(n/2)*n+n/2;int last_red=32;
   for(int k=0;k<=Disc::FADE_FRAMES;++k) {
     g.jobs[0]={};g.jobs[0].frame=100+k;g.jobs[0].angle=37.0f+k*3.3f;g.jobs[0].interpolate=false;g.render(0);
     const int r=gout[mid]>>11,gr=(gout[mid]>>5)&63;
     assert(r<=last_red);last_red=r;
     if(k==0)assert(r>25);
     if(k==Disc::FADE_FRAMES)assert(r==0&&gr==63&&!g.fade_on.load()&&g.fade.slot==-1);
   }
   assert(g.fade_images.load()==Disc::FADE_FRAMES&&g.fade_cut.load()==0);
   g.publish={0,n,Disc::COARSE,2,true,7};g.apply_publish();          // back to slot 0: the green cover fades now
   assert(g.active==0&&g.fade.on&&g.fade.slot==1&&g.fades.load()==2);
   g.publish={1,n,Disc::COARSE,2,true,8};g.apply_publish();          // another change during it: that fade ends early
   assert(g.active==1&&g.fade_cut.load()==1&&g.fade.on&&g.fade.slot==0&&g.fade.picture==7);
   { std::lock_guard<std::mutex> l(g.source_mutex);g.end_fade(true); }   // the builder needs the old slot
   assert(!g.fade_on.load()&&g.fade_cut.load()==2);
   g.jobs[0].frame=300;g.render(0);assert(gout[mid]==0x07E0);          // no fade: the new cover alone
 }
 // Status formatting owns a bounded immutable copy, even when more than eight dirty areas exist.
 geometry.render_areas=12;geometry.render_pixels=90000;geometry.render_largest=82944;
 for(auto &rect:geometry.render_rects)rect={72,110,359,397};
 const auto status=geometry.deferred_status();geometry.render_rects[0]={0,0,0,0};
 std::cout<<"{\"schema\":1"<<status()<<"}";
}
''')
            subprocess.run(['c++','-std=c++17','-O1','-fsanitize=address,undefined','-I',str(p),'-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            # Show the binary's own assert/ASan report on failure, not only its exit code (2026-10-30: it took
            # a rerun by hand three times to see which assert failed).
            run=subprocess.run([str(p/'test')],capture_output=True,text=True)
            if run.returncode:
                self.fail('native test failed:\n'+run.stderr[-3000:])
            result=json.loads(run.stdout)['vinyl']
            self.assertEqual(result['render_areas'],12)
            self.assertEqual(result['render_pixels'],90000)
            self.assertEqual(result['render_rects'],[[72,110,359,397]]*8)
