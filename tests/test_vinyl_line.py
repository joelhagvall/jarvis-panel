"""The real arm's full motion fits its bounded A8 caches; coverage remains rounded and antialiased."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]

class VinylLineTest(unittest.TestCase):
    def test_all_arm_positions_caps_coverage_and_refusal(self):
        source=r'''
#include "styles/glass-v2/vinyl_line.h"
#include "styles/glass-v2/vinyl_motion.h"
#include <cassert>
#include <vector>
using namespace panel_vinyl;
int main(){
std::vector<uint8_t> pixels(16384);LineMask m;m.pixels=pixels.data();m.capacity=pixels.size();
assert(m.paint(0,0,20,0,8));auto at=[&](int x,int y){return pixels[(y-m.y)*m.w+x-m.x];};
assert(at(0,0)==255&&at(20,0)==255&&at(10,0)==255);
assert(at(-4,0)>0&&at(-4,0)<255&&at(-5,0)==0);assert(at(10,-5)==0&&at(10,5)==0);
const auto saved=pixels;assert(m.paint(0,0,20,0,8)&&pixels==saved);
m.capacity=1;assert(!m.paint(0,0,200,200,8)&&pixels==saved);m.capacity=pixels.size();
assert(m.paint(0,0,0,0,8));assert(pixels[(0-m.y)*m.w-m.x]==255); // Degenerate stroke is a round cap.
for(int lift=0;lift<=100;++lift)for(int groove=0;groove<=100;++groove){
 const auto a=arm(216,254,144,lift/100.0f,groove/100.0f);
 auto stroke=[&](float x0,float y0,float x1,float y1,float width,size_t cap){m.capacity=cap;
   assert(m.paint(int(x0),int(y0),int(x1),int(y1),width));assert(size_t(m.w)*m.h<=cap);
   assert(pixels[(int(y0)-m.y)*m.w+int(x0)-m.x]==255);
   const int crop=std::clamp(360-m.x,0,m.w);
   if(crop<m.w)assert(size_t(m.w)*m.h<=m.capacity-crop); // LVGL's real draw-buffer stride contract.
   if(lift%10==0&&groove%10==0)for(int y=0;y<m.h;++y)for(int x=0;x<m.w;++x){
     const float px=m.x+x-int(x0),py=m.y+y-int(y0),dx=int(x1)-int(x0),dy=int(y1)-int(y0);
     const float den=dx*dx+dy*dy,inv=den>0?1/den:0;
     const float t=std::clamp((px*dx+py*dy)*inv,0.0f,1.0f);
     const float a=px-t*dx,b=py-t*dy;
     const auto reference=uint8_t(std::clamp(width*.5f+.5f-std::sqrt(a*a+b*b),0.0f,1.0f)*255+.5f);
     assert(pixels[y*m.w+x]==reference);
   }
   // Cropping the A8 widgets at x=360 must leave every outside pixel visible, throughout real motion.
   for(int y=0;y<m.h;++y)for(int x=0;x<m.w;++x)if(pixels[y*m.w+x]&&m.x+x<360)
     assert(m.y+y>=110&&m.y+y<=397);
   for(int y=0;y<m.h;++y)for(int x=0;x<m.w;++x)if(pixels[y*m.w+x]&&m.x+x>=360)
     assert(m.x+x<424&&m.y+y>=100&&m.y+y<336); // Entire outside arm fits the opaque strip.
 };
 stroke(a.pivot_x,a.pivot_y,a.weight_x,a.weight_y,10,ARM_MASK_CAPACITY[0]);
 stroke(a.pivot_x,a.pivot_y,a.end_x,a.end_y,8,ARM_MASK_CAPACITY[2]);
 stroke(a.end_x,a.end_y,a.head_x,a.head_y,18,ARM_MASK_CAPACITY[3]);
 stroke(a.end_x,a.end_y,a.head_x,a.head_y,14,ARM_MASK_CAPACITY[4]);
 stroke(a.cart_x0,a.cart_y0,a.cart_x1,a.cart_y1,8,ARM_MASK_CAPACITY[5]);
}
// Precomposition has the same stacking and RGB565 alpha arithmetic as LVGL, and never writes outside record.
std::array<LineMask,6> masks;std::array<std::vector<uint8_t>,6> storage;
for(int i:{0,2,3,4,5}){storage[i].resize(64);masks[i].pixels=storage[i].data();masks[i].capacity=64;
 masks[i].x=8+i;masks[i].y=9;masks[i].w=8;masks[i].h=8;
 for(int p=0;p<64;++p)storage[i][p]=(p*37+i*11)%256;}
std::vector<uint16_t> actual(18*18+2,0x5AA5);auto expected=actual;
composite_arm(actual.data()+1,18,10,10,masks);
for(int y=0;y<18;++y)for(int x=0;x<18;++x)for(int i:{0,2,3,4,5}){
 const auto &m=masks[i];int mx=x+10-m.x,my=y+10-m.y;if(mx<0||my<0||mx>=m.w||my>=m.h)continue;
 auto &out=expected[1+y*18+x];uint8_t alpha=m.pixels[my*m.w+mx];
 if(alpha==255)out=ARM_COLORS[i];else if(alpha){unsigned w=(unsigned(alpha)+4)>>3;
   const unsigned a=spread565(ARM_COLORS[i]),b=spread565(out);out=pack565(((((a-b)*w)>>5)+b)&0x07E0F81Fu);}
}
assert(actual==expected);
// The surrounding pixels modified by an old arm are restored, including empty rows and gaps between spans.
std::vector<uint8_t> alpha(18*18,255);alpha[30]=0;alpha[31]=0;alpha[170]=0;
Layout layout;layout.make(18,alpha.data());std::vector<uint16_t> layer(18*18,0x1234),buffer(18*18,0xFFFF);
restore_static(buffer.data(),layout,layer.data());
for(int p=0;p<18*18;++p)assert(buffer[p]==((alpha[p]<252||alpha[18*18-1-p]<252)?0xFFFF:0x1234));
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'test.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-O2','-fsanitize=address,undefined','-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
