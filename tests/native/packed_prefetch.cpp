// Actual LVGL binary decoder, actual prefetch bridge, deterministic fake RTOS tasks.
#include "lvgl.h"
#include "src/draw/lv_image_decoder_private.h"
#include "packed_orb.h"
#include "masked_blend.h"
#include <cassert>
#include <fstream>
#include <iostream>
#include <iterator>
#include <vector>
#include <cstring>
#include <cmath>
#include <set>
extern "C" {
void lv_mem_init() {}
void lv_mem_deinit() {}
void *lv_malloc_core(size_t n) { return std::malloc(n); }
void lv_free_core(void *p) { std::free(p); }
void *lv_realloc_core(void *p, size_t n) { return std::realloc(p,n); }
lv_result_t lv_mem_test_core() { return LV_RESULT_OK; }
void lv_mem_monitor_core(lv_mem_monitor_t *p) { std::memset(p,0,sizeof(*p)); }
}
int main(int argc,char **argv) {
  assert(argc==2);
  std::ifstream input(argv[1],std::ios::binary);
  std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(input)), {});
  packed_orb::Atlas atlas;
  assert(atlas.open(bytes.data(),bytes.size(),211));
  lv_init();
  uint64_t mask_comparisons=0;
  for(unsigned fg=0;fg<65536;++fg)for(uint16_t bg:{uint16_t(0),uint16_t(65535),uint16_t(0x7e0),uint16_t(0xf81f)}) {
    for(uint8_t alpha:{uint8_t(1),uint8_t(2),uint8_t(3)}) { assert(lv_color_16_16_mix(fg,bg,alpha)==bg);++mask_comparisons; }
    for(uint8_t alpha:{uint8_t(252),uint8_t(253),uint8_t(254)}) { assert(lv_color_16_16_mix(fg,bg,alpha)==fg);++mask_comparisons; }
  }
  constexpr unsigned mw=17,mh=11,ds=22,ss=24,ms=23;
  std::vector<uint16_t> mask_source(ss*mh+8),mask_destination(ds*mh+8),mask_reference;
  std::vector<uint8_t> masks(ms*mh+8);
  for(size_t i=0;i<mask_source.size();++i)mask_source[i]=uint16_t(i*71+3);
  for(unsigned alpha=0;alpha<256;++alpha) {
    for(size_t i=0;i<mask_destination.size();++i)mask_destination[i]=uint16_t(i*19+99);
    mask_reference=mask_destination;
    for(size_t i=0;i<masks.size();++i)masks[i]=uint8_t(alpha+i*31);
    for(unsigned y=0;y<mh;++y)for(unsigned x=0;x<mw;++x) {
      auto &out=mask_reference[3+y*ds+x];out=lv_color_16_16_mix(mask_source[1+y*ss+x],out,masks[1+y*ms+x]);++mask_comparisons;
    }
    const auto stats=panel_debug_copy::masked_rows<false>(reinterpret_cast<uint8_t*>(mask_destination.data()+3),ds*2,
        reinterpret_cast<const uint8_t*>(mask_source.data()+1),ss*2,0,masks.data()+1,ms,mw,mh,lv_color_16_16_mix);
    assert(mask_destination==mask_reference && stats.pixels==mw*mh);
  }
  lv_image_cache_resize(0,true);
  // atlas v7: 96 listen + 24 swing + 72 speak + 18 rest + the empty glass, then the virtual timelines
  // (the director's ring follows them and is checked at the end).
  constexpr unsigned RAW=211,V_LISTEN=RAW,V_THINK=V_LISTEN+314,V_MORPH=V_THINK+109,V_SPEAK=V_MORPH+39,V_SETTLE=V_SPEAK+181;
  constexpr unsigned TOTAL=RAW+packed_orb::VIRTUAL_TOTAL;  // 211 raw + 679 virtual images
  static_assert(TOTAL==890 && V_SETTLE+36==TOTAL);
  std::array<lv_image_dsc_t,TOTAL> frames{};
  std::array<packed_orb::Recipe,TOTAL> recipes{};
  std::vector<std::vector<uint8_t>> expected;
  for(unsigned i=0;i<RAW;++i) {
    auto &f=frames[i];
    f.header.magic=LV_IMAGE_HEADER_MAGIC;
    f.header.cf=LV_COLOR_FORMAT_RGB565A8;
    f.header.flags=LV_IMAGE_FLAGS_COMPRESSED;
    f.header.w=f.header.h=220; f.header.stride=440;
    f.data=atlas.frame(i); f.data_size=atlas.frame_size(i);
    lv_image_decoder_dsc_t dsc;
    assert(lv_image_decoder_open(&dsc,&f,nullptr)==LV_RESULT_OK);
    assert(dsc.decoded->data_size==packed_orb::FRAME_BYTES);
    expected.emplace_back(dsc.decoded->data,dsc.decoded->data+packed_orb::FRAME_BYTES);
    lv_image_decoder_close(&dsc);
  }
  packed_orb::virtual_sequence(recipes.data(),V_LISTEN,314,0,96,true);
  packed_orb::virtual_sequence(recipes.data(),V_THINK,109,0,96,true);
  packed_orb::virtual_sequence(recipes.data(),V_MORPH,39,96,24,false,V_SPEAK);
  packed_orb::virtual_sequence(recipes.data(),V_SPEAK,181,120,72,true);
  packed_orb::virtual_sequence(recipes.data(),V_SETTLE,36,192,18,false);
  for(unsigned i=RAW;i<TOTAL;++i){
    const auto &r=recipes[i]; frames[i]=frames[r.first];
    auto result=expected[r.first];
    const auto &a=expected[r.first],&b=expected[r.second];
    const double amount=double(r.weight)/256;
    auto blend=[amount](unsigned x,unsigned y){return unsigned(std::lround(x*(1-amount)+y*amount));};
    for(unsigned p=0;p<220*220;++p){
      const unsigned x=a[p*2]+256*a[p*2+1],y=b[p*2]+256*b[p*2+1];
      unsigned color=0;
      for(auto channel:std::array<std::pair<unsigned,unsigned>,3>{{{11,31},{5,63},{0,31}}})
        color|=blend((x>>channel.first)&channel.second,(y>>channel.first)&channel.second)<<channel.first;
      result[p*2]=color&255;result[p*2+1]=color>>8;
      result[220*220*2+p]=blend(a[220*220*2+p],b[220*220*2+p]);
    }
    expected.push_back(std::move(result));
  }
  auto *display=lv_display_create(32,32);
  static uint8_t pixels[32*32*2];
  lv_display_set_color_format(display,LV_COLOR_FORMAT_RGB565);
  lv_display_set_buffers(display,pixels,nullptr,sizeof(pixels),LV_DISPLAY_RENDER_MODE_FULL);
  lv_display_set_flush_cb(display,[](lv_display_t*d,const lv_area_t*,uint8_t*){lv_display_flush_ready(d);});
  unsigned invalidations=0;
  lv_display_add_event_cb(display,[](lv_event_t *e){++*static_cast<unsigned*>(lv_event_get_user_data(e));},
                          LV_EVENT_INVALIDATE_AREA,&invalidations);
  std::array<lv_obj_t*,5> objects{};
  const unsigned durations[]{5200,1800,650,3000,600};
  for(unsigned i=0;i<5;++i){objects[i]=lv_animimg_create(lv_screen_active());lv_animimg_set_duration(objects[i],durations[i]);}
  const bool installed=packed_orb::install<96,24,72,18,1>(atlas,objects[0],objects[1],objects[2],objects[3],objects[4]);
  assert(installed);
  std::array<const lv_image_dsc_t*,TOTAL> actual{};
  unsigned cursor=0;
  for(unsigned state:{0U,2U,3U,4U})for(size_t i=0;i<packed_orb::timelines.native_counts[state];++i)
    actual[cursor++]=static_cast<const lv_image_dsc_t*>(packed_orb::timelines.native[state][i]);
  actual[cursor++]=static_cast<const lv_image_dsc_t*>(packed_orb::timelines.native[4][17])+1;  // the empty glass
  assert(cursor==RAW);
  for(unsigned state=0;state<5;++state)for(size_t i=0;i<packed_orb::timelines.smooth_counts[state];++i)
    actual[cursor++]=static_cast<const lv_image_dsc_t*>(packed_orb::timelines.smooth[state][i]);
  assert(cursor==TOTAL);
  assert(packed_orb::source_count(objects[0])==314 && lv_animimg_get_src_count(objects[0])<=127);
  packed_orb::select_timeline(false);assert(lv_animimg_get_src_count(objects[0])==96);
  packed_orb::select_timeline(true);assert(packed_orb::source_count(objects[0])==314);
  for(unsigned i=0;i<5;++i)assert(lv_animimg_get_duration(objects[i])==durations[i]);
  // The terminal value repeats the last clamped source. It must not trigger a
  // second redraw after the decoder's output has become reusable by prefetch.
  packed_orb::Timelines::index(objects[0],313);lv_refr_now(display);invalidations=0;
  packed_orb::Timelines::index(objects[0],314);
  packed_orb::Timelines::index(objects[0],313);
  assert(invalidations==0 && lv_image_get_src(objects[0])==actual[V_THINK-1]);
  // Frame-clock pacing: one image per display frame boundary, chosen from the boundary
  // count. Loops wrap, one-shots hold their last image, deleted animations freeze,
  // a restart begins at image 0 and a stalled clock falls back to the animation value.
  {
    vsync::Clock clock;
    uint32_t now=esphome::micros()-3*16560;
    for(unsigned i=0;i<3;++i){clock.frame(now,true);now+=16560;}
    packed_orb::timelines.set_clock(&clock);
    for(unsigned i=0;i<5;++i)lv_animimg_start(objects[i]);
    packed_orb::Timelines::index(objects[0],77);  // ignored while the clock is live
    assert(lv_image_get_src(objects[0])!=actual[V_LISTEN+77]);
    const uint32_t base=clock.count.load();
    for(unsigned tick=1;tick<=400;++tick){
      clock.frame(esphome::micros(),true);
      packed_orb::timelines.step();packed_orb::timelines.step();  // a second pass in the same frame is a no-op
      const uint32_t elapsed=clock.count.load()-(base+1);
      assert(lv_image_get_src(objects[0])==actual[V_LISTEN+elapsed%314]);
      assert(lv_image_get_src(objects[1])==actual[V_THINK+elapsed%109]);
      assert(lv_image_get_src(objects[2])==actual[V_MORPH+std::min<uint32_t>(elapsed,38)]);
      assert(lv_image_get_src(objects[3])==actual[V_SPEAK+elapsed%181]);
      assert(lv_image_get_src(objects[4])==actual[V_SETTLE+std::min<uint32_t>(elapsed,35)]);
    }
    packed_orb::restart(objects[2]);
    assert(lv_image_get_src(objects[2])==actual[V_MORPH]);
    // A step in the same frame as the restart must not jump to the one-shot's last image.
    packed_orb::timelines.applied=UINT32_MAX;packed_orb::timelines.step();
    assert(lv_image_get_src(objects[2])==actual[V_MORPH]);
    clock.frame(esphome::micros(),true);packed_orb::timelines.step();
    assert(lv_image_get_src(objects[2])==actual[V_MORPH]);  // the boundary after a restart still shows image 0
    clock.frame(esphome::micros(),true);packed_orb::timelines.step();
    assert(lv_image_get_src(objects[2])==actual[V_MORPH+1]);
    // A boundary with a submitted but unpresented buffer is skipped; the next one catches up.
    clock.submitted();
    const auto *held=lv_image_get_src(objects[0]);
    clock.frame(esphome::micros(),false);packed_orb::timelines.step();
    assert(lv_image_get_src(objects[0])==held && clock.pending.load());
    clock.frame(esphome::micros(),true);packed_orb::timelines.step();
    assert(!clock.pending.load());
    assert(lv_image_get_src(objects[0])==actual[V_LISTEN+(clock.count.load()-(base+1))%314]);
    lv_anim_delete(objects[0],nullptr);
    const auto *frozen=lv_image_get_src(objects[0]);
    for(unsigned i=0;i<5;++i){clock.frame(esphome::micros(),true);packed_orb::timelines.step();}
    assert(lv_image_get_src(objects[0])==frozen);
    lv_animimg_start(objects[0]);
    // A frame clock that stops for 100 ms hands control back to the animation value.
    packed_orb::Timelines::index(objects[0],5);assert(lv_image_get_src(objects[0])!=actual[V_LISTEN+5]);
    std::this_thread::sleep_for(std::chrono::milliseconds(120));
    packed_orb::Timelines::index(objects[0],5);assert(lv_image_get_src(objects[0])==actual[V_LISTEN+5]);
    packed_orb::timelines.step();assert(lv_image_get_src(objects[0])==actual[V_LISTEN+5]);
    packed_orb::timelines.set_clock(nullptr);
  }
  // Listen and think share artwork. Switching between their different speeds must
  // carry the actual visible phase, rather than jump to a separately running loop.
  unsigned phase_handoffs=0;
  {
    vsync::Clock clock;clock.frame(esphome::micros(),true);
    packed_orb::timelines.set_clock(&clock);
    for(unsigned from:{0U,1U}) {
      const unsigned to=1-from;
      const auto n=packed_orb::timelines.smooth_counts[from],m=packed_orb::timelines.smooth_counts[to];
      for(unsigned i=0;i<5;++i)lv_obj_add_flag(objects[i],LV_OBJ_FLAG_HIDDEN);
      lv_obj_remove_flag(objects[from],LV_OBJ_FLAG_HIDDEN);
      for(size_t frame=0;frame<n;++frame) {
        clock.frame(esphome::micros(),true);
        lv_image_set_src(objects[from],packed_orb::timelines.smooth[from][frame]);
        packed_orb::sync_loop_phase(objects[to]);
        const auto *src=lv_image_get_src(objects[to]);
        const auto *begin=packed_orb::timelines.smooth[to];
        const auto index=std::find(begin,begin+m,src)-begin;
        assert(size_t(index)<m);
        const double delta=std::abs(double(frame)/n-double(index)/m);
        assert(std::min(delta,1-delta)<=.5/m+1e-9);
        clock.frame(esphome::micros(),true);packed_orb::timelines.step();
        assert(lv_image_get_src(objects[to])==begin[(index+1)%m]);
        ++phase_handoffs;
      }
    }
    packed_orb::timelines.set_clock(nullptr);
    for(unsigned i=0;i<5;++i)lv_obj_remove_flag(objects[i],LV_OBJ_FLAG_HIDDEN);
  }
  std::vector<unsigned> sequence;
  for(unsigned round=0;round<3;++round) for(unsigned i=0;i<96;++i) sequence.push_back(i);
  for(unsigned round=0;round<3;++round) for(unsigned i=0;i<96;i+=3) sequence.push_back(i);
  for(unsigned round=0;round<3;++round) for(unsigned i=0;i<96;i+=2) sequence.push_back(i);
  for(unsigned i=96;i<120;++i) sequence.push_back(i);
  for(unsigned round=0;round<3;++round) for(unsigned i=120;i<192;++i) sequence.push_back(i);
  for(unsigned i=192;i<RAW;++i) sequence.push_back(i);
  for(unsigned round=0;round<2;++round) for(unsigned i=0;i<RAW;++i) sequence.push_back((i*71)%RAW);
  for(unsigned round=0;round<2;++round) for(unsigned i=RAW;i<TOTAL;++i) sequence.push_back(i);
  for(unsigned attempt=0;attempt<100 && packed_orb::prefetch.deferred_status()().find("orb_probe_11_us")==std::string::npos;++attempt)
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  assert(packed_orb::prefetch.deferred_status()().find("orb_probe_11_us")!=std::string::npos);
  for(unsigned method:{1U,2U,3U}) {
   packed_orb::prefetch.set_method(method);
   for(auto i:sequence) {
    lv_image_decoder_dsc_t dsc;
    assert(lv_image_decoder_open(&dsc,actual[i],nullptr)==LV_RESULT_OK);
    assert(dsc.decoded->header.cf==LV_COLOR_FORMAT_RGB565A8);
    assert(dsc.decoded->header.stride==440 && dsc.decoded->header.w==220 && dsc.decoded->header.h==220);
    assert(std::memcmp(dsc.decoded->data,expected[i].data(),packed_orb::FRAME_BYTES)==0);
    lv_image_decoder_close(&dsc);
    std::this_thread::sleep_for(std::chrono::milliseconds(1));
   }
  }
  for(unsigned i=1;i<5;++i)lv_obj_add_flag(objects[i],LV_OBJ_FLAG_HIDDEN);
  lv_timer_set_period(lv_display_get_refr_timer(display),1);
  lv_timer_set_period(lv_anim_get_timer(),1);
  lv_animimg_start(objects[0]);
  std::set<const void*> seen;
  for(unsigned i=0;i<400;++i){
    lv_tick_inc(17);lv_timer_handler();
    const auto *source=lv_image_get_src(objects[0]);
    assert(std::find(actual.begin()+V_LISTEN,actual.begin()+V_THINK,source)!=actual.begin()+V_THINK);
    seen.insert(source);
    std::this_thread::sleep_for(std::chrono::milliseconds(2));
  }
  assert(seen.size()>140); // A clamped int8_t count previously rendered one wrong source forever.
  // The director: every frame's recipe is written AHEAD frames early and chained for the prefetch
  // worker; the shown image is the ring slot of the boundary, and decodes to the exact blend.
  unsigned directed_frames=0;
  {
    using Core=packed_orb::DirectorCore;
    auto &tl=packed_orb::timelines;
    vsync::Clock clock;clock.frame(esphome::micros(),true);
    tl.set_clock(&clock);
    auto show=[&](unsigned state){for(unsigned i=0;i<5;++i)lv_obj_add_flag(objects[i],LV_OBJ_FLAG_HIDDEN);
                                  lv_obj_remove_flag(objects[state],LV_OBJ_FLAG_HIDDEN);};
    for(unsigned i=0;i<5;++i)lv_animimg_start(objects[i]);
    show(0);
    assert(packed_orb::direct());
    const size_t ring=tl.ring_base;
    assert(ring==TOTAL);
    std::vector<float> wave_scratch(voice_wave::SCRATCH_FLOATS);
    auto expected_at=[&](size_t at){
      const auto &r=tl.ring_recipes[at];
      std::vector<uint8_t> out(packed_orb::FRAME_BYTES);
      if(r.second==packed_orb::WAVE){out=expected[r.first];voice_wave::render(out.data(),tl.ring_waves[at],wave_scratch.data());}
      else packed_orb::interpolate565a8_original(out.data(),expected[r.first].data(),expected[r.second].data(),220*220,r.weight);
      return out;};
    auto tick=[&](){
      clock.frame(esphome::micros(),true);tl.step();
      const uint32_t b=clock.count.load();
      const size_t at=ring+b%packed_orb::RING;
      for(unsigned i=0;i<5;++i)assert(lv_image_get_src(objects[i])==&tl.ring_frames[at]);
      for(unsigned k=0;k<=packed_orb::AHEAD;++k)
        assert(tl.ring_recipes[ring+(b+k)%packed_orb::RING].next==ring+(b+k+1)%packed_orb::RING);
      lv_image_decoder_dsc_t dsc;
      assert(lv_image_decoder_open(&dsc,lv_image_get_src(objects[0]),nullptr)==LV_RESULT_OK);
      assert(std::memcmp(dsc.decoded->data,expected_at(at).data(),packed_orb::FRAME_BYTES)==0);
      lv_image_decoder_close(&dsc);
      std::this_thread::sleep_for(std::chrono::milliseconds(1));
      ++directed_frames;
      return tl.ring_recipes[at];};
    auto in=[](const packed_orb::Recipe &r,unsigned lo,unsigned hi){return r.first>=lo&&r.first<hi&&r.second>=lo&&r.second<hi;};
    auto drawn=[](const packed_orb::Recipe &r){return r.first==210&&r.second==packed_orb::WAVE;};
    for(int i=0;i<40;++i)assert(in(tick(),0,96));
    assert(std::abs(tl.director.cur.speed-Core::LISTEN_SPEED)<1e-9);
    // Thinking: the same loop speeds up over the ramp, never by a jump.
    show(1);
    double last=tl.director.cur.speed;
    for(unsigned i=0;i<Core::RAMP+packed_orb::AHEAD+2;++i){
      assert(in(tick(),0,96));
      assert(tl.director.cur.speed>=last-1e-12 && tl.director.cur.speed-last<0.06);
      last=tl.director.cur.speed;
    }
    assert(std::abs(last-Core::THINK_SPEED)<1e-9);
    // The swing starts where the loop reaches calm_00 = swing_00: never a cross-fade.
    show(2);
    unsigned waited=0;
    for(;;++waited){
      const auto r=tick();
      assert(in(r,0,96)||in(r,96,120));
      if(in(r,96,120)){assert(r.first==96&&r.weight==0);break;}
      assert(waited<Core::CATCH+packed_orb::AHEAD+12);
    }
    for(int i=0;i<40;++i)assert(in(tick(),96,120));
    // Speaking: the drawn wave on the empty glass, its lines following the voice; every image decodes
    // to exactly what voice_wave::render draws.
    packed_orb::voice_source=[]()->float{return 0.02f;};
    show(3);
    for(unsigned i=0;i<packed_orb::AHEAD+1;++i)tick();
    for(int i=0;i<60;++i)assert(drawn(tick()));
    assert(tl.director.wave.amp[0]>0.9f);
    // Settling: the wave sinks to the flat line (rest_17) and holds it.
    packed_orb::voice_source=[]()->float{return 0.0006f;};
    show(4);
    for(unsigned i=0;i<packed_orb::AHEAD;++i)assert(drawn(tick()));
    for(unsigned i=0;i<Core::HUSH_FRAMES-1;++i)assert(drawn(tick()));
    for(int i=0;i<20;++i){const auto r=tick();assert(r.first==209&&(r.weight==0||r.second==209));}
    tl.directed=false;
    packed_orb::voice_source=nullptr;
    tl.set_clock(nullptr);
  }
  fake_rtos_stop();
  std::cout << "{\"native_mask_pixel_comparisons\":" << mask_comparisons << ",\"directed_frames_checked\":" << directed_frames << ",\"phase_handoffs_checked\":" << phase_handoffs << ",\"terminal_duplicate_invalidations\":0,\"exact_pixel_comparisons\":" << sequence.size()*3 << ",\"interpolation_methods_checked\":3,\"animated_unique_sources\":" << seen.size()
            << packed_orb::prefetch.deferred_status()() << "}\n";
  lv_deinit();
  fake_heap_free();
}
