"""Arm damage must cover old/new pixels without merging through the record's opaque fast path."""
from pathlib import Path
import subprocess
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
class VinylDamageTest(unittest.TestCase):
    def test_subtraction_covers_every_dirty_pixel_without_overlap(self):
        source=r'''
#include "styles/glass-v2/vinyl_damage.h"
#include "styles/glass-v2/vinyl_line.h"
#include "styles/glass-v2/vinyl_motion.h"
#include <cassert>
#include <random>
#include <vector>
using namespace panel_vinyl;
bool contains(DamageRect a,int x,int y){return x>=a.x1&&x<=a.x2&&y>=a.y1&&y<=a.y2;}
int main(){
std::mt19937 rng(734);const DamageRect record{72,110,359,397};
for(int run=0;run<1000;++run){
 int x=int(rng()%480)-20,y=int(rng()%440)-20;
 DamageRect old{x,y,x+int(rng()%100),y+int(rng()%100)};
 x=int(rng()%480)-20;y=int(rng()%440)-20;
 DamageRect next{x,y,x+int(rng()%100),y+int(rng()%100)};
 const auto dirty=damage_union(old,next);std::vector<DamageRect> outside;
 outside_record(dirty,record,[&](DamageRect r){outside.push_back(r);});assert(outside.size()<=4);
 for(const auto &r:outside)assert(r.x2<record.x1||r.x1>record.x2||r.y2<record.y1||r.y1>record.y2);
 for(int py=dirty.y1;py<=dirty.y2;++py)for(int px=dirty.x1;px<=dirty.x2;++px){
   int covers=0;for(const auto &r:outside)covers+=contains(r,px,py);
   assert(covers==(contains(record,px,py)?0:1)); // No lost pixels, repeated blending or overlap into the disc.
 }
}
std::array<std::vector<uint8_t>,5> buffers;
std::array<LineMask,5> masks;const size_t capacity[5]={ARM_MASK_CAPACITY[0],ARM_MASK_CAPACITY[2],ARM_MASK_CAPACITY[3],ARM_MASK_CAPACITY[4],ARM_MASK_CAPACITY[5]};
for(int i=0;i<5;++i){buffers[i].resize(capacity[i]);masks[i].pixels=buffers[i].data();masks[i].capacity=capacity[i];}
auto pose=[&](float lift,float groove){const auto a=arm(216,254,144,lift,groove);
 assert(masks[0].paint(int(a.pivot_x),int(a.pivot_y),int(a.weight_x),int(a.weight_y),10));
 assert(masks[1].paint(int(a.pivot_x),int(a.pivot_y),int(a.end_x),int(a.end_y),8));
 assert(masks[2].paint(int(a.end_x),int(a.end_y),int(a.head_x),int(a.head_y),18));
 assert(masks[3].paint(int(a.end_x),int(a.end_y),int(a.head_x),int(a.head_y),14));
 assert(masks[4].paint(int(a.cart_x0),int(a.cart_y0),int(a.cart_x1),int(a.cart_y1),8));
};
for(int run=0;run<300;++run){
 ArmDamage damage;std::vector<uint8_t> expected(1024*600),covered(1024*600);
 auto mark=[&](){for(const auto &m:masks){damage.merge(m.coverage);
   for(int y=0;y<m.h;++y)for(int x=0;x<m.w;++x)if(m.pixels[y*m.w+x]>3&&!contains(record,m.x+x,m.y+y))
     expected[(m.y+y)*1024+m.x+x]=1;
 }};
 const float lift=(rng()%1000)/1000.0f,groove=(rng()%1000)/1000.0f;
 pose(lift,groove);mark();pose(std::min(1.0f,lift+1.0f/24),std::min(1.0f,groove+.002f));mark();
 auto compact=damage;int rectangles=0,pixels=0;
 damage.emit(record,[&](DamageRect r){++rectangles;
   for(int y=r.y1;y<=r.y2;++y)for(int x=r.x1;x<=r.x2;++x){
     assert(!contains(record,x,y));assert(!covered[y*1024+x]);covered[y*1024+x]=1;++pixels;
   }
 });
 assert(rectangles<24&&pixels<12000); // Leave room in LVGL's 32-area ring and avoid large SRM row flushes.
 for(size_t i=0;i<expected.size();++i)if(expected[i])assert(covered[i]);
 std::fill(covered.begin(),covered.end(),0);rectangles=pixels=0;
 compact.emit_compact(record,[&](DamageRect r){++rectangles;
   for(int y=r.y1;y<=r.y2;++y)for(int x=r.x1;x<=r.x2;++x){
     assert(!contains(record,x,y));assert(!covered[y*1024+x]);covered[y*1024+x]=1;++pixels;
   }
 });
 assert(rectangles==1&&pixels<=24000);
 for(size_t i=0;i<expected.size();++i)if(expected[i])assert(covered[i]);
}
// Unexpected dirty pixels on another side use the original safe sparse subtraction.
ArmDamage mixed;mixed.rows[5]=(uint64_t(1)<<2)|(uint64_t(1)<<30);int fallback=0;
mixed.emit_compact(record,[&](DamageRect r){++fallback;assert(r.x2<record.x1||r.x1>record.x2);});assert(fallback==2);
}
'''
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp);(p/'test.cpp').write_text(source)
            subprocess.run(['c++','-std=c++17','-O2','-fsanitize=address,undefined','-I',str(ROOT),str(p/'test.cpp'),'-o',str(p/'test')],check=True)
            subprocess.run([str(p/'test')],check=True)
