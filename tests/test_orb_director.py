"""Native test of the orb director (styles/glass-v2/orb_director.h): planned changes enter at a frame both
states share (no cross-fade), speeds ease, and the drawn voice wave's lines follow the voice."""
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

PROGRAM = r'''
#include <cassert>
#include <cstdio>
#include <random>
#include "orb_director.h"
using packed_orb::DirectorCore; using packed_orb::Recipe; using M=DirectorCore;
// atlas v7: 96 listen, 24 swing, 72 talk, 18 rest, then the empty glass (210)
constexpr unsigned L0=0,L1=96,M0=96,M1=120,R1=210,HOLLOW=210;
static bool in(const Recipe &r,unsigned lo,unsigned hi){return r.first>=lo&&r.first<hi&&r.second>=lo&&r.second<hi;}
static bool wave(const Recipe &r){return r.first==HOLLOW&&r.second==packed_orb::WAVE;}
static bool valid(const Recipe &r){return wave(r)||(r.first<R1&&r.second<R1&&r.weight<=256);}
static bool exact(const Recipe &r,unsigned frame){return r.first==frame&&(r.weight==0||r.second==frame);}
struct Run { DirectorCore d; unsigned frames=0; Recipe last{};
  Run(){d.setup(96,24,72,18,HOLLOW);}
  Recipe step(double level,bool fade_ok=false){d.voice(level);last=d.advance();assert(valid(last));assert(fade_ok||!d.fading);++frames;return last;}
  Recipe until(M::Mode mode,double level,unsigned limit){
    for(unsigned i=0;i<limit;++i){auto r=step(level);if(d.cur.mode==mode)return r;}
    assert(!"mode not reached");return {};}
};
int main(){
  Run a;
  // Listening: one steady speed in the calm loop, livelier while someone speaks.
  for(int i=0;i<200;++i)assert(in(a.step(0.0006),L0,L1));
  assert(std::abs(a.d.cur.speed-M::LISTEN_SPEED)<1e-9);
  for(int i=0;i<90;++i)assert(in(a.step(0.005+0.002*((i/7)%2)),L0,L1));
  assert(a.d.cur.speed>M::LISTEN_SPEED*1.4);
  for(int i=0;i<200;++i)a.step(0.0006);
  // Thinking eases up to its speed, never by a jump.
  a.d.change(M::THINK);
  double last=a.d.cur.speed;
  for(unsigned i=0;i<M::RAMP+5;++i){assert(in(a.step(0.0006),L0,L1));
    assert(a.d.cur.speed>=last-1e-12&&a.d.cur.speed-last<0.06);last=a.d.cur.speed;}
  assert(std::abs(last-M::THINK_SPEED)<1e-9);
  // The swing starts where the loop reaches calm_00 = swing_00, without a cross-fade, soon.
  a.d.change(M::MORPH);
  const unsigned asked=a.frames;
  auto r=a.until(M::MORPH,0.0006,M::CATCH+20);
  assert(exact(r,M0)&&a.frames-asked<=M::CATCH+12);
  // The swing's last frame is talk_00: the drawn wave takes over at t 0 and talk_00's height.
  a.d.change(M::SPEAK);
  for(int i=0;i<80&&a.d.cur.mode==M::MORPH;++i){auto f=a.step(0.0006);assert(in(f,M0,M1)||(wave(f)&&a.d.cur.mode==M::SPEAK));}
  assert(wave(a.last)&&a.d.wave.t==0&&a.d.wave.amp[0]==M::START_HEIGHT&&a.d.wave.amp[2]==M::START_HEIGHT);
  // Before his voice the lines sink low; with it they rise, the first at once, all within a few frames.
  for(int i=0;i<40;++i)assert(wave(a.step(0.0006)));
  for(float h:a.d.wave.amp)assert(h<0.25f);
  a.step(0.025);
  assert(a.d.wave.amp[0]>0.3f&&a.d.wave.amp[0]>a.d.wave.amp[2]);
  for(int i=0;i<30;++i)a.step(0.025);
  for(float h:a.d.wave.amp)assert(h>0.9f);
  // Heights never jump: at most 0.14 up and 0.05 down per frame, and the phase keeps flowing.
  std::mt19937 random(3);
  float before[3];for(int i=0;i<3;++i)before[i]=a.d.wave.amp[i];
  float phase=a.d.wave.t;
  for(int i=0;i<600;++i){
    const double v=(random()%4)?0.003+0.03*(random()%100)/100.0:0.0006;
    assert(wave(a.step(v)));
    for(int k=0;k<3;++k){assert(a.d.wave.amp[k]-before[k]<=0.14f+1e-6&&before[k]-a.d.wave.amp[k]<=0.05f+1e-6);before[k]=a.d.wave.amp[k];}
    const float moved=std::fmod(a.d.wave.t-phase+1.f,1.f);
    assert(moved>0&&moved<2.5f/181);phase=a.d.wave.t;
  }
  // Settling: the drawn wave sinks to the flat line (rest_17) and holds it.
  a.d.change(M::SETTLE);
  for(unsigned i=0;i<M::HUSH_FRAMES-1;++i)assert(wave(a.step(0.0006)));
  for(int i=0;i<30;++i)assert(exact(a.step(0.0006),R1-1));
  // Listening again from the flat line cross-fades.
  a.d.change(M::LISTEN);
  for(unsigned i=1;i<M::FADE;++i){auto f=a.step(0.0006,true);assert(a.d.fading&&f.first==R1-1&&f.second<L1);}
  // A new question while he speaks: the wave sinks flat quickly, then cross-fades into listening.
  Run b;
  b.d.change(M::MORPH);b.until(M::MORPH,0.0006,200);
  b.d.change(M::SPEAK);b.until(M::SPEAK,0.02,80);
  for(int i=0;i<60;++i)b.step(0.02);
  b.d.change(M::LISTEN);
  for(unsigned i=0;i<M::QUICK_HUSH-1;++i)assert(wave(b.step(0.0006)));
  for(unsigned i=1;i<M::FADE;++i){auto f=b.step(0.0006,true);assert(f.first==R1-1&&f.second<L1);}
  // The speaking_live test voice (tools/panel wave) lifts the drawn wave while it "talks" and lets it sink in
  // its pauses, like a reply: at 60.385 Hz, from the swing on.
  Run s;
  s.d.change(M::MORPH);s.until(M::MORPH,0.0006,200);
  s.d.change(M::SPEAK);
  float high=0,low=2;unsigned ms=0;
  for(int i=0;i<1200;++i){ms=unsigned(i*1000/60.385);s.step(packed_orb::synthetic_speech(ms));
    if(s.d.cur.mode!=M::SPEAK)continue;
    const double t=ms/1000.0;
    if(i>120&&std::fmod(t,7.0)>5.2&&std::fmod(t,7.0)<5.8)high=std::max(high,s.d.wave.amp[0]);
    if(std::fmod(t,7.0)>6.7)low=std::min(low,s.d.wave.amp[0]);}
  assert(s.d.cur.mode==M::SPEAK&&high>0.8f&&low<0.45f);
  // Any order of changes and voices keeps every recipe valid.
  DirectorCore c; c.setup(96,24,72,18,HOLLOW);
  for(int i=0;i<40000;++i){
    if(random()%40==0)c.change(M::Mode(random()%5));
    c.voice((random()%3)?0.03*(random()%100)/100.0:0.0003);
    const auto f=c.advance();assert(valid(f));
    for(float h:c.wave.amp)assert(h>=0&&h<=1.11f);
  }
  std::puts("ok");
}
'''


@unittest.skipUnless(shutil.which("clang++") or shutil.which("g++"), "C++ compiler unavailable")
class OrbDirectorTest(unittest.TestCase):
    def test_seamless_states_eased_speeds_and_voice_driven_wave(self):
        with tempfile.TemporaryDirectory(prefix="hallpanel-director-") as tmp:
            source, binary = Path(tmp) / "director.cpp", Path(tmp) / "director"
            source.write_text(PROGRAM)
            subprocess.run([shutil.which("clang++") or shutil.which("g++"), "-std=c++17", "-O1", "-Wall", "-Werror",
                            "-fsanitize=address,undefined", "-I", str(ROOT / "styles/glass-v2"), str(source),
                            "-o", str(binary)], check=True, capture_output=True)
            result = subprocess.run([str(binary)], check=True, capture_output=True, text=True)
            self.assertEqual(result.stdout.strip(), "ok")


if __name__ == "__main__":
    unittest.main()
