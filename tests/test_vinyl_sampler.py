"""Sparse atlas fidelity, half-turn lookup and continuous start/stop interpolation, under ASan/UBSan."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class VinylSamplerTest(unittest.TestCase):
    def test_sparse_atlas_preserves_sharp_frames_and_interpolates_slow_angles(self):
        cpp=r'''
#include "styles/glass-v2/vinyl_motion.h"
#include "styles/glass-v2/vinyl_sampler.h"
#include <vector>
#include <cassert>
#include <cmath>
using namespace panel_vinyl;
// Original complete-row rasterizer: reference for cache tiling, including fixed-point rounding and alpha.
void linear_rows(const uint16_t *src,int n,uint16_t *dst,const Layout &layout,float degrees,
                 const uint16_t *layer=nullptr,const uint8_t *alpha=nullptr){
 const int size=layout.size;const float k=float(n)/size,a=degrees*3.14159265f/180,c=std::cos(a)*k,s=std::sin(a)*k;
 const int32_t du=int32_t(c*65536),dv=int32_t(-s*65536);
 for(const auto &span:layout.spans){
  const float fy=span.y+.5f-size/2.0f,fx=span.x+.5f-size/2.0f;
  int32_t u=int32_t((n/2.0f+fx*c+fy*s-.5f)*65536),v=int32_t((n/2.0f-fx*s+fy*c-.5f)*65536);
  for(int x=0;x<span.length;++x,u+=du,v+=dv){
   int ix=u>>16,iy=v>>16;uint32_t wx=(u>>11)&31,wy=(v>>11)&31;
   if(ix<0){ix=0;wx=0;}if(iy<0){iy=0;wy=0;}if(ix>=n-1){ix=n-2;wx=31;}if(iy>=n-1){iy=n-2;wy=31;}
   const uint16_t *p=src+iy*n+ix;
   const auto top=mix565(spread565(p[0]),spread565(p[1]),wx),bottom=mix565(spread565(p[n]),spread565(p[n+1]),wx);
   auto value=mix565(top,bottom,wy);const int i=span.y*size+span.x+x;
   if(alpha){const uint32_t w=(uint32_t(alpha[i])+4)>>3;if(w)value=mix565(value,spread565(layer[i]),w);}
   dst[span.packed+x]=pack565(value);
  }
 }
}
int main(){
for(size_t count:{0,1,2,3,63,64,4095,4096,4097,64640})for(size_t capacity:{0,1,2,3,16,4096}){
 std::vector<uint16_t> values(count+2,0xCAFE),tile(capacity+2,0xBABE);
 for(size_t i=0;i<count;++i)values[i+1]=uint16_t(i*71);auto expected=values;
 std::reverse(expected.begin()+1,expected.end()-1);
 reverse_staged(values.data()+1,count,tile.data()+1,capacity);
 assert(values==expected&&tile.front()==0xBABE&&tile.back()==0xBABE);
}
WorkerPriority priority;std::vector<int> applied;auto apply=[&](int p){applied.push_back(p);};
assert(!atlas_budget(true,true,false,5000,true)); // UI rendering owns its memory bandwidth.
assert(atlas_budget(true,true,true,13999,true)==4);assert(atlas_budget(true,true,true,14000,true)==1);
assert(atlas_budget(true,true,true,15999,true)==1);assert(!atlas_budget(true,true,true,16000,true));
assert(!atlas_budget(true,true,true,17000,true));
assert(atlas_budget(true,true,false,5000,true,true)==4); // two queued images: the next boundary is already covered
assert(atlas_budget(false,true,false,17000,true)); // Background stays below the orb worker.
assert(atlas_budget(true,false,false,17000,true));assert(atlas_budget(true,true,false,17000,false));
assert(priority.current<1); // background work never outranks the orb's priority 1
priority.update(true,apply);priority.update(true,apply);priority.update(false,apply);
priority.update(false,apply);priority.update(true,apply);
assert((applied==std::vector<int>{2,0,2}));
const int n=64;Layout l;l.make(n,nullptr);
assert(l.count<n*n&&l.count>n*n*.7);
std::vector<uint16_t> src(n*n),atlas(l.count*HALF),out(n*n),reference(n*n),previous(n*n),staged(n*n),scratch(n*2);
for(int y=0;y<n;++y)for(int x=0;x<n;++x)src[y*n+x]=uint16_t(((x*31/n)<<11)|((y*63/n)<<5)|((x+y)*31/(2*n)));
for(int i=0;i<HALF;++i)for(int y=0;y<n;y+=4)prepare_rows(src.data(),n,atlas.data()+i*l.count,l,y,std::min(n,y+4),i*(180.0f/HALF),nullptr,nullptr);
std::vector<uint16_t> original_atlas(atlas.size());
for(int i=0;i<HALF;++i)linear_rows(src.data(),n,original_atlas.data()+i*l.count,l,i*(180.0f/HALF));
assert(atlas==original_atlas);
for(float angle=0;angle<360;angle+=.7f){
 atlas_frame(atlas.data(),out.data(),l,angle,true);
 atlas_frame(atlas.data(),staged.data(),l,angle,true,scratch.data(),scratch.size());assert(staged==out);
 atlas_frame(atlas.data(),staged.data(),l,angle,true,scratch.data(),1);assert(staged==out);
}
for(int i=0;i<2*HALF;++i){const float angle=i*(180.0f/HALF);atlas_frame(atlas.data(),out.data(),l,angle,false);rotate(src.data(),n,reference.data(),n,angle,0);
 atlas_frame(atlas.data(),staged.data(),l,angle,false,scratch.data(),scratch.size());assert(staged==out);
 atlas_frame(atlas.data(),staged.data(),l,angle,false,scratch.data(),1);assert(staged==out); // Too-small tile keeps the safe path.
 for(size_t j=0;j<out.size();++j){
 assert(std::abs(int(out[j]>>11)-int(reference[j]>>11))<=1);
 assert(std::abs(int((out[j]>>5)&63)-int((reference[j]>>5)&63))<=1);
 assert(std::abs(int(out[j]&31)-int(reference[j]&31))<=1);}}
std::fill(atlas.begin(),atlas.begin()+l.count,0);
std::fill(atlas.begin()+l.count,atlas.begin()+2*l.count,0xFFFF);
int changed=0;for(float angle=0;angle<3.33f;angle+=.1f){atlas_frame(atlas.data(),out.data(),l,angle,true);if(out!=previous)++changed;previous=out;}
assert(changed>=30); // old angle atlas held a single picture throughout this motion.
// Opaque pixels never consume atlas memory; the static layer remains upright outside the disc.
std::vector<uint8_t> alpha(n*n,255);for(int y=4;y<n-4;y++)for(int x=4;x<n-4;x++)alpha[y*n+x]=16;
Layout masked;masked.make(n,alpha.data());assert(masked.count==size_t((n-8)*(n-8)));
std::vector<uint16_t> overlay(n*n,0x1234),tiled(masked.count),linear(masked.count);
for(float angle:{0.0f,17.3f,89.9f,90.0f,147.1f,179.9f}){
 for(int y=0;y<n;y+=4)prepare_rows(src.data(),n,tiled.data(),masked,y,std::min(n,y+4),angle,overlay.data(),alpha.data());
 linear_rows(src.data(),n,linear.data(),masked,angle,overlay.data(),alpha.data());assert(tiled==linear);
}
std::vector<uint16_t> fixed(n*n,0x1234),packed(masked.count*HALF,0xFFFF);
atlas_frame(packed.data(),fixed.data(),masked,270,false);assert(fixed[0]==0x1234&&fixed[(n/2)*n+n/2]==0xFFFF);
// DMA writes a packed prefix into the full output allocation. Expansion cannot overwrite unread input.
std::vector<uint16_t> layer(n*n,0x1234),expected(n*n,0x1234),buffer(n*n,0);
for(size_t i=0;i<masked.count;++i)buffer[i]=uint16_t(i*71);
for(const auto &span:masked.spans)for(int x=0;x<span.length;++x)expected[span.y*n+span.x+x]=buffer[span.packed+x];
expand_in_place(buffer.data(),masked,layer.data());assert(buffer==expected);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'main.cpp').write_text(cpp)
            subprocess.run(['c++','-std=c++17','-O2','-fsanitize=address,undefined','-I',str(ROOT),str(p/'main.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
